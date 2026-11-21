"""Check the entire source inventory against the registered field evidence."""

from collections import Counter
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from quantfolio.storage import digest, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def indexed(rows):
    result = {(r["symbol"], r["ex_date"]): r for r in rows}
    require(len(result) == len(rows), "Duplicate event")
    return result


root = Path.cwd()
base = Path(__file__).resolve().parent
protocol_path = root / "configs/protocols/cash_entitlements.json"
protocol = read(protocol_path)
evidence_protocol = read(root / protocol["evidence_protocol"]["path"])
require(
    sha(root / protocol["evidence_protocol"]["path"]) == protocol["evidence_protocol"]["sha256"],
    "Evidence protocol changed",
)
for item in protocol["source_inputs"].values():
    require(sha(root / item["path"]) == item["sha256"], item["path"])
previous_path = root / protocol["previous_review"]["path"]
require(sha(previous_path) == protocol["previous_review"]["sha256"], "Previous review changed")
previous = read(previous_path)
output_path = base / "source_review.json"
actual = read(output_path)
require(actual["protocol_sha256"] == sha(protocol_path), "Production protocol binding")
require(
    actual["extractor_sha256"]
    == previous["extractor_sha256"]
    == sha(root / "src/quantfolio/distributions.py"),
    "Production extractor changed",
)
require(actual["pypdf_version"] == previous["pypdf_version"] == "6.9.2", "Reader version changed")
require(
    actual["original_actions_sha256"] == previous["original_actions_sha256"],
    "Source action inventory changed",
)
require(
    actual["data_snapshot_sha256"] == previous["data_snapshot_sha256"], "Source snapshot changed"
)
old, new = indexed(previous["records"]), indexed(actual["records"])
require(set(old) == set(new) and len(new) == 1839, "Whole-inventory identity")
field_path = root / protocol["field_decisions"]["path"]
require(sha(field_path) == protocol["field_decisions"]["sha256"], "Field decisions changed")
decisions = indexed(read(field_path)["records"])
scope = indexed(read(root / evidence_protocol["scope"]["path"])["records"])
require(set(decisions) == set(scope) and len(scope) == 31, "Scope changed")
old_manual = read(root / evidence_protocol["source_inputs"]["primary_reviews"]["path"])
manual = read(root / protocol["source_inputs"]["primary_reviews"]["path"])
manual_rows = indexed(manual["events"])
for event in old_manual["events"]:
    require(manual_rows[(event["symbol"], event["ex_date"])] == event, "Prior manual event changed")

preserved_fields, unchanged_records = 0, 0
new_field_counts = Counter()
checked_records = []
for key, row in new.items():
    before = old[key]
    require(
        row["original_action_sha256"] == before["original_action_sha256"],
        "Original action identity changed",
    )
    for field, value in before["verified_fields"].items():
        require(
            row["verified_fields"].get(field) == value,
            f"Prior supported field changed: {key} {field}",
        )
        preserved_fields += 1
    if key not in scope:
        require(row == before, f"Unscoped record changed: {key}")
        unchanged_records += 1
        continue
    decision = decisions[key]
    observation_path = root / decision["evidence"]["primary_observation"]["path"]
    require(
        sha(observation_path) == decision["evidence"]["primary_observation"]["sha256"],
        "Primary evidence changed",
    )
    observation = read(observation_path)
    values = observation["observed_fields"]
    gross = (
        Fraction(values["cash_per_share"])
        if values["cash_unit_shares"] == 1
        else Fraction(values["cash_per_ten_shares"]) / 10
    )
    expected = {
        field: fact["value"]
        for field, fact in decision["field_decisions"].items()
        if fact["status"] == "SUPPORTED_PRIMARY_FIELD"
    }
    require(Fraction(expected["cash_per_share"]) == gross, "Declared cash unit conversion differs")
    require(expected["cash_pay_date"] == values["cash_pay_date"], "Payment evidence differs")
    for field in ("share_list_date", "ex_reference_cash_per_share"):
        require(expected.get(field) == values.get(field), f"Unsupported or missing field: {field}")
    require("cash_rounding" not in expected, "Unproved account rounding accepted")
    require(row["verified_fields"] == expected, f"Produced fields differ: {key}")
    require(
        row["method"] == "MANUAL_PRIMARY_REVIEW" and row["status"] == "VERIFIED_FIELDS",
        "Manual review status",
    )
    require(row["source"] == manual_rows[key]["source"], "Primary source changed")
    require(row["source"]["path"] == observation["source"]["path"], "Source identity differs")
    require(row["source"]["sha256"] == observation["source"]["sha256"], "Source hash differs")
    require(
        row["evidence_pages"] == sorted(p["page"] for p in observation["page_observations"]),
        "Reviewed page scope differs",
    )
    for label in ("independent_primary_check", "issuer_context", "related_inventory"):
        item = decision["evidence"][label]
        require(sha(root / item["path"]) == item["sha256"], label)
    for item in decision["evidence"]["primary_failure_closures"]:
        require(sha(root / item["path"]) == item["sha256"], "Failure closure changed")
    cash_difference = abs(
        Decimal(str(scope[key]["current_action"]["cash_per_share"]))
        - Decimal(expected["cash_per_share"])
    )
    preserve_cash = cash_difference <= Decimal("1e-12")
    require(
        (decision["cash_storage_decision"] == "PRESERVE_STORED_BINARY64") == preserve_cash,
        "Stored cash preservation rule differs",
    )
    new_field_counts.update(expected.keys())
    checked_records.append(
        {
            "symbol": key[0],
            "ex_date": key[1],
            "verified_fields": expected,
            "original_action_sha256": row["original_action_sha256"],
            "primary_observation_sha256": sha(observation_path),
            "field_evidence": decision["evidence"],
            "preserve_original_cash_binary64_at_integration": preserve_cash,
            "initial_failed_checks_retained": decision["initial_failed_checks_retained"],
            "unresolved_primary_aggregate": decision["unresolved_primary_aggregate"],
        }
    )
