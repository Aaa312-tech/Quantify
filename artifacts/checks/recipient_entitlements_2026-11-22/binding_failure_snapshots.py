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


def date_supplement_facts(actions, basis_actions, facts, additions, binding):
    """Bind accepted missing dates to current events without replacing economic terms."""

    def key(row):
        return row["symbol"], row["ex_date"]

    current = {key(row): row for row in actions}
    basis = {key(row): row for row in basis_actions}
    reviewed = {key(row): row for row in facts}
    if (
        len(current) != len(actions)
        or len(basis) != len(basis_actions)
        or current.keys() != basis.keys()
    ):
        raise ValueError("Date supplement action inventory differs")
    if len(reviewed) != len(facts):
        raise ValueError("Duplicate reviewed event")
    economic = (
        "symbol",
        "code",
        "id",
        "record_date",
        "ex_date",
        "cash_per_share",
        "share_multiplier",
        "bonus_per_share",
        "capitalization_per_share",
        "ex_reference_cash_per_share",
        "ex_reference_share_multiplier",
        "cash_rounding",
    )
    seen, supplements = set(), []
    for addition in additions:
        identity = key(addition)
        if identity in seen or identity not in current or not addition["passed"]:
            raise ValueError("Duplicate, absent or unverified date supplement")
        seen.add(identity)
        fields = addition["added_fields"]
        if not fields:
            continue
        if not set(fields) <= {"cash_pay_date", "share_list_date"} or identity not in reviewed:
            raise ValueError("Unregistered date supplement field")
        fact, old, action = reviewed[identity], basis[identity], current[identity]
        if fact["status"] != "VERIFIED_FIELDS" or fact["original_action_sha256"] != digest(old):
            raise ValueError("Reviewed date basis identity differs")
        if any(action.get(field) != old.get(field) for field in economic):
            raise ValueError("Current economic identity differs from the reviewed event")
        missing = {}
        for field, value in fields.items():
            if fact["verified_fields"].get(field) != value:
                raise ValueError("Independent date differs from the accepted source")
            if not value or str(pd.Timestamp(value).date()) != value or value < action["ex_date"]:
                raise ValueError("Invalid supplementary availability date")
            if action.get(field) is not None and action[field] != value:
                raise ValueError("Existing availability date conflict")
            if action.get(field) is None:
                missing[field] = value
        if missing:
            supplements.append(
                {
                    **deepcopy(fact),
                    "original_action_sha256": digest(action),
                    "verified_fields": missing,
                    "date_basis": {
                        **binding,
                        "reviewed_action_sha256": digest(old),
                        "current_action_sha256": digest(action),
                    },
                }
            )
    return supplements


def reviewed_distribution_actions(actions, calendar, facts, *, supplement=False):
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
        if supplement and not set(fields) <= {"cash_pay_date", "share_list_date"}:
            raise ValueError("Date supplements cannot change entitlements")
        changed = {}
        for field, value in fields.items():
            previous = action.get(field)
            if supplement and previous is not None and previous != value:
                raise ValueError("Existing availability date conflict")
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
        if supplement and not changed:
            continue
        if not supplement or "cash_pay_date" in changed:
            action["cash_available_date"] = next_opening(calendar, action.get("cash_pay_date"))
        evidence = {
            "method": fact["method"],
            "fields": sorted(fields),
            "source": fact["source"],
            "pages": fact["evidence_pages"],
            "known_at": fact["known_at"],
        }
        if supplement:
            evidence["date_basis"] = fact["date_basis"]
            action.setdefault("distribution_date_reviews", []).append(evidence)
        else:
            action["distribution_review"] = evidence
        if changed:
            changes.append({"symbol": key[0], "ex_date": key[1], "fields": changed})
    return repaired, changes


