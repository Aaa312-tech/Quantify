"""Accept source-only evidence while preserving all active research and paper inputs."""

import ast
import json
import subprocess
from collections import Counter
from pathlib import Path

import joblib

from quantfolio.dataset import check_integrity
from quantfolio.planning import source_hashes
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol_file = root / "configs/protocols/distribution_roles.json"
protocol = read(protocol_file)
base = root / protocol["checks_output"]
review_file = root / protocol["review"]
review = read(review_file)
previous_file = root / protocol["previous_review"]["path"]
previous = read(previous_file)
assert sha(previous_file) == protocol["previous_review"]["sha256"]


def identity(row):
    return row["symbol"], row["ex_date"]


old = {identity(row): row for row in previous["records"]}
new = {identity(row): row for row in review["records"]}
assert len(old) == len(new) == 1839 and old.keys() == new.keys()
unchanged_records = 0
for key, before in old.items():
    after = new[key]
    assert before["original_action_sha256"] == after["original_action_sha256"]
    if "candidate_resolution" not in after:
        assert before == after
        unchanged_records += 1
    for field, value in before["verified_fields"].items():
        assert after["verified_fields"][field] == value
assert unchanged_records == 1676
assert previous["counts"]["VERIFIED_FIELDS"] == 1282
assert review["counts"]["VERIFIED_FIELDS"] == 1359
added = {
    key: {k: v for k, v in row["verified_fields"].items() if k not in old[key]["verified_fields"]}
    for key, row in new.items()
}
added = {key: fields for key, fields in added.items() if fields}
assert len(added) == 77 and all(set(fields) == {"cash_pay_date"} for fields in added.values())

independent = read(base / "independent_closure.json")
assert independent["status"] == "PASS" and independent["checks"] == 308
assert independent["source_review_sha256"] == sha(review_file)
assert independent["checker_sha256"] == sha(base / "check_source_closure.py")
assert independent["field_reader_sha256"] == sha(base / "read_additional_fields.py")
assert all(row["passed"] for row in independent["roles"] + independent["fields"])
assert {identity(row): row["added_fields"] for row in independent["fields"]} == added
assert independent["multi_events"] == 163 and independent["documents_checked"] == 329
assert independent["resolved_events"] == 94
initial = read(base / "independent_sources.json")
assert (
    initial["status"] == "FAIL" and initial["failed_roles"] == 31 and initial["failed_events"] == 3
)
assert initial["checker_sha256"] == sha(base / "check_sources.py")
assert independent["prior_failed_review_sha256"] == sha(base / "independent_sources.json")
amendments = read(base / "amendment_closure.json")
assert amendments["status"] == "PASS" and amendments["amendment_notices"] == 56
assert amendments["source_review_sha256"] == sha(review_file)
assert amendments["checker_sha256"] == sha(base / "check_amendment_closure.py")
assert amendments["decisions"]["AMENDED_GROSS_AGREES"] == 15
assert amendments["prior_failed_checker_sha256"] == sha(base / "check_amendments.py")
assert amendments["prior_failure_log_sha256"] == sha(base / "amendment_review.log")
visual = read(base / "visual_review.json")
assert visual["status"] == "PASS" and visual["whole_pages_reviewed"] == 72
assert len(visual["field_layouts"]) == 11 and len(visual["notice_roles"]) == 3
assert visual["renderer_manifest_sha256"] == sha(base / "rendered_pages.json")
for row in visual["records"]:
    assert sha(root / row["image"]) == row["image_sha256"]
    assert sha(root / row["source"]) == row["source_sha256"]

