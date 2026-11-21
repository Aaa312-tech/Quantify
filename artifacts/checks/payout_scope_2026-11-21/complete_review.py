"""Accept implementation-scope source facts while preserving active research inputs."""

import json
import subprocess
from collections import Counter
from decimal import Decimal
from pathlib import Path

from quantfolio.dataset import check_integrity, load_bundle
from quantfolio.planning import verified_plan
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol_file = root / "configs/protocols/payout_scope.json"
order_file = root / "configs/protocols/payout_page_order.json"
protocol = read(protocol_file)
base = root / protocol["checks_output"]
review = read(base / "source_review.json")
old_file = root / protocol["previous_review"]["path"]
assert sha(old_file) == protocol["previous_review"]["sha256"]
old = read(old_file)
before = {(row["symbol"], row["ex_date"]): row for row in old["records"]}
after = {(row["symbol"], row["ex_date"]): row for row in review["records"]}
assert len(before) == len(after) == 1839 and before.keys() == after.keys()
assert review["protocol_sha256"] == sha(protocol_file)
assert read(order_file)["parent_protocol_sha256"] == sha(protocol_file)
assert review["extractor_sha256"] == sha(base / "executed_distributions.py")
assert not review["facts_applied"]
added = {}
preserved = 0
transitions = Counter()
for key, previous in before.items():
    current = after[key]
    assert previous["original_action_sha256"] == current["original_action_sha256"]
    assert previous.get("candidate_resolution") == current.get("candidate_resolution")
    for field, value in previous["verified_fields"].items():
        assert current["verified_fields"].get(field) == value, (key, field)
        preserved += 1
    if previous["verified_fields"]:
        for field in ("source", "method", "known_at"):
            assert current[field] == previous[field], (key, field)
    additions = {
        field: value
        for field, value in current["verified_fields"].items()
        if field not in previous["verified_fields"]
    }
    if additions:
        assert set(additions) == {"cash_pay_date"}
        assert previous["status"] == "PRIMARY_GROSS_AMOUNT_UNSUPPORTED_OR_CONFLICTING"
        added[key] = additions
    if previous["status"] != current["status"]:
        transitions[previous["status"] + " -> " + current["status"]] += 1
assert preserved == 1414 and len(added) == 34
assert old["counts"]["VERIFIED_FIELDS"] == 1387
assert review["counts"]["VERIFIED_FIELDS"] == 1421
assert review["counts"]["PRIMARY_GROSS_AMOUNT_UNSUPPORTED_OR_CONFLICTING"] == 139

fields = read(base / "independent_closure.json")
assert fields["status"] == "PASS" and fields["field_checks"] == 136 and fields["events"] == 34
assert fields["source_review_sha256"] == sha(base / "source_review.json")
assert fields["checker_sha256"] == sha(base / "check_field_closure.py")
assert fields["reader_sha256"] == sha(base / "read_field_closure.py")
assert fields["prior_reader_sha256"] == sha(base / "read_primary_fields.py")
assert fields["initial_failed_checker_sha256"] == sha(base / "check_fields.py")
assert fields["initial_failed_review_sha256"] == sha(base / "independent_fields.json")
assert fields["initial_failed_log_sha256"] == sha(base / "independent_fields.log")
assert all(
    row["passed"] and all(check["passed"] for check in row["checks"].values())
    for row in fields["records"]
)
assert {(row["symbol"], row["ex_date"]): row["added_fields"] for row in fields["records"]} == added
failed = read(base / "independent_fields.json")
assert failed["status"] == "FAIL" and failed["failed_events"] == 6
assert failed["checker_sha256"] == sha(base / "check_fields.py")
assert failed["reader_sha256"] == sha(base / "read_primary_fields.py")
before_tests = read(base / "before_tests.json")
assert before_tests["status"] == "EXPECTED_FAILURE_BEFORE_IMPLEMENTATION"
assert before_tests["log_sha256"] == sha(base / "before_tests.log")
assert before_tests["extractor_sha256"] == sha(base / "before_distributions.py")
assert "13 failed, 26 passed" in (base / "before_tests.log").read_text(encoding="utf-8")
test_file = root / "tests/test_implementation_payouts.py"
assert before_tests["test_sha256"] == sha(test_file) == sha(base / "executed_tests.py")
assert sha(base / "before_tests.py") == sha(test_file)