def accepted_date_records(protocol, registered, acceptance, independent):
    """Bind independent date records to the frozen receipt for their review phase."""
    if (
        independent["status"] != "PASS"
        or independent["source_review_sha256"] != protocol["review_sha256"]
    ):
        raise ValueError("Independent date review binding differs")
    if registered.get("phase") == "source_candidate_roles":
        if (
            acceptance["status"] != "PASS_SOURCE_ONLY"
            or acceptance["protocol_sha256"] != protocol["review_protocol"]["sha256"]
            or acceptance["facts_applied"] is not False
            or independent["facts_applied"] is not False
            or independent["failed_roles"] != 0
            or independent["failed_events"] != 0
            or acceptance["prior_supported_events_preserved"]
            != independent["prior_events_preserved"]
            or acceptance["evidence_sha256"]["source_review.json"] != protocol["review_sha256"]
            or acceptance["evidence_sha256"]["independent_closure.json"]
            != protocol["independent_review"]["sha256"]
            or acceptance["active_state"]["bundle_sha256"] != protocol["source_bundle_sha256"]
            or acceptance["active_missing_payment_dates_supported"]
            != protocol["expected_changes"]["cash_pay_date"]
            or acceptance["added_payment_dates"] != protocol["verified_additions"]["cash_pay_date"]
        ):
            raise ValueError("Companion date acceptance binding differs")
        records = independent["fields"]
    elif registered.get("phase") in {"gross_cash_wording", "implementation_payout_scope"}:
        records = independent.get("records", [])
        preserved = independent.get("preserved_fields", [])
        field_checks = {"record_date", "ex_date", "cash_pay_date", "cash_per_share"}
        checks = field_checks | {"issuer", "title"}
        evidence = acceptance.get("evidence_sha256", {})
        if registered.get("phase") == "implementation_payout_scope":
            page_order = protocol.get("source_page_order", {}).get("sha256")
            if not page_order or acceptance.get("page_order_protocol_sha256") != page_order:
                raise ValueError("Implementation payment page-order binding differs")
            receipt = "independent_closure.json"
        else:
            receipt = "field_acceptance.json"
        if (
            acceptance.get("status") != "PASS_SOURCE_ONLY"
            or acceptance.get("protocol_sha256") != protocol["review_protocol"]["sha256"]
            or acceptance.get("facts_applied") is not False
            or independent.get("failed_events") != 0
            or independent.get("events") != len(records)
            or independent.get("field_checks") != len(records) * len(field_checks)
            or acceptance.get("independent_field_checks") != independent.get("field_checks")
            or acceptance.get("prior_supported_fields_preserved") != len(preserved)
            or any(len(field) != 4 for field in preserved)
            or len({tuple(field[:3]) for field in preserved}) != len(preserved)
            or acceptance.get("prior_supported_events_preserved")
            != len({tuple(field[:2]) for field in preserved})
            or evidence.get("source_review.json") != protocol["review_sha256"]
            or evidence.get(receipt) != protocol["independent_review"]["sha256"]
            or acceptance.get("active_state", {}).get("bundle_sha256")
            != protocol["source_bundle_sha256"]
            or acceptance.get("active_missing_payment_dates_supported")
            != protocol["expected_changes"]["cash_pay_date"]
            or acceptance.get("added_payment_dates")
            != protocol["verified_additions"]["cash_pay_date"]
            or acceptance.get("already_present_matching_dates")
            != protocol["verified_additions"]["cash_pay_date"]
            - protocol["expected_changes"]["cash_pay_date"]
            or len({(row["symbol"], row["ex_date"]) for row in records}) != len(records)
            or any(
                row.get("passed") is not True
                or set(row.get("checks", {})) != checks
                or any(check.get("passed") is not True for check in row["checks"].values())
                for row in records
            )
        ):
            raise ValueError("Declared payment acceptance binding differs")
    else:
        if (
            acceptance["status"] != "SOURCE_REVIEW_ACCEPTED_WITH_UNRESOLVED_EVENTS"
            or not acceptance["previous_facts_preserved"]
            or acceptance["source_review_sha256"] != protocol["review_sha256"]
            or acceptance["active_bundle_sha256"] != protocol["source_bundle_sha256"]
            or acceptance["independent_fields"]["sha256"]
            != protocol["independent_review"]["sha256"]
        ):
            raise ValueError("Accepted date review binding differs")
        records = independent["records"]
    allowed = set(protocol["supplement_fields"])
    if (
        not allowed
        or not allowed <= {"cash_pay_date", "share_list_date"}
        or any(not set(record["added_fields"]) <= allowed for record in records)
    ):
        raise ValueError("Independent date fields exceed the registered scope")
    counts = Counter(field for record in records for field in record["added_fields"])
    counts["events"] = sum(bool(record["added_fields"]) for record in records)
    if dict(counts) != protocol["verified_additions"]:
        raise ValueError("Verified date addition inventory differs")
    return records


