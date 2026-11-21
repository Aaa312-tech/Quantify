"""Bind field-scoped decisions to completed primary and issuer-context reviews."""

import datetime as dt
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from quantfolio.storage import digest, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def binding(path):
    return {"path": path.relative_to(root).as_posix(), "sha256": sha(path)}


root = Path.cwd()
base = Path(__file__).resolve().parent
protocol_path = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_path)
progress_path = base / "issuer_context_complete.json"
progress = read(progress_path)
require(progress["issuer_context"]["all_issuer_context_complete"], "Issuer context incomplete")
require(progress["protocol_sha256"] == sha(protocol_path), "Review protocol changed")
for path, expected in {**progress["observations"], **progress["evidence"]}.items():
    require(sha(root / path) == expected, path)
scope_path = root / protocol["scope"]["path"]
require(sha(scope_path) == protocol["scope"]["sha256"], "Scope changed")
scope = {(x["symbol"], x["ex_date"]): x for x in read(scope_path)["records"]}
require(len(scope) == 31, "Registered scope changed")
previous_review_path = root / protocol["source_inputs"]["previous_review"]["path"]
previous_review = read(previous_review_path)
previous_rows = {(r["symbol"], r["ex_date"]): r for r in previous_review["records"]}
require(len(previous_rows) == 1839, "Prior inventory changed")
require(sum(len(r["verified_fields"]) for r in previous_rows.values()) == 1448, "Prior fields")

primary_checks = {}
for relative, symbol in (
    ("remaining_primary_checks.json", None),
    ("adjusted_cash_primary_checks.json", None),
    ("issuer_checks/sz002475.json", "sz002475"),
    ("issuer_checks/sz002714.json", "sz002714"),
):
    report_path = base / relative
    for row in read(report_path)["records"]:
        key = (symbol or row["symbol"], row.get("ex_date", row.get("event_date")))
        require(key not in primary_checks, "Duplicate independent primary check")
        primary_checks[key] = (row, report_path)