amendments = read(base / "amendment_review.json")
assert amendments["status"] == "PASS" and amendments["issuer_documents"] == 55
assert len(amendments["issuers"]) == 12 and amendments["body_hits_reviewed"] == 10
assert amendments["body_hit_documents"] == 9 and amendments["unresolved_conflicts"] == 0
assert amendments["checker_sha256"] == sha(base / "accept_reviews.py")
assert amendments["independent_closure_sha256"] == sha(base / "independent_closure.json")
assert amendments["inventory_sha256"] == sha(base / "related_notices.json")
assert amendments["visual_review_sha256"] == sha(base / "visual_review.json")
visual = read(base / "visual_review.json")
assert visual["status"] == "PASS" and visual["whole_pages_reviewed"] == 38
assert len(visual["field_layouts"]) == 21 and len(visual["initial_mismatches_reviewed"]) == 6
assert visual["checker_sha256"] == sha(base / "accept_reviews.py")
assert visual["observations_sha256"] == sha(base / "visual_observations.json")
assert visual["renderer_manifest_sha256"] == sha(base / "rendered_pages.json")
assert visual["related_renderer_manifest_sha256"] == sha(base / "related_pages.json")
assert visual["independent_closure_sha256"] == sha(base / "independent_closure.json")
for row in visual["records"]:
    assert sha(root / row["image"]) == row["image_sha256"]
    assert sha(root / row["source"]) == row["source_sha256"]

implementation = read(base / "implementation.json")
assert implementation["status"] == "PASS"
assert implementation["checker_sha256"] == sha(base / "check_implementation.py")
assert implementation["protocol_sha256"] == sha(protocol_file)
assert implementation["page_order_protocol_sha256"] == sha(order_file)
assert implementation["unchanged_financial_acceptance"]
source = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
assert source == implementation["source_sha256"]
assert source["distributions.py"] == sha(base / "executed_distributions.py")
state = protocol["active_state"]
changed_modules = sorted(
    name for name, value in source.items() if state["source_sha256"][name] != value
)
assert changed_modules == ["__init__.py", "distributions.py"]
assert sha(paths.configuration_path) == state["configuration_sha256"]
assert sha(paths.bundle) == state["bundle_sha256"]
plan_file, plan = verified_plan(paths, state["plan"])
assert sha(plan_file) == state["plan_sha256"] and not plan["orders"]
models = read(base / "models/verification.json")
assert models["status"] == "PASS" and models["models_reloaded"] == 48
assert models["data_sha256"] == state["bundle_sha256"] and models["fresh_refit"] is None
assert all(row["max_error"] == 0 and row["label_availability_pass"] for row in models["models"])
tests = read(base / "local_validation.json")
assert tests["status"] == "PASS" and tests["passed"] == 646 and tests["skipped"] == 1
assert tests["source_sha256"] == source and tests["test_sha256"] == sha(test_file)
assert tests["log_sha256"] == sha(base / "local_validation.log")
assert "646 passed, 1 skipped" in (base / "local_validation.log").read_text(encoding="utf-8")

bundle = load_bundle(paths)
actions = {(row["symbol"], row["ex_date"]): row for row in bundle["actions"]}
availability = []
for key, values in added.items():
    action = actions[key]
    value = values["cash_pay_date"]
    assert action.get("cash_pay_date") in (None, value)
    assert action["record_date"] == after[key]["structured_source"]["record_date"]
    assert abs(
        Decimal(str(action["cash_per_share"]))
        - Decimal(after[key]["structured_source"]["cash_per_share"])
    ) <= Decimal("1e-12")
    availability.append(
        {
            "symbol": key[0],
            "ex_date": key[1],
            "accepted": value,
            "active": action.get("cash_pay_date"),
        }
    )
assert sum(row["active"] is None for row in availability) == 31
assert sum(row["active"] is not None for row in availability) == 3

baseline_bytes = subprocess.check_output(
    ["git", "show", protocol["baseline_commit"] + ":configs/integrity_manifest.json"]
)
baseline = json.loads(baseline_bytes)
assert len(baseline["files"]) == protocol["baseline_frozen_files"] == 36229
manifest = read(paths.manifest)
for relative, expected in baseline["files"].items():
    assert manifest["files"][relative] == expected, relative