def _accepted_date_supplements(paths, protocol, review, original, manifest):
    """Check the frozen source-review basis before rebinding its accepted date fields."""
    from . import distributions

    def frozen(reference):
        if manifest["files"].get(reference["path"]) != reference["sha256"]:
            raise ValueError("Date supplement reference is not frozen")
        return distributions.checked_path(paths.root, reference["path"], reference["sha256"])

    registered_path = frozen(protocol["review_protocol"])
    registered, _ = distributions.registered_inputs(paths, registered_path)
    basis_path = frozen(protocol["review_basis"])
    acceptance = read(frozen(protocol["source_acceptance"]))
    independent = read(frozen(protocol["independent_review"]))
    if (
        registered["source_bundle"] != protocol["review_basis"]["path"]
        or registered["source_bundle_sha256"] != sha(basis_path)
        or registered["source_data_cutoff"] != protocol["source_data_cutoff"]
        or registered["review"] != protocol["review"]
        or review["protocol_sha256"] != sha(registered_path)
        or review["data_snapshot_sha256"] != sha(basis_path)
        or review["source_inputs"] != registered["source_inputs"]
    ):
        raise ValueError("Accepted date review binding differs")
    additions = accepted_date_records(protocol, registered, acceptance, independent)
    if registered.get("phase") == "source_candidate_roles":
        closure = protocol["source_closure"]
        if set(closure) != {"amendment_closure.json", "visual_review.json"}:
            raise ValueError("Companion date source closure is incomplete")
        for name, reference in closure.items():
            if (
                acceptance["evidence_sha256"][name] != reference["sha256"]
                or read(frozen(reference))["status"] != "PASS"
            ):
                raise ValueError("Companion date source closure binding differs")
    elif registered.get("phase") in {"gross_cash_wording", "implementation_payout_scope"}:
        closure = protocol["source_closure"]
        if set(closure) != {"amendment_review.json", "visual_review.json"}:
            raise ValueError("Declared payment source closure inventory differs")
        for name, reference in closure.items():
            if (
                acceptance["evidence_sha256"].get(name) != reference["sha256"]
                or read(frozen(reference))["status"] != "PASS"
            ):
                raise ValueError("Declared payment source closure binding differs")
        if registered.get("phase") == "implementation_payout_scope":
            page_order = read(frozen(protocol["source_page_order"]))
            if (
                page_order.get("name") != "distribution_page_order"
                or page_order.get("phase") != "implementation_payout_scope"
                or page_order.get("parent_protocol") != protocol["review_protocol"]["path"]
                or page_order.get("parent_protocol_sha256") != protocol["review_protocol"]["sha256"]
            ):
                raise ValueError("Implementation payment page-order protocol differs")
    basis = load_snapshot(paths, basis_path, registered["source_data_cutoff"])
    if review["original_actions_sha256"] != digest(basis["actions"]):
        raise ValueError("Date review does not bind the original action inventory")
    identities = {(r["symbol"], r["ex_date"]): digest(r) for r in basis["actions"]}
    records = {(r["symbol"], r["ex_date"]): r["original_action_sha256"] for r in review["records"]}
    if records != identities or len(records) != len(review["records"]):
        raise ValueError("Date review event inventory differs")
    return date_supplement_facts(
        original["actions"],
        basis["actions"],
        review["facts"],
        additions,
        {
            "review_sha256": protocol["review_sha256"],
            "review_basis_sha256": sha(basis_path),
            "source_acceptance_sha256": protocol["source_acceptance"]["sha256"],
            "independent_review_sha256": protocol["independent_review"]["sha256"],
        },
    )