require(set(primary_checks) == set(scope), "Incomplete independent primary coverage")
exceptions = {
    ("sh688627", "2024-07-01"): {"record_ex_payment_table_values"},
    ("sh688627", "2025-07-10"): {"record_ex_payment_table_values"},
    ("sz301313", "2024-05-31"): {"printed_aggregate_at_stated_precision"},
    ("sz300223", "2025-06-24"): {"printed_final_aggregate_at_cents"},
    ("sz300014", "2026-05-14"): {"original_proposal_arithmetic"},
}
closure_paths = [
    base / "remaining_primary_closure.json",
    base / "adjusted_cash_arithmetic_review.json",
]
require(read(closure_paths[0])["explained_failures"] == 3, "Remaining check closure changed")
require(read(closure_paths[1])["explained_check_failures"] == 1, "Adjusted check closure changed")
require(read(closure_paths[1])["unresolved_source_aggregates"] == 1, "Source conflict was cleared")
records, manual_events, cash_corrections = [], [], []
for key, registered in sorted(scope.items()):
    symbol, event_date = key
    observation_path = base / "observations" / f"{symbol}_{event_date}.json"
    observation = read(observation_path)
    fields = observation["observed_fields"]
    checked, check_path = primary_checks[key]
    require(checked["observation_sha256"] == sha(observation_path), "Observation identity")
    failed = {name for name, passed in checked["checks"].items() if not passed}
    require(failed == exceptions.get(key, set()), "Unreviewed independent failure")
    require(not previous_rows[key]["verified_fields"], "Cannot overwrite accepted source facts")
    require(
        digest(registered["source_basis_action"]) == observation["source_basis_action_sha256"],
        "Basis action",
    )
    require(
        digest(registered["current_action"]) == observation["current_action_sha256"],
        "Active action",
    )
    for action in (registered["source_basis_action"], registered["current_action"]):
        require(action["record_date"] == fields["record_date"], "Record date mismatch")
        require(
            Decimal(str(action["share_multiplier"])) == Decimal(fields["share_multiplier"]),
            "Multiplier mismatch",
        )
    require(fields["ex_date"] == event_date, "Ex-date mismatch")
    if fields["cash_unit_shares"] == 1:
        gross = Decimal(fields["cash_per_share"])
    else:
        require(fields["cash_unit_shares"] == 10, "Unsupported declared cash unit")
        gross = Decimal(fields["cash_per_ten_shares"]) / 10
    reviewed = {
        "record_date": fields["record_date"],
        "ex_date": event_date,
        "share_multiplier": fields["share_multiplier"],
        "cash_per_share": str(gross),
        "cash_pay_date": fields["cash_pay_date"],
    }
    for field in ("ex_reference_cash_per_share", "share_list_date"):
        if field in fields:
            reviewed[field] = fields[field]
    old_cash = Decimal(str(registered["current_action"]["cash_per_share"]))
    cash_action = (
        "PRESERVE_STORED_BINARY64"
        if abs(old_cash - gross) <= Decimal("1e-12")
        else "CORRECT_RECIPIENT_CASH_AFTER_SEPARATE_INTEGRATION"
    )
    if cash_action != "PRESERVE_STORED_BINARY64":
        cash_corrections.append(
            {"symbol": symbol, "ex_date": event_date, "old": str(old_cash), "reviewed": str(gross)}
        )
    context_path = base / "issuer_checks" / f"{symbol}.json"
    context = read(context_path)
    require(not context["failures"], "Issuer context failure")
    for name in ("source", "receipt"):
        require(sha(root / observation[name]["path"]) == observation[name]["sha256"], name)
    for page in observation["page_observations"]:
        require(page["whole_page_visually_inspected"], "Page not inspected")
        require(sha(root / page["image"]) == page["image_sha256"], "Reviewed image changed")
    catalogue_date = registered["candidate"]["catalog_date"]
    known = dt.date.fromisoformat(catalogue_date) + dt.timedelta(days=1)
    require(known.isoformat() <= event_date, "Late source under registered convention")
    evidence = {
        "primary_observation": binding(observation_path),
        "independent_primary_check": binding(check_path),
        "issuer_context": binding(context_path),
        "related_inventory": binding(base / "related_source_inventory.json"),
        "primary_failure_closures": [binding(path) for path in closure_paths],
    }
    decisions = {}
    for field in (
        "cash_per_share",
        "cash_pay_date",
        "share_list_date",
        "ex_reference_cash_per_share",
        "cash_rounding",
    ):
        if field in reviewed:
            reason = {
                "cash_per_share": "Final gross entitlement for an ordinary eligible A-share holder, with declared units; proposal, tax-net, other recipient classes and price-reference terms are excluded.",
                "cash_pay_date": "Explicit payment date for ordinary CSDC-paid holders; no intraday availability or self-paid holder date is inferred.",
                "share_list_date": "Explicit start of trading for the unrestricted capitalisation shares, separately labelled in the primary notice.",
                "ex_reference_cash_per_share": "Explicit equity ex-price cash deduction; retained separately from shareholder cash and repurchase/option/bond prices.",
            }[field]
            decisions[field] = {
                "status": "SUPPORTED_PRIMARY_FIELD",
                "value": reviewed[field],
                "basis": reason,
            }
        else:
            decisions[field] = {
                "status": "NOT_ESTABLISHED",
                "value": None,
                "basis": "No distinct declared equity ex-reference, distribution listing date or account settlement rounding is established for this field; no amount/date/rounding is inferred from another role.",
            }
    aggregate_note = None
    if key == ("sz300223", "2025-06-24"):
        aggregate_note = {
            "status": "SOURCE_AGGREGATE_DISCREPANCY_UNRESOLVED",
            "printed_total": "48156985.09",
            "gross_times_participants": "48156985.1065324",
            "failed_check_retained": "printed_final_aggregate_at_cents",
            "decision": "The final per-ten gross 0.997988 is explicitly repeated in the adjusted scheme and implemented payout, and both independent readers agree. Accept that directly declared per-share field and independently labelled dates only. Do not infer a different entitlement from the aggregate, certify the total, clear the failed check or infer account rounding.",
        }
    record = {
        "symbol": symbol,
        "ex_date": event_date,
        "source_id": observation["source_id"],
        "evidence": evidence,
        "control_fields": {k: reviewed[k] for k in ("record_date", "ex_date", "share_multiplier")},
        "field_decisions": decisions,
        "cash_storage_decision": cash_action,
        "initial_failed_checks_retained": sorted(failed),
        "unresolved_primary_aggregate": aggregate_note,
        "primary_anomalies": observation.get("source_anomalies"),
        "issuer_context_anomalies": context.get("source_anomalies", []),
        "interpretation": observation["interpretation"],
    }
    records.append(record)
    manual_events.append(
        {
            "symbol": symbol,
            "ex_date": event_date,
            "stored_action": registered["source_basis_action"],
            "reviewed_facts": reviewed,
            "decision": cash_action,
            "reason": observation["interpretation"],
            "source": {
                **observation["source"],
                "catalog_date": catalogue_date,
                "receipt": observation["receipt"]["path"],
                "receipt_sha256": observation["receipt"]["sha256"],
                "field_review": {
                    "path": (base / "field_decisions.json").relative_to(root).as_posix()
                },
            },
            "evidence_pages": sorted(page["page"] for page in observation["page_observations"]),
            "review_status": "FULL_TEXT_AND_RELEVANT_PAGE_IMAGES_REVIEWED",
            "field_evidence": evidence,
            "unresolved_primary_aggregate": aggregate_note,
            "historical_time_limitation": "Retrospectively retrieved; catalogue date plus one calendar day is the unchanged convention, not certification of original publication time or archive completeness.",
        }
    )
