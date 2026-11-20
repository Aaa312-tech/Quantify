"""Rebuild historical eligibility from reviewed, dated issuer notices."""

from copy import deepcopy
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .dataset import check_integrity, load_snapshot, validate_features
from .market import resumption_reference
from .preprocessing import build_dataset, compare_history
from .snapshots import label_changes, rebuild_labels
from .storage import array_sha, dump_gzip, load_gzip, now, read, save, sha

AVAILABILITY_BASIS = "CALENDAR_PUBLICATION_DATE_NEXT_MIDNIGHT"


def reviewed_meta(meta, facts):
    """Attach bounded warning intervals without changing names or industry membership."""
    result = deepcopy(meta)
    if not facts:
        raise ValueError("Status repair needs reviewed intervals")
    for fact in facts:
        symbol = fact["symbol"]
        if symbol not in result or result[symbol]["kind"] != "stock":
            raise ValueError("Reviewed warning must identify a stock in the source universe")
        if fact["availability_basis"] != AVAILABILITY_BASIS:
            raise ValueError("Unregistered notice availability convention")
        if fact["start_scope"] not in {
            "ANNOUNCED_EFFECTIVE_DATE",
            "PRE_SAMPLE_CONTINUING_STATE_CONFIRMATION",
        }:
            raise ValueError("Unknown warning evidence scope")
        if len(fact["sources"]) != 2:
            raise ValueError("Warning requires both start and removal evidence")
        for boundary, source in zip(("start", "end"), fact["sources"]):
            session = fact[boundary]
            if date.fromisoformat(session).isoformat() != session:
                raise ValueError("Warning boundaries must be ISO calendar dates")
            published = source["notice_date"]
            if date.fromisoformat(published).isoformat() != published:
                raise ValueError("Notice date must be an ISO calendar date")
            expected = pd.Timestamp(published, tz="Asia/Shanghai") + pd.Timedelta(days=1)
            available = pd.Timestamp(fact[f"{boundary}_available_at"])
            opening = pd.Timestamp(session, tz="Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
            if (
                pd.isna(available)
                or available.tzinfo is None
                or available != expected
                or available > opening
            ):
                raise ValueError("Notice availability must follow publication and precede opening")
        limit = fact["limit"]
        if fact["start"] >= fact["end"] or not np.isfinite(limit) or not 0 < limit < 1:
            raise ValueError("Invalid warning interval or sourced daily limit")
        periods = result[symbol].setdefault("risk_warning_periods", [])
        for prior in periods:
            if fact["start"] < prior["end"] and prior["start"] < fact["end"]:
                raise ValueError("Reviewed warning intervals overlap")
        periods.append({key: deepcopy(value) for key, value in fact.items() if key != "symbol"})
        periods.sort(key=lambda item: item["start"])
    return result


def _checked_path(paths, relative, expected):
    path = (paths.root / relative).resolve()
    if not path.is_relative_to(paths.root) or sha(path) != expected:
        raise ValueError(f"Registered status-repair input changed: {relative}")
    return path


def _review_inputs(paths, protocol_path):
    protocol = read(protocol_path)
    source = _checked_path(paths, protocol["source_bundle"], protocol["source_bundle_sha256"])
    review_path = _checked_path(paths, protocol["review"], protocol["review_sha256"])
    review = read(review_path)
    if (
        review["status"] != "ISSUER_NOTICES_REVIEWED"
        or review["data_snapshot_sha256"] != protocol["source_bundle_sha256"]
    ):
        raise ValueError("Status review belongs to another snapshot or is not reviewed")
    for fact in review["facts"]:
        for evidence in fact["sources"]:
            _checked_path(paths, evidence["path"], evidence["sha256"])
    provider = read(
        _checked_path(paths, protocol["provider_audit"], protocol["provider_audit_sha256"])
    )
    records = load_gzip(
        _checked_path(paths, protocol["provider_records"], protocol["provider_records_sha256"])
    )
    if provider["source_bundle_sha256"] != protocol["source_bundle_sha256"]:
        raise ValueError("Provider screen belongs to another snapshot")
    return protocol, source, review, provider, records


def compare_provider(bundle, facts, provider, records):
    """Reconcile every positive vendor session with notices; do not certify negative flags."""
    calendar = bundle["calendar"]
    positives = {r["symbol"]: r for r in records if r["st_sessions"]}
    if set(positives) != {fact["symbol"] for fact in facts}:
        raise ValueError("Notice review does not cover every screened positive security")
    comparisons = []
    for symbol, record in sorted(positives.items()):
        sourced = np.zeros(len(calendar), dtype=bool)
        observed = np.zeros(len(calendar), dtype=bool)
        for fact in facts:
            if fact["symbol"] == symbol:
                sourced |= (calendar >= fact["start"]) & (calendar < fact["end"])
        for period in record["st_periods"]:
            mask = (calendar >= period["first"]) & (calendar <= period["last"])
            if int(mask.sum()) != period["sessions"]:
                raise ValueError("Provider interval differs from the frozen calendar")
            observed |= mask
        eligible = bundle["panels"]["eligible"][symbol].to_numpy(dtype=bool)
        eligible_dates = [str(d.date()) for d in calendar[sourced & eligible]]
        if (
            not np.array_equal(sourced, observed)
            or int(sourced.sum()) != record["st_sessions"]
            or eligible_dates != record["st_eligible_dates"]
            or len(eligible_dates) != record["st_eligible_sessions"]
        ):
            raise ValueError(f"Notice interval disagrees with the provider screen: {symbol}")
        comparisons.append(
            {
                "symbol": symbol,
                "warning_sessions": int(sourced.sum()),
                "previously_eligible": len(eligible_dates),
            }
        )
    if (
        len(comparisons) != provider["st_securities"]
        or sum(r["warning_sessions"] for r in comparisons) != provider["st_sessions"]
        or sum(r["previously_eligible"] for r in comparisons) != provider["st_eligible_sessions"]
    ):
        raise ValueError("Provider summary disagrees with its security records")
    return comparisons


def repair_status_snapshot(paths, output, protocol):
    """Create a new snapshot; activation still requires retraining and account validation."""
    target = (paths.root / output).resolve()
    parent = (paths.root / "data/processed").resolve()
    if (
        not parent.is_relative_to(paths.root)
        or not target.is_relative_to(parent)
        or target == parent
    ):
        raise ValueError("Snapshot must be a named directory under data/processed")
    if target.exists():
        raise FileExistsError(f"Snapshot already exists: {target}")
    protocol_path = (paths.root / protocol).resolve()
    if not protocol_path.is_relative_to((paths.root / "configs/protocols").resolve()):
        raise ValueError("Status repair protocol must stay under configs/protocols")
    integrity = check_integrity(paths)
    protocol, source, review, provider, records = _review_inputs(paths, protocol_path)
    if target != (paths.root / protocol["snapshot_output"]).resolve():
        raise ValueError("Output differs from the registered status-repair snapshot")
    original = load_snapshot(paths, source, protocol["source_data_cutoff"])
    meta = reviewed_meta(original["meta"], review["facts"])
    comparisons = compare_provider(original, review["facts"], provider, records)
    provenance = {
        "registered_at": now(),
        "protocol_sha256": sha(protocol_path),
        "review_sha256": protocol["review_sha256"],
        "source_bundle_sha256": sha(source),
        "source_sha256": {
            file.name: sha(file) for file in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    save(target / "provenance.json", provenance, immutable=True)
    # Rebuilding the untouched parent first separates builder drift from the intended repair.
    rebuilt = build_dataset(
        original["meta"],
        original["raws"],
        original["actions"],
        quarantined=protocol["quarantined"],
        opening_status=original.get("opening_status"),
    )
    parent_check = compare_history(original, rebuilt)
    del rebuilt
    repaired = build_dataset(
        meta,
        original["raws"],
        original["actions"],
        quarantined=protocol["quarantined"],
        opening_status=original.get("opening_status"),
    )
    pd.testing.assert_index_equal(original["calendar"], repaired["calendar"], exact=True)
    pd.testing.assert_frame_equal(
        original["table"][["date_index", "symbol"]],
        repaired["table"][["date_index", "symbol"]],
        check_exact=True,
    )
    for symbol, raw in original["raws"].items():
        pd.testing.assert_frame_equal(raw, repaired["raws"][symbol], check_exact=True)
    for name in original["panels"].keys() - {"eligible"}:
        pd.testing.assert_frame_equal(
            original["panels"][name], repaired["panels"][name], check_exact=True
        )
    if original["actions"] != repaired["actions"] or original["features"] != repaired["features"]:
        raise ValueError("Status repair changed actions or the model feature contract")
    expected = original["panels"]["eligible"].copy()
    for fact in review["facts"]:
        dates = (expected.index >= fact["start"]) & (expected.index < fact["end"])
        expected.loc[dates, fact["symbol"]] = False
    pd.testing.assert_frame_equal(expected, repaired["panels"]["eligible"], check_exact=True)
    features = validate_features(repaired)
    label_summary, label_records = label_changes(original["table"], repaired["table"])
    changed_features = {}
    for name in repaired["features"]:
        before, after = original["table"][name], repaired["table"][name]
        changed_features[name] = int((~(before.eq(after) | (before.isna() & after.isna()))).sum())
    repaired["provenance"] = {
        "schema_version": 1,
        "purpose": "reviewed_historical_risk_warnings",
        "source_bundle_sha256": provenance["source_bundle_sha256"],
        "review_sha256": protocol["review_sha256"],
        "availability_basis": AVAILABILITY_BASIS,
        "negative_flags_certified": False,
    }
    dump_gzip(target / "label_changes.json.gz", label_records)
    joblib.dump(repaired, target / "bundle.joblib", compress=3)
    if sha(source) != provenance["source_bundle_sha256"]:
        raise ValueError("Source snapshot changed during repair")
    audit = {
        "status": "PASS",
        "completed_at": now(),
        "source_integrity": integrity,
        "unchanged_parent_rebuild": parent_check,
        "data_cutoff": repaired["cutoff"],
        "bundle_sha256": sha(target / "bundle.joblib"),
        "prices_actions_calendar_row_order_unchanged": True,
        "metadata_change": "Only the reviewed risk_warning_periods are added",
        "provider_comparison": comparisons,
        "removed_eligible_rows": sum(r["previously_eligible"] for r in comparisons),
        "added_eligible_rows": 0,
        "features": features,
        "changed_feature_values": changed_features,
        "feature_values_sha256": array_sha(repaired["table"][repaired["features"]].to_numpy()),
        "labels": label_summary,
        "label_changes_sha256": sha(target / "label_changes.json.gz"),
        "limitations": protocol["limitations"],
        "decision": "RETRAINING_REQUIRED",
    }
    save(target / "audit.json", audit, immutable=True)
    from .planning import register_output

    register_output(paths, target)
    return audit


def repair_resumption_snapshot(paths, output, protocol):
    """Apply source-bound opening statuses and rebuild only that security's labels."""
    target = (paths.root / output).resolve()
    parent = (paths.root / "data/processed").resolve()
    protocol_path = (paths.root / protocol).resolve()
    if (
        not target.is_relative_to(parent)
        or target == parent
        or not parent.is_relative_to(paths.root)
    ):
        raise ValueError("Snapshot must be a named directory under data/processed")
    if target.exists():
        raise FileExistsError(f"Snapshot already exists: {target}")
    if not protocol_path.is_relative_to((paths.root / "configs/protocols").resolve()):
        raise ValueError("Resumption protocol must stay under configs/protocols")
    integrity = check_integrity(paths)
    specification = read(protocol_path)
    source = _checked_path(
        paths, specification["source_bundle"], specification["source_bundle_sha256"]
    )
    review_file = _checked_path(paths, specification["review"], specification["review_sha256"])
    review = read(review_file)
    if (
        target != (paths.root / specification["snapshot_output"]).resolve()
        or review["data_snapshot_sha256"] != sha(source)
        or review["status"] != "PUBLISHER_TIMING_AND_ISSUER_FACTS_REVIEWED"
    ):
        raise ValueError("Review, output or snapshot differs from registration")
    bounds = []
    for publication in review["publications"]:
        notice = publication["issuer_source"]
        _checked_path(paths, notice["local_pdf"], notice["sha256"])
        displayed = pd.Timestamp(publication["publisher_displayed_at"], tz=publication["timezone"])
        bound = pd.Timestamp(publication["public_no_later_than"])
        if bound != displayed + pd.Timedelta(minutes=1):
            raise ValueError("Availability must follow the publisher's displayed minute")
        bounds.append(bound)
    original = load_snapshot(paths, source, specification["source_data_cutoff"])
    symbol = review["symbol"]
    raw, calendar = original["raws"][symbol], original["calendar"]
    actions = [a for a in original["actions"] if a["symbol"] == symbol]
    statuses = review["opening_status"]
    resumptions = 0
    for day, status in statuses.items():
        session = pd.Timestamp(day)
        deadline = session.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
        known = pd.Timestamp(status["known_at"])
        if (
            session not in calendar
            or known != bounds[status["publication_index"]]
            or known > deadline
        ):
            raise ValueError("Status lacks timely source evidence for an observed session")
        if not isinstance(status["suspended"], bool) or status["suspended"] == (
            session in raw.index
        ):
            raise ValueError("Reviewed status contradicts observed quotes")
        if not status["suspended"]:
            previous = calendar[calendar.get_loc(session) - 1]
            if resumption_reference(raw, session, previous, calendar, status, actions) is None:
                raise ValueError("Resumption reference has an incomplete or unsupported gap")
            for item in status["suspension_evidence"]:
                halt = statuses.get(item["date"], {})
                if halt.get("suspended") is not True or halt.get("known_at") != item["known_at"]:
                    raise ValueError("Resumption evidence disagrees with the reviewed daily status")
            resumptions += 1
    if resumptions != 1:
        raise ValueError("Review must identify exactly one complete suspension and resumption")
    parent_labels = rebuild_labels(original, {symbol})
    pd.testing.assert_frame_equal(original["table"], parent_labels, check_exact=True)
    repaired = deepcopy(original)
    for day, status in statuses.items():
        daily = repaired.setdefault("opening_status", {}).setdefault(day, {})
        if symbol in daily:
            raise ValueError("Review would overwrite an existing opening status")
        daily[symbol] = {**deepcopy(status), "review_sha256": sha(review_file)}
    repaired["table"] = rebuild_labels(repaired, {symbol})
    unchanged = compare_history(original, repaired, labels=False)
    label_summary, changes = label_changes(original["table"], repaired["table"])
    if (
        any(row["symbol"] != symbol for row in changes)
        or original["actions"] != repaired["actions"]
    ):
        raise ValueError("Correction changed an unrelated security or corporate action")
    provenance = {
        "created_at": now(),
        "purpose": "reviewed_resumption_reference",
        "source_bundle_sha256": sha(source),
        "review_sha256": sha(review_file),
        "protocol_sha256": sha(protocol_path),
        "source_sha256": {
            file.name: sha(file) for file in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    repaired["provenance"] = provenance
    save(target / "provenance.json", provenance, immutable=True)
    dump_gzip(target / "label_changes.json.gz", changes)
    joblib.dump(repaired, target / "bundle.joblib", compress=3)
    audit = {
        "status": "PASS",
        "completed_at": now(),
        "source_integrity": integrity,
        "bundle_sha256": sha(target / "bundle.joblib"),
        "data_cutoff": repaired["cutoff"],
        "non_label_history": unchanged,
        "labels": label_summary,
        "label_changes_sha256": sha(target / "label_changes.json.gz"),
        "suspension_sessions": len(statuses) - resumptions,
        "resumptions": resumptions,
        "quality_flags_cleared": 0,
        "liquidity_or_price_bars_filled": 0,
        "decision": "RETRAINING_REQUIRED",
        "limitations": specification["limitations"],
    }
    save(target / "audit.json", audit, immutable=True)
    from .planning import register_output

    register_output(paths, target)
    return audit