def entitlement_facts(actions, basis_actions, review, additions, decisions, binding):
    """Bind accepted recipient terms without replacing prior reviews or unrelated actions."""
    from .distributions import FIELDS

    def index(rows):
        indexed = {(row["symbol"], row["ex_date"]): row for row in rows}
        if len(indexed) != len(rows):
            raise ValueError("Duplicate entitlement event")
        return indexed

    current, basis = index(actions), index(basis_actions)
    records, facts = index(review["records"]), index(review["facts"])
    accepted, decided = index(additions), index(decisions)
    if (
        current.keys() != basis.keys()
        or records.keys() != basis.keys()
        or review["original_actions_sha256"] != digest(basis_actions)
        or any(records[key]["original_action_sha256"] != digest(row) for key, row in basis.items())
        or facts != {key: row for key, row in records.items() if row["status"] == "VERIFIED_FIELDS"}
        or not accepted
        or accepted.keys() != decided.keys()
        or not accepted.keys() <= facts.keys()
    ):
        raise ValueError("Entitlement source or accepted event inventory differs")
    result = []
    for key, acceptance in accepted.items():
        action, old, fact, decision = current[key], basis[key], facts[key], decided[key]
        fields = acceptance["verified_fields"]
        supported = {
            name: field["value"]
            for name, field in decision["field_decisions"].items()
            if field["status"] == "SUPPORTED_PRIMARY_FIELD"
        }
        if (
            digest(action) != digest(old)
            or acceptance["original_action_sha256"] != digest(old)
            or any(name in action for name in ("distribution_review", "distribution_date_reviews"))
        ):
            raise ValueError("Current entitlement identity or prior review differs")
        if (
            not fields
            or not set(fields) <= FIELDS - {"cash_rounding"}
            or fields != fact["verified_fields"]
            or fields != supported
            or set(decision["field_decisions"]) != FIELDS
            or fact["source"].get("field_review") != binding["field_decisions"]
            or acceptance["field_evidence"] != decision["evidence"]
            or acceptance["initial_failed_checks_retained"]
            != decision["initial_failed_checks_retained"]
            or acceptance["unresolved_primary_aggregate"]
            != decision["unresolved_primary_aggregate"]
        ):
            raise ValueError("Accepted entitlement fields or source decisions differ")
        control = decision["control_fields"]
        if (
            control["record_date"] != action.get("record_date")
            or control["ex_date"] != action["ex_date"]
            or Decimal(control["share_multiplier"]) != Decimal(str(action["share_multiplier"]))
        ):
            raise ValueError("Entitlement record date or share ratio differs")
        cash = Decimal(str(fields["cash_per_share"]))
        preserve_cash = abs(cash - Decimal(str(action["cash_per_share"]))) <= Decimal("1e-12")
        if acceptance["preserve_original_cash_binary64_at_integration"] is not preserve_cash:
            raise ValueError("Accepted cash storage decision differs")
        result.append(
            {
                **deepcopy(fact),
                "entitlement_basis": {
                    **deepcopy(binding),
                    "reviewed_action_sha256": digest(old),
                    "current_action_sha256": digest(action),
                    "independent_record_sha256": digest(acceptance),
                    "field_decision_sha256": digest(decision),
                },
            }
        )
    return result


