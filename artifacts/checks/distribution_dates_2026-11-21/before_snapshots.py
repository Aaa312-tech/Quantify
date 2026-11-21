"""Build immutable, source-checked repairs of registered research snapshots."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .dataset import check_integrity, load_snapshot, validate_features
from .market import LABEL_STATUS, legal_label
from .storage import array_sha, digest, dump_gzip, log, now, read, save, sha

HORIZONS = (1, 3, 5)
LABEL_FIELDS = ("y", "end", "status", "delay")


def next_opening(calendar, payment_date):
    """Return the first observed session strictly after payment, without inventing dates."""
    if not payment_date:
        return None
    index = calendar.searchsorted(pd.Timestamp(payment_date), side="right")
    return str(calendar[index].date()) if index < len(calendar) else None


def repair_actions(actions, calendar, facts, declared_payment_field):
    """Preserve entitlements; separate sourced payment dates from cash availability."""
    repaired = deepcopy(actions)
    normalized = 0
    for action in repaired:
        if (
            action.get(declared_payment_field)
            and action.get("cash_pay_date") != action[declared_payment_field]
        ):
            action["cash_pay_date"] = action[declared_payment_field]
            normalized += 1
    for fact in facts:
        matches = [
            a for a in repaired if (a["symbol"], a["ex_date"]) == (fact["symbol"], fact["ex_date"])
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected one reviewed corporate action: {fact['symbol']}")
        action = matches[0]
        for key in ("cash_per_share", "share_multiplier"):
            if not np.isclose(action[key], fact[key], rtol=0, atol=1e-12):
                raise ValueError(f"Reviewed entitlement differs: {fact['symbol']} {key}")
        if action.get("record_date") != fact["record_date"]:
            raise ValueError(f"Reviewed record date differs: {fact['symbol']}")
        payment_date = fact["cash_pay_date"]
        if fact["cash_per_share"] > 0 and not payment_date:
            raise ValueError("Reviewed cash dividend requires a payment date")
        if fact["cash_per_share"] == 0 and payment_date is not None:
            raise ValueError("Non-cash action cannot have a payment date")
        if payment_date and payment_date < fact["ex_date"]:
            raise ValueError("Payment precedes the entitlement event")
        for key in (
            "cash_pay_date",
            "share_list_date",
            "ex_reference_cash_per_share",
            "ex_reference_share_multiplier",
        ):
            if key in fact:
                action[key] = fact[key]
        action["date_status"] = "PRIMARY_FILING_REVIEWED"
        action["verified_source"] = fact["source"]["source"]
        action["source_evidence"] = {
            "path": fact["source"]["local_pdf"],
            "sha256": fact["source"]["sha256"],
            "pages": fact["evidence_pages"],
        }
        if (
            next_opening(calendar, action["cash_pay_date"])
            != fact["simulation_cash_available_date"]
        ):
            raise ValueError(f"Reviewed availability differs from calendar: {fact['symbol']}")
    for action in repaired:
        action["cash_available_date"] = next_opening(calendar, action.get("cash_pay_date"))
    return repaired, normalized


def rebuild_labels(bundle, symbols=None):
    """Recompute legal entry/exit outcomes, preserving table order and stored dtypes."""
    table = bundle["table"].copy(deep=True)
    columns = {f"{field}{h}" for h in HORIZONS for field in LABEL_FIELDS}
    arrays = {name: table[name].to_numpy(copy=True) for name in columns}
    actions = {}
    for action in bundle["actions"]:
        actions.setdefault(action["symbol"], []).append(action)
    groups = table.groupby("symbol", sort=True, observed=True).indices
    for count, (symbol, positions) in enumerate(groups.items(), 1):
        if symbols is not None and symbol not in symbols:
            continue
        for position in positions:
            row = table.iloc[position]
            for horizon in HORIZONS:
                label = (
                    legal_label(
                        bundle["meta"][symbol],
                        bundle["raws"][symbol],
                        bundle["calendar"],
                        int(row.date_index),
                        horizon,
                        actions.get(symbol, []),
                        opening_status=bundle.get("opening_status"),
                    )
                    if row.eligible
                    else (np.nan, -1, LABEL_STATUS["INELIGIBLE"], 0)
                )
                for field, value in zip(LABEL_FIELDS, label):
                    arrays[f"{field}{horizon}"][position] = value
        if symbols is None and count % 50 == 0:
            log("labels rebuilt", securities=count, total=len(groups))
    for name, values in arrays.items():
        table[name] = values
    return table


def label_changes(before, after):
    """Describe changed values, legal end dates and status separately for each horizon."""
    records = []
    summary = {}
    for horizon in HORIZONS:
        names = [f"{field}{horizon}" for field in LABEL_FIELDS]
        old, new = before[names], after[names]
        equal = old.eq(new) | (old.isna() & new.isna())
        positions = np.flatnonzero(~equal.all(axis=1).to_numpy())
        transitions = Counter()
        for position in positions:
            row = before.iloc[position]
            transitions[f"{int(old.iloc[position, 2])}->{int(new.iloc[position, 2])}"] += 1
            records.append(
                {
                    "symbol": row.symbol,
                    "date_index": int(row.date_index),
                    "horizon": horizon,
                    "before": [
                        None if pd.isna(value) else value for value in old.iloc[position].tolist()
                    ],
                    "after": [
                        None if pd.isna(value) else value for value in new.iloc[position].tolist()
                    ],
                }
            )
        summary[str(horizon)] = {
            "changed_rows": len(positions),
            "changed_fields": {
                field: int((~equal[f"{field}{horizon}"]).sum()) for field in LABEL_FIELDS
            },
            "status_transitions": dict(transitions),
        }
    return summary, records


def repair_snapshot(paths, output, protocol="configs/protocols/action_repair.json"):
    """Create and register a new snapshot; never activate it or overwrite old inputs."""
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
    integrity = check_integrity(paths)
    protocol_path = (paths.root / protocol).resolve()
    if not protocol_path.is_relative_to((paths.root / "configs/protocols").resolve()):
        raise ValueError("Repair protocol must stay under configs/protocols")
    protocol = read(protocol_path)
    if protocol.get("name") == "gap_precision":
        return repair_gap_snapshot(paths, target, protocol_path, integrity)
    if protocol.get("name") == "distribution_repair":
        return repair_distribution_snapshot(paths, target, protocol_path, integrity)
    review_path = (paths.root / protocol["review"]).resolve()
    source_path = (paths.root / protocol["source_bundle"]).resolve()
    if not review_path.is_relative_to(paths.root) or not source_path.is_relative_to(paths.root):
        raise ValueError("Repair inputs must stay inside the project")
    review = read(review_path)
    if protocol.get("review_sha256", sha(review_path)) != sha(review_path):
        raise ValueError("Review differs from the registered protocol")
    source_hash = sha(source_path)
    if (
        source_hash != protocol["source_bundle_sha256"]
        or source_hash != review["data_snapshot_sha256"]
    ):
        raise ValueError("Review or protocol belongs to another source snapshot")
    for fact in review["facts"]:
        source = fact["source"]
        pdf = (paths.root / source["local_pdf"]).resolve()
        if not pdf.is_relative_to(paths.root) or sha(pdf) != source["sha256"]:
            raise ValueError("Reviewed primary-source document hash mismatch")
    provenance = {
        "registered_at": now(),
        "protocol_sha256": sha(protocol_path),
        "parent_protocol_sha256": sha(paths.root / protocol["parent_protocol"]),
        "review_sha256": sha(review_path),
        "source_bundle": source_path.relative_to(paths.root).as_posix(),
        "source_bundle_sha256": source_hash,
        "source_sha256": {
            file.name: sha(file) for file in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    save(target / "provenance.json", provenance, immutable=True)
    original = load_snapshot(
        paths, source_path, protocol.get("source_data_cutoff", paths.config["data_cutoff"])
    )
    causal_table = rebuild_labels(original)
    repaired = {
        key: original[key]
        for key in ("meta", "raws", "actions", "panels", "scores", "calendar", "features", "cutoff")
    }
    if "opening_status" in original:
        repaired["opening_status"] = original["opening_status"]
    if "fund_mode" in original:
        repaired["fund_mode"] = original["fund_mode"]
    repaired["table"] = causal_table
    repaired["actions"], normalized = repair_actions(
        original["actions"],
        original["calendar"],
        review["facts"],
        paths.archive["action_fields"]["declared_payment_date"],
    )
    repaired["table"] = rebuild_labels(repaired, {fact["symbol"] for fact in review["facts"]})
    label_columns = [f"{field}{h}" for h in HORIZONS for field in LABEL_FIELDS]
    pd.testing.assert_frame_equal(
        original["table"].drop(columns=label_columns), repaired["table"].drop(columns=label_columns)
    )
    feature_check = validate_features(repaired)
    causal_summary, causal_changes = label_changes(original["table"], causal_table)
    action_summary, action_changes = label_changes(causal_table, repaired["table"])
    dump_gzip(
        target / "label_changes.json.gz",
        {"causal_opening": causal_changes, "reviewed_actions": action_changes},
    )
    repaired["provenance"] = {
        "schema_version": 1,
        "purpose": "reviewed_actions",
        "source_bundle_sha256": source_hash,
        "review_sha256": provenance["review_sha256"],
        "cash_availability": "first snapshot session strictly after declared payment date",
        "unresolved_data_audit": "artifacts/checks/causal_execution_2026-11-20/data_audit.json",
    }
    joblib.dump(repaired, target / "bundle.joblib", compress=3)
    if sha(source_path) != source_hash:
        raise ValueError("Source snapshot changed during repair")
    audit = {
        "status": "PASS",
        "completed_at": now(),
        "data_cutoff": str(repaired["cutoff"]),
        "source_integrity": integrity,
        "bundle_sha256": sha(target / "bundle.joblib"),
        "rows": len(repaired["table"]),
        "features": feature_check,
        "feature_values_sha256": array_sha(repaired["table"][repaired["features"]].to_numpy()),
        "non_label_columns_exact": True,
        "prices_calendar_universe_unchanged": True,
        "reviewed_actions": len(review["facts"]),
        "legacy_shifted_dates_normalized": normalized,
        "known_payment_dates": sum(bool(a.get("cash_pay_date")) for a in repaired["actions"]),
        "known_cash_available_dates": sum(
            bool(a["cash_available_date"]) for a in repaired["actions"]
        ),
        "causal_opening_label_changes": causal_summary,
        "reviewed_action_label_changes": action_summary,
        "label_changes_sha256": sha(target / "label_changes.json.gz"),
        "limitations": [
            "Historical market and industry status coverage is unchanged",
            "Unexplained price gaps remain unresolved",
            "Fractional share allocation remains unverified",
            "This snapshot does not extend the market-data cutoff",
        ],
    }
    save(target / "audit.json", audit, immutable=True)
    manifest = read(paths.manifest)
    for file in sorted(target.iterdir()):
        manifest["files"][file.relative_to(paths.root).as_posix()] = sha(file)
    save(paths.manifest, manifest)
    return audit


def repair_gap_snapshot(paths, target, protocol_path, integrity):
    """Rebuild local opening gaps while requiring every other stored input to stay exact."""
    from .preprocessing import build_dataset, compare_history

    protocol = read(protocol_path)
    manifest = read(paths.manifest)
    source_path = (paths.root / protocol["source_bundle"]).resolve()
    checks = paths.artifact_path(protocol["checks_output"], "checks")
    parent_file = checks / "parent_rebuild.json"
    if (
        not source_path.is_relative_to(paths.root)
        or target != (paths.root / protocol["snapshot_output"]).resolve()
        or sha(source_path) != protocol["source_bundle_sha256"]
        or manifest["files"].get(protocol_path.relative_to(paths.root).as_posix())
        != sha(protocol_path)
        or manifest["files"].get(parent_file.relative_to(paths.root).as_posix()) != sha(parent_file)
    ):
        raise ValueError("Gap repair inputs differ from registration")
    parent = read(parent_file)
    if (
        parent["status"] != "PASS"
        or parent["data_sha256"] != sha(source_path)
        or parent["protocol_sha256"] != sha(protocol_path)
    ):
        raise ValueError("Parent reconstruction belongs to another input or protocol")
    source = {f.name: sha(f) for f in sorted(Path(__file__).parent.glob("*.py"))}
    original = load_snapshot(paths, source_path, protocol["source_data_cutoff"])
    repaired = build_dataset(
        original["meta"],
        original["raws"],
        original["actions"],
        opening_status=original.get("opening_status"),
    )
    if original["actions"] != repaired["actions"] or original.get("opening_status") != repaired.get(
        "opening_status"
    ):
        raise ValueError("Gap repair changed corporate actions or opening status")
    comparison = dict(repaired, table=repaired["table"].copy())
    changes, counts = [], {}
    for field in ("gap", "rank_gap"):
        old, new = original["table"][field], repaired["table"][field]
        pd.testing.assert_series_equal(old.isna(), new.isna(), check_exact=True)
        changed = ~(old.eq(new) | (old.isna() & new.isna()))
        counts[field] = int(changed.sum())
        rows = repaired["table"].loc[changed, ["date_index", "symbol"]].copy()
        rows["field"] = field
        rows["before"] = old.loc[changed]
        rows["after"] = new.loc[changed]
        changes.extend(rows.to_dict("records"))
        comparison["table"][field] = old
    unchanged = compare_history(original, comparison)
    feature_check = validate_features(repaired)
    provenance = {
        "purpose": "local_opening_gaps",
        "created_at": now(),
        "protocol_sha256": sha(protocol_path),
        "source_bundle_sha256": sha(source_path),
        "parent_rebuild_sha256": sha(parent_file),
        "source_sha256": source,
    }
    repaired["provenance"] = provenance
    save(target / "provenance.json", provenance, immutable=True)
    dump_gzip(target / "feature_changes.json.gz", changes)
    joblib.dump(repaired, target / "bundle.joblib", compress=3)
    if source != {f.name: sha(f) for f in sorted(Path(__file__).parent.glob("*.py"))}:
        raise ValueError("Implementation changed during snapshot construction")
    audit = {
        "status": "PASS",
        "completed_at": now(),
        "source_integrity": integrity,
        "bundle_sha256": sha(target / "bundle.joblib"),
        "data_cutoff": str(repaired["cutoff"]),
        "parent_exact_rebuild": parent,
        "unaffected_fields_exact": unchanged,
        "changed_rows": counts,
        "features": feature_check,
        "limitations": protocol["limitations"],
    }
    save(target / "audit.json", audit, immutable=True)
    if sha(source_path) != protocol["source_bundle_sha256"]:
        raise ValueError("Source bundle changed during construction")
    for file in sorted(target.iterdir()):
        manifest["files"][file.relative_to(paths.root).as_posix()] = sha(file)
    save(paths.manifest, manifest)
    return audit


def reviewed_distribution_actions(actions, calendar, facts):
    """Apply only individually verified fields; retain opaque event identities and source bytes."""
    from .distributions import FIELDS

    repaired = deepcopy(actions)
    index = {(a["symbol"], a["ex_date"]): a for a in repaired}
    seen, changes = set(), []
    for fact in facts:
        key = (fact["symbol"], fact["ex_date"])
        if key in seen or key not in index:
            raise ValueError("Duplicate or absent reviewed distribution")
        seen.add(key)
        action = index[key]
        if digest(action) != fact["original_action_sha256"]:
            raise ValueError("Reviewed action identity mismatch")
        fields = fact["verified_fields"]
        if not fields or not set(fields) <= FIELDS:
            raise ValueError("Unregistered distribution field")
        changed = {}
        for field, value in fields.items():
            previous = action.get(field)
            if field in ("cash_per_share", "ex_reference_cash_per_share"):
                number = Decimal(str(value))
                if not number.is_finite() or number < 0:
                    raise ValueError("Invalid cash entitlement")
                if previous is not None and abs(number - Decimal(str(previous))) <= Decimal(
                    "1e-12"
                ):
                    continue
                value = float(number)
            elif field == "cash_rounding":
                if value != "FLOOR_TO_CENT":
                    raise ValueError("Unregistered cash rounding")
            elif (
                value is None
                or str(pd.Timestamp(value).date()) != value
                or value < action["ex_date"]
            ):
                raise ValueError("Invalid reviewed availability date")
            if previous != value:
                action[field] = value
                changed[field] = {"before": previous, "after": value}
        if action.get("cash_pay_date") and action["cash_per_share"] <= 0:
            raise ValueError("Non-cash action has a payment date")
        action["cash_available_date"] = next_opening(calendar, action.get("cash_pay_date"))
        action["distribution_review"] = {
            "method": fact["method"],
            "fields": sorted(fields),
            "source": fact["source"],
            "pages": fact["evidence_pages"],
            "known_at": fact["known_at"],
        }
        if changed:
            changes.append({"symbol": key[0], "ex_date": key[1], "fields": changed})
    return repaired, changes


def repair_distribution_snapshot(paths, target, protocol_path, integrity):
    """Reconstruct the complete dataset after an entitlement change, without activation."""
    from . import distributions
    from .preprocessing import build_dataset, compare_history

    protocol, _ = distributions.registered_inputs(paths, protocol_path)
    if target != (paths.root / protocol["snapshot_output"]).resolve():
        raise ValueError("Snapshot output differs from registration")
    manifest = read(paths.manifest)
    review_path = distributions.checked_path(
        paths.root, protocol["review"], manifest["files"][protocol["review"]]
    )
    review = read(review_path)
    source_path = distributions.checked_path(
        paths.root, protocol["source_bundle"], protocol["source_bundle_sha256"]
    )
    if (
        review["protocol_sha256"] != sha(protocol_path)
        or review["data_snapshot_sha256"] != sha(source_path)
        or review["extractor_sha256"] != sha(Path(distributions.__file__))
        or review["source_inputs"] != protocol["source_inputs"]
    ):
        raise ValueError("Frozen source review binding differs")
    for fact in review["facts"]:
        distributions.checked_path(paths.root, fact["source"]["path"], fact["source"]["sha256"])
    original = load_snapshot(paths, source_path, protocol["source_data_cutoff"])
    if review["original_actions_sha256"] != digest(original["actions"]):
        raise ValueError("Source review does not bind the full action inventory")
    provenance = {
        "started_at": now(),
        "protocol_sha256": sha(protocol_path),
        "review_sha256": sha(review_path),
        "source_bundle_sha256": sha(source_path),
        "source_sha256": {f.name: sha(f) for f in sorted(Path(__file__).parent.glob("*.py"))},
    }
    save(target / "provenance.json", provenance, immutable=True)
    rebuild_args = {"opening_status": original.get("opening_status")}
    parent = build_dataset(original["meta"], original["raws"], original["actions"], **rebuild_args)
    parent_check = compare_history(original, parent)
    actions, changes = reviewed_distribution_actions(
        original["actions"], original["calendar"], review["facts"]
    )
    repaired = build_dataset(original["meta"], original["raws"], actions, **rebuild_args)
    if repaired["meta"] != original["meta"] or repaired.get("opening_status") != original.get(
        "opening_status"
    ):
        raise ValueError("Unregistered metadata or opening status change")
    pd.testing.assert_frame_equal(
        original["panels"]["eligible"], repaired["panels"]["eligible"], check_exact=True
    )
    for symbol, raw in original["raws"].items():
        pd.testing.assert_frame_equal(raw, repaired["raws"][symbol], check_exact=True)
    pd.testing.assert_index_equal(original["calendar"], repaired["calendar"], exact=True)
    entitlement_dates = [c["ex_date"] for c in changes if "cash_per_share" in c["fields"]]
    boundary = (
        original["calendar"].searchsorted(min(entitlement_dates))
        if entitlement_dates
        else len(original["calendar"])
    )
    columns = [
        c for c in original["table"] if c not in {f"{f}{h}" for f in LABEL_FIELDS for h in HORIZONS}
    ]
    prefix = original["table"].date_index < boundary
    pd.testing.assert_frame_equal(
        original["table"].loc[prefix, columns],
        repaired["table"].loc[prefix, columns],
        check_exact=True,
    )
    label_summary, labels = label_changes(original["table"], repaired["table"])
    field_changes = {}
    for section in ("panels", "scores"):
        field_changes[section] = {}
        for name, old in original[section].items():
            new = repaired[section][name]
            pd.testing.assert_frame_equal(
                old.iloc[:boundary], new.iloc[:boundary], check_exact=True
            )
            field_changes[section][name] = int(
                (~(old.eq(new) | (old.isna() & new.isna()))).to_numpy().sum()
            )
    field_changes["table"] = {}
    for name in original["table"]:
        old, new = original["table"][name], repaired["table"][name]
        field_changes["table"][name] = int((~(old.eq(new) | (old.isna() & new.isna()))).sum())
    features = validate_features(repaired)
    repaired["provenance"] = {
        **provenance,
        "purpose": "reviewed_distributions",
        "cash_availability": "first observed opening strictly after declared payment date",
    }
    dump_gzip(target / "label_changes.json.gz", labels)
    save(target / "action_changes.json", changes, immutable=True)
    save(target / "field_changes.json", field_changes, immutable=True)
    joblib.dump(repaired, target / "bundle.joblib", compress=3)
    if provenance["source_sha256"] != {
        f.name: sha(f) for f in sorted(Path(__file__).parent.glob("*.py"))
    }:
        raise ValueError("Implementation changed during snapshot construction")
    audit = {
        "status": "PASS",
        "completed_at": now(),
        "source_integrity": integrity,
        "bundle_sha256": sha(target / "bundle.joblib"),
        "parent_exact_rebuild": parent_check,
        "data_cutoff": str(repaired["cutoff"]),
        "review_sha256": sha(review_path),
        "reviewed_events": len(review["facts"]),
        "changed_events": len(changes),
        "non_label_prefix_boundary_index": int(boundary),
        "first_changed_entitlement": min(entitlement_dates) if entitlement_dates else None,
        "raw_calendar_metadata_eligibility_exact": True,
        "non_label_prefix_exact": True,
        "known_payment_dates": sum(bool(a.get("cash_pay_date")) for a in actions),
        "known_share_listing_dates": sum(bool(a.get("share_list_date")) for a in actions),
        "features": features,
        "label_changes": label_summary,
        "limitations": protocol["limitations"],
    }
    save(target / "audit.json", audit, immutable=True)
    if sha(source_path) != protocol["source_bundle_sha256"]:
        raise ValueError("Source bundle changed during construction")
    for file in sorted(target.iterdir()):
        manifest["files"][file.relative_to(paths.root).as_posix()] = sha(file)
    save(paths.manifest, manifest)
    return audit
