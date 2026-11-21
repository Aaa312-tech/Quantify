"""Bind source-field acceptance to preserved data, models, code and page reviews."""

import ast
import hashlib
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
base = root / "artifacts/checks/distribution_terms_2026-11-21"
initial = read(root / "configs/protocols/distribution_terms.json")
protocol_file = root / "configs/protocols/distribution_approved_wording.json"
protocol = read(protocol_file)
review_file = root / protocol["review"]
review = read(review_file)
previous_file = root / initial["previous_review"]["path"]
previous = read(previous_file)
key = lambda r: (r["symbol"], r["ex_date"])
old = {key(r): r for r in previous["facts"]}
new = {key(r): r for r in review["facts"]}
assert len(old) == 1119 and len(new) == 1282
assert {key(r): r["original_action_sha256"] for r in previous["records"]} == {
    key(r): r["original_action_sha256"] for r in review["records"]
}
for identity, record in old.items():
    for field, value in record["verified_fields"].items():
        assert new[identity]["verified_fields"][field] == value
added = {
    identity: {
        f: v
        for f, v in r["verified_fields"].items()
        if f not in old.get(identity, {}).get("verified_fields", {})
    }
    for identity, r in new.items()
}
added = {k: v for k, v in added.items() if v}
independent = read(base / "independent_closure.json")
assert independent["status"] == "PASS" and independent["failed_events"] == 0
assert independent["source_review_sha256"] == sha(review_file)
assert independent["checker_sha256"] == sha(base / "independent_fields.py")
assert independent["checks"] == 677 and independent["events"] == 165
assert {key(r): r["added_fields"] for r in independent["records"] if r["added_fields"]} == added
assert all(r["passed"] for r in independent["records"])
assert (
    sha(base / "independent_initial.py") == read(base / "independent_fields.json")["checker_sha256"]
)
visual = read(base / "visual_review.json")
assert visual["status"] == "PASS" and visual["families"] == 14
assert visual["total_unique_relevant_pages"] == 29
assert len(visual["independent_mismatches"]) == 11
assert read(base / "approved_wording/preservation.json")["status"] == "PASS"
assert read(base / "scope_review/preservation.json")["status"] == "FAIL"
assert read(base / "preservation_failure.json")["status"] == "FAILED_PRIOR_FIELD_PRESERVATION"

source = {f.name: sha(f) for f in (root / "src/quantfolio").glob("*.py")}
before = initial["active_state"]["source_sha256"]
assert source.keys() == before.keys()
assert {name for name in source if source[name] != before[name]} == {
    "__init__.py",
    "distributions.py",
}
assert sha(root / "src/quantfolio/distributions.py") == sha(base / "approved_wording/extractor.py")
assert review["extractor_sha256"] == source["distributions.py"]


def stable_ast(path):
    module = ast.parse(path.read_text(encoding="utf-8"))
    excluded = {"_date", "_current_scheme", "_current_payout", "extract_terms"}
    nodes = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in excluded:
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in {"DATE", "EX_LABEL"}
            for target in node.targets
        ):
            continue
        nodes.append(ast.dump(node, include_attributes=False))
    return nodes


assert stable_ast(base / "before_distributions.py") == stable_ast(
    root / "src/quantfolio/distributions.py"
)
state = initial["active_state"]
assert sha(paths.configuration_path) == state["configuration_sha256"]
assert sha(root / state["bundle"]) == state["bundle_sha256"]
assert sha(root / state["plan"]) == state["plan_sha256"]
plan = read(root / state["plan"])
assert plan["source_sha256"] == source_hashes()
for relative, expected in plan["input_sha256"].items():
    assert sha(root / relative) == expected
assert len(plan["orders"]) == 0
models = read(base / "models/verification.json")
assert models["status"] == "PASS" and models["models_reloaded"] == 48
assert all(row["max_error"] == 0 for row in models["models"])
assert "435 passed, 1 skipped" in (base / "scope_validation.log").read_text(encoding="utf-8")

