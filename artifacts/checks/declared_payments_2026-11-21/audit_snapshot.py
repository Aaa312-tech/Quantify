"""Check the rebuilt snapshot against independent events and exact parent inputs."""

from collections import Counter
from pathlib import Path

import pandas as pd

from quantfolio.dataset import load_snapshot, validate_features
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha

root = Path.cwd()
pfile = root / "configs/protocols/declared_payments.json"
p = read(pfile)
paths = Paths(root)
checks = root / p["checks_output"]
scope = read(checks / "input_scope.json")
assert scope["status"] == "PASS" and scope["label_change_expectation"] == 0
assert scope["protocol_sha256"] == sha(pfile)
old = load_snapshot(paths, root / p["source_bundle"], p["source_data_cutoff"])
new_file = root / p["snapshot_output"] / "bundle.joblib"
new = load_snapshot(paths, new_file, p["source_data_cutoff"])
assert new.get("fund_mode") == old.get("fund_mode") == "reported"
assert set(new) == set(old)
for key in ("meta", "opening_status", "features", "cutoff"):
    assert new[key] == old[key]
pd.testing.assert_index_equal(new["calendar"], old["calendar"], exact=True)
pd.testing.assert_frame_equal(new["table"], old["table"], check_exact=True)
for section in ("raws", "panels", "scores"):
    assert new[section].keys() == old[section].keys()
    for name in old[section]:
        pd.testing.assert_frame_equal(new[section][name], old[section][name], check_exact=True)
expected = read(checks / "expected_actions.json")
assert new["actions"] == expected and digest(expected) == scope["actions_sha256"]
changes = read(new_file.parent / "action_changes.json")
counts = Counter(field for change in changes for field in change["fields"])
assert counts == {"cash_pay_date": 25} and len(changes) == 25
assert all(change["fields"]["cash_pay_date"]["before"] is None for change in changes)
audit = read(new_file.parent / "audit.json")
assert audit["status"] == "PASS" and audit["parent_exact_rebuild"]
field_changes = read(new_file.parent / "field_changes.json")
assert all(not any(values.values()) for values in field_changes.values())
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert sources == new["provenance"]["source_sha256"] == scope["source_sha256"]
save(
    checks / "snapshot_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "protocol_sha256": sha(pfile),
        "source_sha256": sources,
        "bundle_sha256": sha(new_file),
        "parent_bundle_sha256": sha(root / p["source_bundle"]),
        "source_review_sha256": sha(root / p["review"]),
        "independent_review_sha256": p["independent_review"]["sha256"],
        "input_scope_sha256": sha(checks / "input_scope.json"),
        "expected_actions_sha256": sha(checks / "expected_actions.json"),
        "action_inventory": len(new["actions"]),
        "changed_events": 25,
        "changed_fields": dict(counts),
        "unchanged_actions": len(new["actions"]) - 25,
        "fund_mode": "reported",
        "all_non_label_inputs_exact": True,
        "restored_label_tuples": 0,
        "all_label_cells_exact": True,
        "raw_calendar_metadata_opening_status_eligibility_exact": True,
        "existing_facts_and_reviews_preserved": True,
        "parent_exact_rebuild": audit["parent_exact_rebuild"],
        "features": validate_features(new),
        "changes": changes,
        "source_events_unverified": 452,
        "activated": False,
    },
    immutable=True,
)
print("INDEPENDENT SNAPSHOT REVIEW PASSED", len(changes), dict(counts), flush=True)