for file in sorted(base.rglob("*")):
    if file.is_file() and file.suffix != ".pyc" and "__pycache__" not in file.parts:
        relative = file.relative_to(root).as_posix()
        assert relative not in manifest["files"] or manifest["files"][relative] == sha(file)
        manifest["files"][relative] = sha(file)
save(paths.manifest, manifest)
print("CHECKING ALL FROZEN FILES", len(manifest["files"]), flush=True)
integrity = check_integrity(paths)
reader_paths = [
    base / "read_field_closure.py",
    base / "read_primary_fields.py",
    root / "artifacts/checks/distribution_wording_2026-11-21/read_field_closure.py",
    root / "artifacts/checks/distribution_wording_2026-11-21/read_primary_fields.py",
    root / "artifacts/checks/distribution_terms_2026-11-21/independent_fields.py",
]
completion = {
    "status": "PASS_SOURCE_ONLY",
    "completed_at": now(),
    "checker_sha256": sha(Path(__file__)),
    "protocol_sha256": sha(protocol_file),
    "page_order_protocol_sha256": sha(order_file),
    "baseline_commit": protocol["baseline_commit"],
    "baseline_frozen_files_preserved": len(baseline["files"]),
    "integrity": integrity,
    "source_sha256": source,
    "changed_runtime_modules": changed_modules,
    "scope": implementation["scope"],
    "events_reviewed": len(after),
    "prior_supported_events_preserved": old["counts"]["VERIFIED_FIELDS"],
    "prior_supported_fields_preserved": preserved,
    "supported_events": review["counts"]["VERIFIED_FIELDS"],
    "unverified_events": len(after) - review["counts"]["VERIFIED_FIELDS"],
    "status_transitions": dict(transitions),
    "added_payment_dates": len(added),
    "active_missing_payment_dates_supported": 31,
    "already_present_matching_dates": 3,
    "date_availability": availability,
    "independent_field_checks": fields["field_checks"],
    "initial_independent_failures_preserved": failed["failed_events"],
    "reader_chain_sha256": {file.relative_to(root).as_posix(): sha(file) for file in reader_paths},
    "visual_pages": visual["whole_pages_reviewed"],
    "visual_field_layouts": len(visual["field_layouts"]),
    "related_issuers": len(amendments["issuers"]),
    "issuer_documents_inventoried": amendments["issuer_documents"],
    "related_body_hits_reviewed": amendments["body_hits_reviewed"],
    "model_reloads": models["models_reloaded"],
    "prediction_max_error": max(row["max_error"] for row in models["models"]),
    "local_tests": {
        "passed": tests["passed"],
        "skipped": 1,
        "reason": "Host directory symlink permission",
    },
    "active_state": state,
    "facts_applied": False,
    "fresh_fits": 0,
    "fresh_historical_account_replays": 0,
    "forward_observations": 0,
    "historical_results_unchanged": True,
    "evidence_sha256": {
        file.relative_to(base).as_posix(): sha(file)
        for file in sorted(base.rglob("*"))
        if file.is_file() and file.suffix != ".pyc" and "__pycache__" not in file.parts
    },
    "documents_sha256": {
        name: sha(root / name)
        for name in (
            "README.md",
            "CHANGELOG.md",
            "AGENTS.md",
            "docs/remediation.md",
            "docs/distribution_terms.md",
            "docs/distribution_availability.md",
        )
    },
    "test_source_sha256": {test_file.relative_to(root).as_posix(): sha(test_file)},
    "decision": "RESEARCH_ONLY",
    "whole_goal_complete": False,
    "limitations": protocol["limitations"],
    "next_step": "Separately register the 31 supported missing payment dates, determine label scope independently, rebuild and fully retrain, compare fixed forecasts/full accounts/paper checks before activation. Continue all other remaining source, status/quote, historical-universe/timing, exposure, competition-execution and forward-observation work.",
}
target = base / "completion.json"
save(target, completion, immutable=True)
manifest["files"][target.relative_to(root).as_posix()] = sha(target)
save(paths.manifest, manifest)
print("SOURCE SCOPE ACCEPTED", sha(target), len(manifest["files"]), flush=True)