state = protocol["active_state"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert source.keys() == state["source_sha256"].keys()
changed = {name for name in source if source[name] != state["source_sha256"][name]}
assert changed == {"__init__.py", "distributions.py"}
assert source["distributions.py"] == sha(base / "extractor.py") == review["extractor_sha256"]
assert sha(base / "before_distributions.py") == state["source_sha256"]["distributions.py"]


def nodes(file):
    module = ast.parse(file.read_text(encoding="utf-8"))
    functions = {n.name: ast.dump(n) for n in module.body if isinstance(n, ast.FunctionDef)}
    other = [ast.dump(n) for n in module.body if not isinstance(n, ast.FunctionDef)]
    return functions, other


before, before_other = nodes(base / "before_distributions.py")
after, after_other = nodes(root / "src/quantfolio/distributions.py")
assert before_other == after_other and before.keys() <= after.keys()
assert after.keys() - before.keys() == {
    "_compact",
    "notice_role",
    "resolve_companion_notices",
    "_candidate_pages",
}
assert {name for name in before if before[name] != after[name]} == {
    "registered_inputs",
    "review_distributions",
}
assert sha(paths.configuration_path) == state["configuration_sha256"]
assert sha(paths.bundle) == state["bundle_sha256"]
assert sha(root / state["plan"]) == state["plan_sha256"]
plan = read(root / state["plan"])
assert plan["source_sha256"] == source_hashes() and len(plan["orders"]) == 0
for relative, expected in plan["input_sha256"].items():
    assert sha(root / relative) == expected
models = read(base / "models/verification.json")
assert models["status"] == "PASS" and models["models_reloaded"] == 48
assert models["data_sha256"] == state["bundle_sha256"]
assert all(row["max_error"] == 0 for row in models["models"])
assert "482 passed, 1 skipped" in (base / "local_validation.log").read_text(encoding="utf-8")
bundle = joblib.load(paths.bundle)
actions = {identity(a): a for a in bundle["actions"]}
availability = Counter()
for key, fields in added.items():
    current = actions[key].get("cash_pay_date")
    assert current in (None, fields["cash_pay_date"])
    availability["missing" if current is None else "already_present"] += 1
assert dict(availability) == {"missing": 73, "already_present": 4}

baseline = json.loads(
    subprocess.check_output(
        [
            "git",
            "show",
            protocol["baseline_commit"] + ":configs/integrity_manifest.json",
        ]
    )
)
manifest = read(paths.manifest)
assert len(baseline["files"]) == protocol["baseline_frozen_files"] == 33786
for relative, expected in baseline["files"].items():
    assert manifest["files"][relative] == expected
for file in sorted(base.rglob("*")):
    if file.is_file():
        relative = file.relative_to(root).as_posix()
        assert relative not in manifest["files"] or manifest["files"][relative] == sha(file)
        manifest["files"][relative] = sha(file)
save(paths.manifest, manifest)
integrity = check_integrity(paths)
assert integrity["status"] == "PASS"
completion = {
    "status": "PASS_SOURCE_ONLY",
    "completed_at": now(),
    "protocol_sha256": sha(protocol_file),
    "baseline_commit": protocol["baseline_commit"],
    "baseline_frozen_files_preserved": 33786,
    "integrity": integrity,
    "source_sha256": source,
    "changed_runtime_modules": sorted(changed),
    "original_financial_extraction_and_verification_ast_unchanged": True,
    "events_reviewed": 1839,
    "non_multiple_records_exact": unchanged_records,
    "prior_supported_events_preserved": 1282,
    "supported_events": 1359,
    "unverified_events": 480,
    "original_multi_events": 163,
    "candidate_occurrences_checked": 329,
    "unique_candidate_documents": len({r["source"]["id"] for r in independent["roles"]}),
    "resolved_candidate_sets": 94,
    "added_payment_dates": 77,
    "active_missing_payment_dates_supported": 73,
    "already_present_matching_dates": 4,
    "independent_field_checks": 308,
    "amendment_notices": 56,
    "same_period_amended_amounts_agree": 15,
    "visual_pages": 72,
    "visual_field_layouts": 11,
    "model_reloads": 48,
    "prediction_max_error": 0,
    "local_tests": {"passed": 482, "skipped": 1, "reason": "Host directory symlink permission"},
    "evidence_sha256": {
        name: sha(base / name)
        for name in (
            "source_review.json",
            "independent_sources.json",
            "independent_closure.json",
            "amendment_closure.json",
            "visual_review.json",
            "models/verification.json",
            "local_validation.log",
        )
    },
    "active_state": state,
    "facts_applied": False,
    "return_change": None,
    "decision": "RESEARCH_ONLY",
    "limitations": protocol["limitations"],
    "next_step": "Register a separate integration for the 73 supported missing payment dates, independently establish any restored labels before fitting, then rebuild, retrain and compare full accounts and conditional paper plans. Remaining sources, status/quote gaps, universe bias, ETF economic exposure, competition matching and forward observations remain open.",
}
target = base / "completion.json"
save(target, completion, immutable=True)
manifest["files"][target.relative_to(root).as_posix()] = sha(target)
save(paths.manifest, manifest)
print("SOURCE ROLE REVIEW ACCEPTED", sha(target), len(manifest["files"]), flush=True)