supported = Counter(
    field
    for record in records
    for field, decision in record["field_decisions"].items()
    if decision["status"] == "SUPPORTED_PRIMARY_FIELD"
)
require(
    supported
    == {
        "cash_per_share": 31,
        "cash_pay_date": 31,
        "share_list_date": 1,
        "ex_reference_cash_per_share": 19,
    },
    "Field inventory changed",
)
require(
    {(x["symbol"], x["ex_date"]) for x in cash_corrections}
    == {("sh600989", "2024-07-24"), ("sh600989", "2025-05-13"), ("sz300750", "2024-04-30")},
    "Unexpected economic correction",
)
field_path = base / "field_decisions.json"
save(
    field_path,
    {
        "status": "FIELD_DECISIONS_PENDING_FULL_INVENTORY_ACCEPTANCE",
        "recorded_at": now(),
        "script_sha256": sha(Path(__file__)),
        "protocol": binding(protocol_path),
        "scope": binding(scope_path),
        "context_completion": binding(progress_path),
        "events": 31,
        "supported_field_counts": dict(supported),
        "supported_fields": sum(supported.values()),
        "cash_corrections": cash_corrections,
        "records": records,
        "facts_applied": False,
        "whole_goal_complete": False,
        "boundary": "Field evidence only; the whole 1839-event production review must preserve 1448 prior fields and all unscoped decisions. Aggregates and account rounding are not accepted by inference. Runtime inputs remain unchanged.",
    },
    immutable=True,
)
for event in manual_events:
    event["source"]["field_review"]["sha256"] = sha(field_path)
old_manual_path = root / protocol["source_inputs"]["primary_reviews"]["path"]
old_manual = read(old_manual_path)
require(
    sha(old_manual_path) == protocol["source_inputs"]["primary_reviews"]["sha256"],
    "Prior manual review changed",
)
old_keys = {(x["symbol"], x["ex_date"]) for x in old_manual["events"]}
require(not old_keys.intersection(scope), "Prior manual event would be replaced")
manual = deepcopy(old_manual)
manual.update(
    reviewed_at=now(),
    status="PRIMARY_FACTS_REVIEWED_NOT_APPLIED",
    parent_review=binding(old_manual_path),
    field_decisions=binding(field_path),
    events=[*old_manual["events"], *manual_events],
)
manual_path = root / protocol["outputs"]["primary_supplement"]
save(manual_path, manual, immutable=True)
parent_path = root / "configs/protocols/payout_scope.json"
parent = read(parent_path)
source_inputs = deepcopy(parent["source_inputs"])
source_inputs["primary_reviews"] = binding(manual_path)
source_inputs["field_decisions"] = binding(field_path)
source_protocol = {
    "name": "distribution_repair",
    "phase": "recipient_and_adjusted_entitlements",
    "registered_at": now(),
    "baseline_commit": protocol["baseline_commit"],
    "parent_protocol": binding(parent_path),
    "evidence_protocol": binding(protocol_path),
    "source_bundle": parent["source_bundle"],
    "source_bundle_sha256": parent["source_bundle_sha256"],
    "source_data_cutoff": parent["source_data_cutoff"],
    "source_inputs": source_inputs,
    "allowed_fact_changes": parent["allowed_fact_changes"],
    "candidate_resolution": parent["candidate_resolution"],
    "review": protocol["outputs"]["source_review"],
    "checks_output": protocol["checks_output"],
    "previous_review": binding(previous_review_path),
    "field_decisions": binding(field_path),
    "active_state": protocol["active_state"],
    "acceptance": "Preserve all 1808 unscoped event records exactly, all 1448 supported source fields and the five earlier manual events plus the unchanged prior follow-up. Add only the 82 supported fields for the 31 registered events. No extraction, financial agreement, date/timing, strategy or runtime change.",
    "integration_gate": protocol["runtime_boundary"],
    "facts_applied": False,
    "whole_goal_complete": False,
}
save(root / protocol["outputs"]["compatible_source_protocol"], source_protocol, immutable=True)
print("FIELD_DECISIONS", len(records), dict(supported), "CASH_CORRECTIONS", cash_corrections)
print("MANUAL_EVENTS", len(manual["events"]), "PRIOR_EVENTS_PRESERVED", len(old_manual["events"]))
