"""Freeze the tested parser checkpoint while leaving financial source acceptance open."""

import json
import subprocess
from pathlib import Path

from quantfolio.dataset import check_integrity
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd().resolve()
base = Path("artifacts/checks/distribution_residuals_2026-11-22")
protocol = read("configs/protocols/distribution_residuals.json")
scope = read(base / "reader_scope.json")
model = read(base / "model_guard/verification.json")
cells = read(base / "table_cell_closure.json")
tables = read(base / "candidate_table_review.json")
navigation = read(base / "dated_navigation_closure.json")
tests = (base / "full_checks.log").read_text(encoding="utf-8")
source = sha("src/quantfolio/distributions.py")
require(source == scope["extractor_sha256"] == cells["extractor_sha256"], "Checked reader changed")
require("772 passed, 1 skipped" in tests, "Complete test result absent")
require(model["status"] == "PASS" and model["models_reloaded"] == 48, "Model guard incomplete")
require(
    all(row["max_error"] == 0 and row["label_availability_pass"] for row in model["models"]),
    "Model replay differs",
)
require(
    scope["events_replayed"] == 1839 and scope["prior_fields_preserved"] == 1530,
    "Prior scope incomplete",
)
require(cells["source_regression_gate_passed"], "Recorded layout failures remain unexplained")
require(
    tables["field_status_counts"]
    == {"NO_GRID_EVIDENCE_NARRATIVE_REVIEW_REQUIRED": 60, "LABELLED_GRID_AGREES": 245},
    "Unexpected grid review",
)
require(
    navigation["metadata_amendments_and_empty_pages_unchanged"],
    "Navigation repair changed other evidence",
)
active = protocol["active_state"]
require(
    sha(active["configuration"]) == active["configuration_sha256"], "Active configuration changed"
)
for field in ("bundle", "plan"):
    require(sha(active[field]["path"]) == active[field]["sha256"], "Active data or plan changed")
for name, expected in active["source_sha256"].items():
    if name != "distributions.py":
        require(sha(Path("src/quantfolio") / name) == expected, (name, "Runtime changed"))
manifest = read("configs/integrity_manifest.json")
baseline = json.loads(
    subprocess.run(
        ["git", "show", protocol["baseline_commit"] + ":configs/integrity_manifest.json"],
        capture_output=True,
        check=True,
    ).stdout
)
require(len(baseline["files"]) == 38827, "Baseline inventory differs")
require(
    all(manifest["files"].get(path) == value for path, value in baseline["files"].items()),
    "Baseline manifest entries changed",
)
registered = [Path("configs/protocols/distribution_layouts.json")] + list(
    Path("configs/protocols").glob("residual_*.json")
)
for path in [p for p in base.rglob("*") if p.is_file()] + registered:
    relative, actual = path.as_posix(), sha(path)
    require(
        relative not in manifest["files"] or manifest["files"][relative] == actual,
        (relative, "Frozen bytes changed"),
    )
    manifest["files"][relative] = actual
save("configs/integrity_manifest.json", manifest)
integrity = check_integrity(Paths(root))
output = base / "reader_checkpoint.json"
save(
    output,
    {
        "status": "PARSER_VALIDATED_SOURCE_ACCEPTANCE_PENDING",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "extractor_sha256": source,
        "full_tests": {"passed": 772, "skipped": 1, "log_sha256": sha(base / "full_checks.log")},
        "model_guard": {
            "path": (base / "model_guard/verification.json").as_posix(),
            "sha256": sha(base / "model_guard/verification.json"),
            "models": 48,
            "max_prediction_error": 0,
        },
        "checks": {
            name: sha(base / name)
            for name in (
                "reader_scope.json",
                "table_cell_closure.json",
                "candidate_table_review.json",
                "dated_navigation_closure.json",
            )
        },
        "integrity": integrity,
        "baseline_files_preserved": len(baseline["files"]),
        "prior_accepted_events": 1452,
        "prior_accepted_fields": 1530,
        "proposed_events_not_accepted": 95,
        "proposed_fields_not_accepted": 115,
        "out_of_scope_listing_proposals_not_accepted": 8,
        "active_source_unverified_events": 387,
        "active_data_numerical_modules_and_plan_unchanged": True,
        "facts_accepted": 0,
        "facts_applied": False,
        "source_phase_complete": False,
        "whole_goal_complete": False,
        "remaining": [
            "Independent narrative/current-cash and companion-role checks for all proposals",
            "Received amendment/archive review and explicit outcomes for all residuals",
            "Separate acceptance, integration and full retraining/account attribution if supported facts are applied",
            "The remaining complete remediation plan in docs/remediation.md",
        ],
    },
    immutable=True,
)
manifest["files"][output.as_posix()] = sha(output)
save("configs/integrity_manifest.json", manifest)
print(
    "PARSER_CHECKPOINT_VALIDATED",
    integrity["files"],
    "frozen files checked; source acceptance pending",
)
