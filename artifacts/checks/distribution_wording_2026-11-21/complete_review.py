"""Accept source-only wording evidence without changing active research inputs."""

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
protocol_file = root / "configs/protocols/distribution_wording.json"
protocol = read(protocol_file)
base = root / protocol["checks_output"]
review = read(base / "source_review.json")
old = read(root / protocol["previous_review"]["path"])
before = {(r["symbol"], r["ex_date"]): r for r in old["records"]}
after = {(r["symbol"], r["ex_date"]): r for r in review["records"]}
assert len(before) == len(after) == 1839 and before.keys() == after.keys()
assert review["protocol_sha256"] == sha(protocol_file)
assert review["extractor_sha256"] == sha(base / "extractor.py")
assert not review["facts_applied"]
added = {}
preserved = 0
transitions = Counter()
for key, previous in before.items():
    current = after[key]
    assert previous["original_action_sha256"] == current["original_action_sha256"]
    assert previous.get("candidate_resolution") == current.get("candidate_resolution")
    for field, value in previous["verified_fields"].items():
        assert current["verified_fields"].get(field) == value
        preserved += 1
    fields = {
        f: v for f, v in current["verified_fields"].items() if f not in previous["verified_fields"]
    }
    if fields:
        assert set(fields) == {"cash_pay_date"}
        assert previous["status"] == "PRIMARY_GROSS_AMOUNT_UNSUPPORTED_OR_CONFLICTING"
        added[key] = fields
    if previous["status"] != current["status"]:
        transitions[previous["status"] + " -> " + current["status"]] += 1
assert preserved == 1386 and len(added) == 28
assert old["counts"]["VERIFIED_FIELDS"] == 1359
assert review["counts"]["VERIFIED_FIELDS"] == 1387
assert review["counts"]["PRIMARY_GROSS_AMOUNT_UNSUPPORTED_OR_CONFLICTING"] == 182
fields = read(base / "field_acceptance.json")
assert fields["status"] == "PASS" and fields["field_checks"] == 112 and fields["events"] == 28
assert fields["source_review_sha256"] == sha(base / "source_review.json")
assert fields["checker_sha256"] == sha(base / "check_field_acceptance.py")
assert fields["reader_sha256"] == sha(base / "read_field_closure.py")
assert fields["prior_reader_sha256"] == sha(base / "read_primary_fields.py")
assert all(
    r["passed"] and all(c["passed"] for c in r["checks"].values()) for r in fields["records"]
)
assert {(r["symbol"], r["ex_date"]): r["added_fields"] for r in fields["records"]} == added
failed = read(base / "independent_closure.json")
assert failed["status"] == "FAIL" and failed["failed_events"] == 7
assert failed["checker_sha256"] == sha(base / "check_field_closure.py")
assert failed["reader_sha256"] == sha(base / "read_primary_fields.py")
assert failed["prior_failed_checker_sha256"] == sha(base / "check_fields.py")
assert failed["prior_failure_log_sha256"] == sha(base / "independent_fields.log")
assert "AssertionError" in (base / "independent_fields.log").read_text(encoding="utf-8")
assert "11 failed, 14 passed" in (base / "before_tests.log").read_text(encoding="utf-8")
amendments = read(base / "amendment_review.json")
assert amendments["status"] == "PASS" and amendments["issuer_documents"] == 62
assert len(amendments["issuers"]) == 13 and amendments["amendment_candidates"] == 1
assert amendments["unresolved_conflicts"] == 0
assert amendments["checker_sha256"] == sha(base / "check_amendments.py")
assert amendments["field_acceptance_sha256"] == sha(base / "field_acceptance.json")
visual = read(base / "visual_review.json")
assert visual["status"] == "PASS" and visual["whole_pages_reviewed"] == 22
assert len(visual["field_layouts"]) == 7
assert visual["renderer_manifest_sha256"] == sha(base / "rendered_pages.json")
for row in visual["records"]:
    assert sha(root / row["image"]) == row["image_sha256"]
    assert sha(root / row["source"]) == row["source_sha256"]
