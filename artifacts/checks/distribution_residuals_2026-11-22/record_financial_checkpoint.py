"""Record a source-only checkpoint without accepting or integrating new fields."""

import hashlib
import json
import subprocess
from pathlib import Path

from quantfolio.dataset import check_integrity
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
BASELINE = "c94ac2bda43b840d41b245ae4a3835a460306aa7"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git(*args):
    return subprocess.check_output(["git", *args])


protocol = read("configs/protocols/distribution_residuals.json")
financial = read(BASE / "financial_source_review.json")
tests = (BASE / "financial_checks.log").read_text(encoding="utf-8")
require("772 passed, 1 skipped" in tests, "Full tests incomplete")
require(financial["gross_cash_events_checked"] == 95, "Primary scope incomplete")
require(financial["gross_cash_reader_checks"] == 190, "Cash checks incomplete")
require(len(financial["date_field_checks"]) == 305, "Date checks incomplete")
require(len(financial["role_bindings"]) == 136, "Identity scope incomplete")
require(len(financial["whole_pages"]) == 46, "Visual scope incomplete")
require(
    financial["facts_accepted"] == 0 and not financial["source_phase_complete"],
    "Acceptance boundary changed",
)
require(
    financial["checker_sha256"] == sha(BASE / "verify_financial_sources.py"),
    "Final checker changed",
)
require(
    financial["cash_scope_review_sha256"] == sha(BASE / "financial_scope_review.json"),
    "Cash evidence changed",
)
require(
    financial["page_notes_sha256"] == sha(BASE / "financial_page_notes.json"),
    "Observed notes changed",
)
source_files = git("ls-tree", "-r", "--name-only", BASELINE, "src").decode().splitlines()
require(
    set(source_files) == {p.as_posix() for p in Path("src").rglob("*") if p.is_file()},
    "Runtime file inventory changed",
)
runtime = {}
for path in source_files:
    expected = hashlib.sha256(git("show", f"{BASELINE}:{path}")).hexdigest()
    require(sha(path) == expected, "Runtime bytes changed: " + path)
    runtime[path] = expected
active = protocol["active_state"]
require(sha(active["configuration"]) == active["configuration_sha256"], "Active config changed")
for field in ("bundle", "plan"):
    require(sha(active[field]["path"]) == active[field]["sha256"], "Active state changed: " + field)
baseline = json.loads(git("show", f"{BASELINE}:configs/integrity_manifest.json"))
manifest = read("configs/integrity_manifest.json")
require(
    all(manifest["files"].get(p) == h for p, h in baseline["files"].items()),
    "Baseline manifest changed",
)
paths = [p for p in BASE.rglob("*") if p.is_file()] + [
    Path("configs/protocols/residual_narrative_review.json"),
    Path("configs/protocols/residual_financial_scope.json"),
]
for path in paths:
    key, actual = path.as_posix(), sha(path)
    require(
        key not in manifest["files"] or manifest["files"][key] == actual,
        "Frozen file changed: " + key,
    )
    manifest["files"][key] = actual
save("configs/integrity_manifest.json", manifest)
integrity = check_integrity(Paths(Path.cwd().resolve()))
model = read(BASE / "model_guard/verification.json")
require(model["status"] == "PASS" and model["models_reloaded"] == 48, "Reference guard missing")
require(all(row["max_error"] == 0 for row in model["models"]), "Reference prediction mismatch")
output = BASE / "financial_checkpoint.json"
save(
    output,
    {
        "status": "PRIMARY_FIELD_CHECKPOINT_RELATED_SOURCE_ACCEPTANCE_PENDING",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "baseline_commit": BASELINE,
        "baseline_frozen_files_preserved": len(baseline["files"]),
        "integrity": integrity,
        "runtime_files_unchanged": runtime,
        "active_configuration_bundle_and_plan_unchanged": True,
        "full_tests": {
            "passed": 772,
            "skipped": 1,
            "log_sha256": sha(BASE / "financial_checks.log"),
        },
        "prior_reference_guard": {
            "path": (BASE / "model_guard/verification.json").as_posix(),
            "sha256": sha(BASE / "model_guard/verification.json"),
            "models": 48,
            "max_prediction_error": 0,
            "reused_under_identical_runtime_and_frozen_inputs": True,
            "new_fits_in_this_phase": 0,
        },
        "primary_financial_review_sha256": sha(BASE / "financial_source_review.json"),
        "facts_accepted": 0,
        "facts_applied": 0,
        "source_unverified_events": 387,
        "source_phase_complete": False,
        "whole_goal_complete": False,
        "remaining": [
            "Same-event and contradictory-clause review of all price companions",
            "All 1135 received related notices and explicit outcomes for all 387 residual events",
            "Source acceptance followed by separately registered integration and full retraining/account attribution",
            "The complete remaining remediation plan in docs/remediation.md",
        ],
    },
    immutable=True,
)
manifest["files"][output.as_posix()] = sha(output)
save("configs/integrity_manifest.json", manifest)
print(
    "FINANCIAL_CHECKPOINT",
    "baseline_files",
    len(baseline["files"]),
    "total_frozen",
    len(manifest["files"]),
    "runtime_files",
    len(runtime),
    "facts_accepted",
    0,
    flush=True,
)