def _accepted_entitlement_facts(paths, protocol, review, original, manifest):
    """Require the completed source phase, including its retained unresolved evidence."""
    from . import distributions

    def frozen(reference):
        if manifest["files"].get(reference["path"]) != reference["sha256"]:
            raise ValueError("Entitlement reference is not frozen")
        return distributions.checked_path(paths.root, reference["path"], reference["sha256"])

    registered_path = frozen(protocol["review_protocol"])
    registered, _ = distributions.registered_inputs(paths, registered_path)
    basis_path = frozen(protocol["review_basis"])
    acceptance = read(frozen(protocol["source_acceptance"]))
    independent = read(frozen(protocol["independent_review"]))
    decisions = read(frozen(protocol["field_decisions"]))
    context = read(frozen(protocol["source_context"]))
    previous = read(frozen(registered["previous_review"]))
    if (
        registered.get("phase") != "recipient_and_adjusted_entitlements"
        or registered["source_bundle"] != protocol["review_basis"]["path"]
        or registered["source_bundle_sha256"] != sha(basis_path)
        or registered["source_data_cutoff"] != protocol["source_data_cutoff"]
        or registered["review"] != protocol["review"]
        or registered["source_inputs"] != protocol["source_inputs"]
        or registered["field_decisions"] != protocol["field_decisions"]
        or review["protocol_sha256"] != sha(registered_path)
        or review["data_snapshot_sha256"] != sha(basis_path)
        or acceptance["status"] != "SOURCE_REVIEW_VALIDATED_LOCALLY"
        or independent["status"] != "FIELD_SOURCE_ACCEPTANCE_PASSED"
        or acceptance["facts_applied"] is not False
        or independent["facts_applied"] is not False
        or acceptance["source_review_sha256"] != protocol["review_sha256"]
        or independent["source_review_sha256"] != protocol["review_sha256"]
        or acceptance["production_protocol_sha256"] != sha(registered_path)
        or independent["protocol_sha256"] != sha(registered_path)
        or acceptance["field_acceptance_sha256"] != protocol["independent_review"]["sha256"]
        or independent["field_decisions_sha256"] != protocol["field_decisions"]["sha256"]
        or independent["previous_review_sha256"] != registered["previous_review"]["sha256"]
        or acceptance["active_state"]["bundle"]["sha256"] != protocol["source_bundle_sha256"]
        or decisions["context_completion"] != protocol["source_context"]
        or decisions["protocol"] != registered["evidence_protocol"]
        or acceptance["protocol_sha256"] != decisions["protocol"]["sha256"]
        or context["protocol_sha256"] != decisions["protocol"]["sha256"]
        or context["issuer_context"]["all_issuer_context_complete"] is not True
        or context["issuer_context_checks"]["context_check_failures"] != 0
        or set(protocol["entitlement_fields"]) != distributions.FIELDS - {"cash_rounding"}
    ):
        raise ValueError("Entitlement source acceptance binding differs")
    additions = independent["records"]
    counts = Counter(name for record in additions for name in record["verified_fields"])
    if (
        {**counts, "events": len(additions)} != protocol["verified_additions"]
        or dict(counts) != acceptance["new_supported_fields"]
        or dict(counts) != independent["new_fields"]
        or dict(counts) != decisions["supported_field_counts"]
        or sum(counts.values()) != independent["new_field_count"]
        or sum(counts.values()) != decisions["supported_fields"]
        or decisions["events"] != len(additions)
        or acceptance["initial_primary_failures_preserved"]
        != independent["initial_primary_failures_retained"]
        or acceptance["unresolved_primary_source_aggregates"]
        != independent["unresolved_primary_aggregates"]
    ):
        raise ValueError("Entitlement accepted field inventory differs")
    prior = {(row["symbol"], row["ex_date"]): row for row in previous["records"]}
    current = {(row["symbol"], row["ex_date"]): row for row in review["records"]}
    scoped = {(row["symbol"], row["ex_date"]) for row in additions}
    preserved_fields = sum(len(row["verified_fields"]) for row in previous["facts"])
    if (
        len(prior) != len(previous["records"])
        or prior.keys() != current.keys()
        or len(current) != independent["events_checked"]
        or len(current) != acceptance["events_checked"]
        or len(current) - len(scoped) != independent["unscoped_records_exact"]
        or preserved_fields != independent["prior_supported_fields_preserved"]
        or preserved_fields != acceptance["prior_supported_fields_preserved"]
        or any(current[key] != row for key, row in prior.items() if key not in scoped)
        or any(
            current[key]["verified_fields"].get(name) != value
            for key, row in prior.items()
            for name, value in row.get("verified_fields", {}).items()
        )
    ):
        raise ValueError("Prior distribution source fields or records changed")
    basis = load_snapshot(paths, basis_path, registered["source_data_cutoff"])
    if independent["source_basis_actions_sha256"] != digest(basis["actions"]):
        raise ValueError("Entitlement basis actions differ")
    return entitlement_facts(
        original["actions"],
        basis["actions"],
        review,
        additions,
        decisions["records"],
        {
            "review_sha256": protocol["review_sha256"],
            "review_basis": protocol["review_basis"],
            "source_acceptance": protocol["source_acceptance"],
            "independent_review": protocol["independent_review"],
            "field_decisions": protocol["field_decisions"],
            "source_context": protocol["source_context"],
        },
    )