implementation = read(base / "implementation.json")
assert implementation["status"] == "PASS" and implementation["checker_sha256"] == sha(
    base / "check_implementation.py"
)
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert source == implementation["source_sha256"]
state = protocol["active_state"]
assert sha(paths.configuration_path) == state["configuration_sha256"]
assert sha(paths.bundle) == state["bundle_sha256"]
plan_file, plan = verified_plan(paths, state["plan"])
assert sha(plan_file) == state["plan_sha256"] and not plan["orders"]
models = read(base / "models/verification.json")
assert models["status"] == "PASS" and models["models_reloaded"] == 48
assert models["data_sha256"] == state["bundle_sha256"] and models["fresh_refit"] is None
assert all(r["max_error"] == 0 and r["label_availability_pass"] for r in models["models"])
assert "570 passed, 1 skipped" in (base / "local_validation.log").read_text(encoding="utf-8")
bundle = load_bundle(paths)
actions = {(r["symbol"], r["ex_date"]): r for r in bundle["actions"]}
availability = Counter()
for key, values in added.items():
    action = actions[key]
    value = values["cash_pay_date"]
    assert action.get("cash_pay_date") in (None, value)
    assert action["record_date"] == after[key]["structured_source"]["record_date"]
    assert abs(
        Decimal(str(action["cash_per_share"]))
        - Decimal(after[key]["structured_source"]["cash_per_share"])
    ) <= Decimal("1e-12")
    availability["missing" if action.get("cash_pay_date") is None else "already_known"] += 1
assert dict(availability) == {"missing": 25, "already_known": 3}
baseline = json.loads(
    subprocess.check_output(
        ["git", "show", protocol["baseline_commit"] + ":configs/integrity_manifest.json"]
    )
)
assert len(baseline["files"]) == protocol["baseline_frozen_files"] == 35106
manifest = read(paths.manifest)
for relative, expected in baseline["files"].items():
    assert manifest["files"][relative] == expected
for file in sorted(base.rglob("*")):
    if file.is_file() and file.suffix != ".pyc" and "__pycache__" not in file.parts:
        relative = file.relative_to(root).as_posix()
        assert relative not in manifest["files"] or manifest["files"][relative] == sha(file)
        manifest["files"][relative] = sha(file)
save(paths.manifest, manifest)
print("CHECKING ALL FROZEN FILES", len(manifest["files"]), flush=True)
integrity = check_integrity(paths)
completion = {
    "status": "PASS_SOURCE_ONLY",
    "completed_at": now(),
    "checker_sha256": sha(Path(__file__)),
    "protocol_sha256": sha(protocol_file),
    "baseline_commit": protocol["baseline_commit"],
    "baseline_frozen_files_preserved": 35106,
    "integrity": integrity,
    "source_sha256": source,
    "changed_runtime_modules": ["__init__.py", "distributions.py"],
    "scope": "Six source-recognition string constants only. All other runtime modules and all financial, provider, timing, date and selection gates remain exact.",
    "events_reviewed": 1839,
    "prior_supported_events_preserved": 1359,
    "prior_supported_fields_preserved": preserved,
    "supported_events": 1387,
    "unverified_events": 452,
    "status_transitions": dict(transitions),
    "added_payment_dates": len(added),
    "active_missing_payment_dates_supported": 25,
    "already_present_matching_dates": 3,
    "independent_field_checks": 112,
    "visual_pages": 22,
    "visual_field_layouts": 7,
    "related_issuers": 13,
    "issuer_documents_inventoried": 62,
    "related_status_notice": 1,
    "model_reloads": 48,
    "prediction_max_error": 0,
    "local_tests": {"passed": 570, "skipped": 1, "reason": "Host directory symlink permission"},
    "active_state": state,
    "facts_applied": False,
    "fresh_fits": 0,
    "fresh_historical_account_replays": 0,
    "forward_observations": 0,
    "historical_results_unchanged": True,
    "evidence_sha256": {
        f.relative_to(base).as_posix(): sha(f)
        for f in sorted(base.rglob("*"))
        if f.is_file() and f.suffix != ".pyc" and "__pycache__" not in f.parts
    },
    "documents_sha256": {
        name: sha(root / name)
        for name in (
            "README.md",
            "CHANGELOG.md",
            "AGENTS.md",
            "docs/remediation.md",
            "docs/distribution_terms.md",
        )
    },
    "test_source_sha256": {
        "tests/test_distribution_wording.py": sha(root / "tests/test_distribution_wording.py")
    },
    "decision": "RESEARCH_ONLY",
    "whole_goal_complete": False,
    "limitations": protocol["limitations"],
    "next_step": "Separately register the 25 supported missing payment dates, determine label scope independently, rebuild and fully retrain, compare fixed forecasts/full accounts/paper checks before activation. All other remaining source and remediation work continues.",
}
target = base / "completion.json"
save(target, completion, immutable=True)
manifest["files"][target.relative_to(root).as_posix()] = sha(target)
save(paths.manifest, manifest)
print("SOURCE WORDING ACCEPTED", sha(target), len(manifest["files"]), flush=True)