bundle = joblib.load(root / state["bundle"])
active_actions = {key(r): r for r in bundle["actions"]}
missing, present = Counter(), Counter()
for identity, fields in added.items():
    for field, value in fields.items():
        actual = active_actions[identity].get(field)
        assert actual in (None, value)
        (missing if actual is None else present)[field] += 1
assert dict(missing) == {"cash_pay_date": 154, "share_list_date": 16}

baseline_raw = subprocess.check_output(
    ["git", "show", initial["baseline_commit"] + ":configs/integrity_manifest.json"]
)
assert hashlib.sha256(baseline_raw).hexdigest() == initial["baseline_manifest_sha256"]
baseline = json.loads(baseline_raw)["files"]
assert len(baseline) == 32628
manifest = read(paths.manifest)
assert all(manifest["files"].get(k) == v for k, v in baseline.items())
for file in base.rglob("*"):
    if not file.is_file() or file.suffix == ".pyc" or "__pycache__" in file.parts:
        continue
    relative = file.relative_to(root).as_posix()
    value = sha(file)
    assert relative not in manifest["files"] or manifest["files"][relative] == value
    manifest["files"][relative] = value
save(paths.manifest, manifest)
integrity = check_integrity(paths)
completion = {
    "status": "SOURCE_REVIEW_ACCEPTED_WITH_UNRESOLVED_EVENTS",
    "completed_at": now(),
    "protocol": protocol_file.relative_to(root).as_posix(),
    "protocol_sha256": sha(protocol_file),
    "source_review": review_file.relative_to(root).as_posix(),
    "source_review_sha256": sha(review_file),
    "inventory_events": 1839,
    "previous_verified_events": len(old),
    "verified_events": len(new),
    "previous_facts_preserved": True,
    "unresolved_events": 1839 - len(new),
    "status_counts": review["counts"],
    "added_events": len(added),
    "added_fields": dict(Counter(f for fields in added.values() for f in fields)),
    "independent_fields": {
        "path": (base / "independent_closure.json").relative_to(root).as_posix(),
        "sha256": sha(base / "independent_closure.json"),
        "checks": 677,
        "events": 165,
    },
    "visual_review": {"sha256": sha(base / "visual_review.json"), "pages": 29, "families": 14},
    "active_snapshot_missing_verified_fields": dict(missing),
    "active_snapshot_already_known_fields": dict(present),
    "baseline_frozen_files_preserved": len(baseline),
    "integrity": integrity,
    "source_sha256": source,
    "changed_runtime_modules": ["__init__.py", "distributions.py"],
    "other_distribution_code_ast_unchanged": True,
    "active_configuration_sha256": state["configuration_sha256"],
    "active_bundle_sha256": state["bundle_sha256"],
    "active_plan_sha256": state["plan_sha256"],
    "plan_source_and_input_guards_unchanged": True,
    "model_reloads": 48,
    "prediction_max_error": 0.0,
    "local_tests": {"passed": 435, "skipped": 1, "log_sha256": sha(base / "scope_validation.log")},
    "facts_applied": False,
    "historical_returns_changed": False,
    "paper_orders_changed": False,
    "publication_status": "PENDING_COMMIT_PUSH_AND_ACTUAL_CI",
    "next_phase": "Register applying the accepted missing fields to the active ETF snapshot, then rebuild, fit and independently verify all account controls before activation.",
    "limitations": initial["limitations"],
}
save(base / "completion.json", completion, immutable=True)
manifest = read(paths.manifest)
manifest["files"][(base / "completion.json").relative_to(root).as_posix()] = sha(
    base / "completion.json"
)
save(paths.manifest, manifest)
print(
    {
        k: completion[k]
        for k in [
            "status",
            "verified_events",
            "added_events",
            "added_fields",
            "unresolved_events",
            "facts_applied",
        ]
    },
    flush=True,
)
