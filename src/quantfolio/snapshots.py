"""Build an immutable, source-checked corporate-action repair of the reference data."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .dataset import check_integrity, load_bundle, validate_features
from .market import LABEL_STATUS, legal_label
from .storage import array_sha, dump_gzip, log, now, read, save, sha

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
        if action.get(declared_payment_field):
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
        if fact["cash_pay_date"] < fact["ex_date"]:
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
    groups = table.groupby("symbol", sort=True).indices
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


def repair_snapshot(paths, output):
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
    protocol_path = paths.root / "configs/protocols/action_repair.json"
    protocol = read(protocol_path)
    review_path = paths.root / protocol["review"]
    review = read(review_path)
    source_hash = sha(paths.reference_bundle)
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
        "source_bundle": paths.reference_bundle.relative_to(paths.root).as_posix(),
        "source_bundle_sha256": source_hash,
        "source_sha256": {
            file.name: sha(file) for file in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    save(target / "provenance.json", provenance, immutable=True)
    original = load_bundle(paths, reference=True)
    causal_table = rebuild_labels(original)
    repaired = {
        key: original[key]
        for key in ("meta", "raws", "actions", "panels", "scores", "calendar", "features", "cutoff")
    }
    if "opening_status" in original:
        repaired["opening_status"] = original["opening_status"]
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
    if sha(paths.reference_bundle) != source_hash:
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