def restored_label_table(table, records):
    """Restore only independently accepted unknown-action labels at their original rows."""
    expected = table.copy(deep=True)
    seen = set()
    for record in records:
        position, horizon = record["row"], record["horizon"]
        if (
            not isinstance(position, int)
            or isinstance(position, bool)
            or not 0 <= position < len(table)
            or type(horizon) is not int
            or horizon not in HORIZONS
            or (position, horizon) in seen
        ):
            raise ValueError("Invalid or duplicate restored label row")
        seen.add((position, horizon))
        row = table.iloc[position]
        names = [f"{field}{horizon}" for field in LABEL_FIELDS]
        before = [None if pd.isna(row[name]) else row[name] for name in names]
        after = record["after"]
        if (
            row.symbol != record["symbol"]
            or row.date_index != record["date_index"]
            or not row.eligible
            or before != record["before"]
            or before[0] is not None
            or before[1] != -1
            or before[2] != LABEL_STATUS["ACTION_UNRESOLVED"]
            or record["independent_before"] != before
            or record["independent_after"] != after
            or len(after) != 4
            or not np.isfinite(after[0])
            or after[0] < -1
            or not isinstance(after[1], int)
            or not isinstance(after[3], int)
            or not row.date_index + 1 + horizon <= after[1] <= table.date_index.max()
            or after[2] != LABEL_STATUS["OK"]
            or after[3] != after[1] - (row.date_index + 1 + horizon)
        ):
            raise ValueError("Restored label differs from its accepted censoring basis")
        for name, value in zip(names, after):
            expected.iat[position, expected.columns.get_loc(name)] = value
    return expected


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
    operation = protocol.get("operation")
    if operation not in (None, "supplement_verified_dates", "apply_verified_entitlements"):
        raise ValueError("Unsupported distribution repair operation")
    supplement = operation == "supplement_verified_dates"
    entitlement = operation == "apply_verified_entitlements"
    if (
        review["extractor_sha256"] != sha(Path(distributions.__file__))
        or review["source_inputs"] != protocol["source_inputs"]
        or ((supplement or entitlement) and sha(review_path) != protocol["review_sha256"])
        or (
            not (supplement or entitlement)
            and (
                review["protocol_sha256"] != sha(protocol_path)
                or review["data_snapshot_sha256"] != sha(source_path)
            )
        )
    ):
        raise ValueError("Frozen source review binding differs")
    for fact in review["facts"]:
        distributions.checked_path(paths.root, fact["source"]["path"], fact["source"]["sha256"])
    original = load_snapshot(paths, source_path, protocol["source_data_cutoff"])
    if supplement:
        facts = _accepted_date_supplements(paths, protocol, review, original, manifest)
    elif entitlement:
        facts = _accepted_entitlement_facts(paths, protocol, review, original, manifest)
    elif review["original_actions_sha256"] != digest(original["actions"]):
        raise ValueError("Source review does not bind the full action inventory")
    else:
        facts = review["facts"]
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
        original["actions"], original["calendar"], facts, supplement=supplement
    )
    if supplement or entitlement:
        counts = Counter(f for change in changes for f in change["fields"])
        counts["events"] = len(changes)
        if dict(counts) != protocol["expected_changes"]:
            raise ValueError("Distribution changes differ from registration")
        save(target / "fact_bindings.json", facts, immutable=True)
    repaired = build_dataset(original["meta"], original["raws"], actions, **rebuild_args)
    if repaired.get("fund_mode") != original.get("fund_mode"):
        raise ValueError("Distribution repair changed the fund control")
    if supplement:
        expected_table = original["table"]
        if protocol.get("label_review"):
            reference = protocol["label_review"]
            if manifest["files"].get(reference["path"]) != reference["sha256"]:
                raise ValueError("Independent label review is not frozen")
            label_review = read(
                distributions.checked_path(paths.root, reference["path"], reference["sha256"])
            )
            if (
                label_review["status"] != "PASS"
                or label_review["data_sha256"] != sha(source_path)
                or label_review["actions_sha256"] != digest(actions)
                or label_review["protocol_sha256"] != protocol["label_review_basis_protocol_sha256"]
                or label_review["changed_labels"] != protocol["expected_label_changes"]["total"]
                or label_review["changed_by_horizon"]
                != protocol["expected_label_changes"]["by_horizon"]
                or Counter(str(r["horizon"]) for r in label_review["records"])
                != label_review["changed_by_horizon"]
            ):
                raise ValueError("Independent label review binding differs")
            expected_table = restored_label_table(original["table"], label_review["records"])
        pd.testing.assert_frame_equal(expected_table, repaired["table"], check_exact=True)
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
    if entitlement:
        entitlement_dates.extend(
            c["ex_date"] for c in changes if "ex_reference_cash_per_share" in c["fields"]
        )
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