require(preserved_fields == 1448 and unchanged_records == 1808, "Prior preservation count")
require(
    new_field_counts
    == {
        "cash_per_share": 31,
        "cash_pay_date": 31,
        "share_list_date": 1,
        "ex_reference_cash_per_share": 19,
    },
    "Added field count",
)
require(
    actual["facts"] == [row for row in actual["records"] if row["verified_fields"]],
    "Fact list differs from complete records",
)
require(len(actual["facts"]) == 1452, "Supported event count")
require(sum(len(row["verified_fields"]) for row in new.values()) == 1530, "Supported field count")
require(Counter(row["status"] for row in new.values()) == actual["counts"], "Status inventory")
ingenic = decisions[("sz300223", "2025-06-24")]["unresolved_primary_aggregate"]
require(
    ingenic["status"] == "SOURCE_AGGREGATE_DISCREPANCY_UNRESOLVED", "Source discrepancy cleared"
)
require(
    sum(bool(x["unresolved_primary_aggregate"]) for x in decisions.values()) == 1,
    "Unresolved aggregate inventory",
)
state = evidence_protocol["active_state"]
require(sha(root / state["configuration"]) == state["configuration_sha256"], "Active configuration")
for name in ("bundle", "plan"):
    require(sha(root / state[name]["path"]) == state[name]["sha256"], name)
for name, expected in state["source_sha256"].items():
    require(sha(root / "src/quantfolio" / name) == expected, name)
imports_path = base / "prior_reviews/publication_import.json"
for alias in read(imports_path)["aliases"]:
    require(sha(root / alias["path"]) == alias["sha256"], "Imported prior evidence changed")
save(
    base / "field_acceptance.json",
    {
        "status": "FIELD_SOURCE_ACCEPTANCE_PASSED",
        "checked_at": now(),
        "script_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(protocol_path),
        "source_review_sha256": sha(output_path),
        "field_decisions_sha256": sha(field_path),
        "previous_review_sha256": sha(previous_path),
        "prior_import_sha256": sha(imports_path),
        "events_checked": 1839,
        "unscoped_records_exact": unchanged_records,
        "prior_supported_fields_preserved": preserved_fields,
        "new_fields": dict(new_field_counts),
        "new_field_count": sum(new_field_counts.values()),
        "supported_events": 1452,
        "supported_fields": 1530,
        "unverified_events": 387,
        "initial_primary_failures_retained": 5,
        "unresolved_primary_aggregates": 1,
        "records": checked_records,
        "prior_manual_events_preserved": len(old_manual["events"]),
        "source_basis_actions_sha256": digest(
            [scope[k]["source_basis_action"] for k in sorted(scope)]
        ),
        "runtime_inputs_changed": False,
        "facts_applied": False,
        "whole_goal_complete": False,
        "acceptance_boundary": "Only the listed 82 fields have new evidence. All unscoped decisions are exact and all previous fields are retained. Source aggregate conflicts, account rounding, archive completeness and exact availability remain uncertified; no active data or historical result changes. Publication validation and separate economic integration remain required.",
    },
    immutable=True,
)
print(
    "FIELD_ACCEPTANCE_PASS",
    "EVENTS",
    len(new),
    "PRIOR_FIELDS",
    preserved_fields,
    "NEW_FIELDS",
    sum(new_field_counts.values()),
)
print("UNCHANGED_UNSCOPED", unchanged_records, "ACTIVE_STATE_UNCHANGED", "UNRESOLVED_AGGREGATES", 1)
