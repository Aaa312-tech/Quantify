"""Independently check every current event and every unchanged numerical input."""

from collections import Counter
from pathlib import Path

import pandas as pd

from quantfolio.dataset import load_snapshot, validate_features
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol_file = root / "configs/protocols/distribution_availability.json"
p = read(protocol_file)
checks = root / p["checks_output"]
old = load_snapshot(paths, root / p["source_bundle"], p["source_data_cutoff"])
new_file = root / p["snapshot_output"] / "bundle.joblib"
new = load_snapshot(paths, new_file, p["source_data_cutoff"])
review = read(root / p["review"])
independent = read(root / p["independent_review"]["path"])
facts = {(r["symbol"], r["ex_date"]): r for r in review["facts"]}
additions = {
    (r["symbol"], r["ex_date"]): r["added_fields"]
    for r in independent["records"]
    if r["added_fields"]
}
assert independent["status"] == "PASS"
assert new.get("fund_mode") == old.get("fund_mode") == "reported"
assert set(old) == set(new)
assert new["meta"] == old["meta"]
assert new.get("opening_status") == old.get("opening_status")
assert new["features"] == old["features"] and new["cutoff"] == old["cutoff"]
pd.testing.assert_index_equal(old["calendar"], new["calendar"], exact=True)
label_review = read(root / p["label_review"]["path"])
assert label_review["status"] == "PASS" and label_review["data_sha256"] == sha(
    root / p["source_bundle"]
)
expected_table = old["table"].copy(deep=True)
for record in label_review["records"]:
    row = expected_table.iloc[record["row"]]
    assert row.symbol == record["symbol"] and row.date_index == record["date_index"]
    for field, value in zip(("y", "end", "status", "delay"), record["independent_after"]):
        column = f"{field}{record['horizon']}"
        expected_table.iat[record["row"], expected_table.columns.get_loc(column)] = value
pd.testing.assert_frame_equal(expected_table, new["table"], check_exact=True)
for section in ("raws", "panels", "scores"):
    assert old[section].keys() == new[section].keys()
    for name in old[section]:
        pd.testing.assert_frame_equal(old[section][name], new[section][name], check_exact=True)
dates = [str(day.date()) for day in old["calendar"]]
changes = []
counts = Counter()
for before, after in zip(old["actions"], new["actions"], strict=True):
    identity = before["symbol"], before["ex_date"]
    assert identity == (after["symbol"], after["ex_date"])
    expected = {
        field: value
        for field, value in additions.get(identity, {}).items()
        if before.get(field) is None
    }
    if not expected:
        assert before == after
        continue
    fact = facts[identity]
    assert set(expected) <= {"cash_pay_date", "share_list_date"}
    permitted = set(expected) | {"cash_available_date", "distribution_date_reviews"}
    assert {k: v for k, v in before.items() if k not in permitted} == {
        k: v for k, v in after.items() if k not in permitted
    }
    for field, value in expected.items():
        assert after[field] == value == fact["verified_fields"][field]
        assert value >= after["ex_date"]
        counts[field] += 1
    if "cash_pay_date" in expected:
        later = [day for day in dates if day > expected["cash_pay_date"]]
        assert after["cash_available_date"] == (later[0] if later else None)
    else:
        assert after.get("cash_available_date") == before.get("cash_available_date")
    history = before.get("distribution_date_reviews", [])
    assert after["distribution_date_reviews"][:-1] == history
    entry = after["distribution_date_reviews"][-1]
    assert entry["source"] == fact["source"]
    assert entry["known_at"] == fact["known_at"]
    assert entry["pages"] == fact["evidence_pages"]
    assert set(entry["fields"]) == set(expected)
    assert entry["date_basis"]["reviewed_action_sha256"] == fact["original_action_sha256"]
    assert entry["date_basis"]["current_action_sha256"] == digest(before)
    assert entry["date_basis"]["review_sha256"] == sha(root / p["review"])
    assert entry["date_basis"]["review_basis_sha256"] == p["review_basis"]["sha256"]
    assert sha(root / fact["source"]["path"]) == fact["source"]["sha256"]
    changes.append({"symbol": identity[0], "ex_date": identity[1], "fields": expected})
assert len(changes) == p["expected_changes"]["events"] == 154
assert dict(counts) == {"cash_pay_date": 154, "share_list_date": 16}
audit = read(new_file.parent / "audit.json")
assert audit["status"] == "PASS" and audit["parent_exact_rebuild"]
field_changes = read(new_file.parent / "field_changes.json")
assert not any(field_changes["panels"].values()) and not any(field_changes["scores"].values())
expected_fields = {
    f"{field}{horizon}": count
    for horizon, count in p["expected_label_changes"]["by_horizon"].items()
    for field in ("y", "end", "status")
}
assert {name: count for name, count in field_changes["table"].items() if count} == expected_fields
assert digest(new["actions"]) == label_review["actions_sha256"]
sources = {f.name: sha(f) for f in (root / "src/quantfolio").glob("*.py")}
assert new["provenance"]["source_sha256"] == sources
save(
    checks / "snapshot_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "protocol_sha256": sha(protocol_file),
        "source_sha256": sources,
        "bundle_sha256": sha(new_file),
        "parent_bundle_sha256": sha(root / p["source_bundle"]),
        "source_review_sha256": sha(root / p["review"]),
        "independent_review_sha256": sha(root / p["independent_review"]["path"]),
        "action_inventory": len(new["actions"]),
        "changed_events": len(changes),
        "changed_fields": dict(counts),
        "unchanged_actions": len(new["actions"]) - len(changes),
        "fund_mode": "reported",
        "all_non_label_inputs_exact": True,
        "restored_label_tuples": 106,
        "independent_label_review_sha256": sha(root / p["label_review"]["path"]),
        "all_other_label_cells_exact": True,
        "raw_calendar_metadata_opening_status_eligibility_exact": True,
        "existing_facts_and_reviews_preserved": True,
        "parent_exact_rebuild": audit["parent_exact_rebuild"],
        "features": validate_features(new),
        "changes": changes,
        "source_events_unverified": 557,
        "activated": False,
    },
    immutable=True,
)
print("INDEPENDENT SNAPSHOT REVIEW PASSED", len(changes), dict(counts), flush=True)
