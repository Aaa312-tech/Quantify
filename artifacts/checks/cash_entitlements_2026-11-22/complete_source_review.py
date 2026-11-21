"""Freeze the accepted source-only batch after local and model validation."""

import json
import re
import subprocess
from pathlib import Path

from quantfolio.dataset import check_integrity
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd()
base = Path(__file__).resolve().parent
paths = Paths(root)
protocol_path = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_path)
accepted = read(base / "field_acceptance.json")
require(accepted["status"] == "FIELD_SOURCE_ACCEPTANCE_PASSED", "Field acceptance missing")
require(
    accepted["source_review_sha256"] == sha(base / "source_review.json"), "Source review changed"
)
models_path = base / "active_models/verification.json"
models = read(models_path)
require(models["status"] == "PASS" and models["models_reloaded"] == 48, "Model reload incomplete")
require(models["fresh_refit"] is None and len(models["models"]) == 48, "Unexpected fit inventory")
require(
    all(r["max_error"] == 0 and r["label_availability_pass"] for r in models["models"]),
    "Model parity failed",
)
test_log = base / "tests.log"
tests = test_log.read_text(encoding="utf-8")
matches = re.findall(r"(\d+) passed, (\d+) skipped in", tests)
require(matches == [("687", "1")], "Local test conclusion differs")
require("Host does not permit directory symlinks" in tests, "Unexpected skip")
state = protocol["active_state"]
require(
    sha(root / state["configuration"]) == state["configuration_sha256"], "Active config changed"
)
for name in ("bundle", "plan"):
    require(sha(root / state[name]["path"]) == state[name]["sha256"], name)
require(models["data_sha256"] == state["bundle"]["sha256"], "Reload used a different bundle")
for name, expected in state["source_sha256"].items():
    require(sha(root / "src/quantfolio" / name) == expected, name)
baseline = json.loads(
    subprocess.check_output(
        [
            "git",
            "show",
            protocol["baseline_commit"] + ":configs/integrity_manifest.json",
        ]
    ).decode("utf-8")
)
require(len(baseline["files"]) == protocol["baseline_frozen_files"] == 37392, "Baseline count")
manifest = read(paths.manifest)
for name, expected in baseline["files"].items():
    require(manifest["files"].get(name) == expected, "Frozen baseline identity changed")
added = {}
for path in sorted(base.rglob("*")):
    if (
        not path.is_file()
        or path.suffix == ".pyc"
        or any(part in {"__pycache__", ".pytest_cache"} for part in path.parts)
    ):
        continue
    name, value = path.relative_to(root).as_posix(), sha(path)
    if name in manifest["files"]:
        require(manifest["files"][name] == value, f"Executed evidence changed: {name}")
    else:
        added[name] = value
manifest["files"].update(added)
save(paths.manifest, manifest)
integrity = check_integrity(paths)
completion = {
    "status": "SOURCE_REVIEW_VALIDATED_LOCALLY",
    "completed_at": now(),
    "script_sha256": sha(Path(__file__)),
    "protocol_sha256": sha(protocol_path),
    "production_protocol_sha256": sha(root / "configs/protocols/cash_entitlements.json"),
    "field_acceptance_sha256": sha(base / "field_acceptance.json"),
    "source_review_sha256": sha(base / "source_review.json"),
    "source_sha256": state["source_sha256"],
    "active_state": state,
    "local_tests": {
        "passed": 687,
        "skipped": 1,
        "reason": "Host directory symlink permission",
        "log_sha256": sha(test_log),
    },
    "model_reloads": 48,
    "model_max_error": 0,
    "model_verification_sha256": sha(models_path),
    "fresh_fits": 0,
    "baseline_frozen_files_preserved": 37392,
    "registered_inputs_before_review": 37394,
    "integrity_before_completion": integrity,
    "events_checked": 1839,
    "unscoped_records_exact": 1808,
    "prior_supported_fields_preserved": 1448,
    "new_supported_fields": {
        "cash_per_share": 31,
        "cash_pay_date": 31,
        "ex_reference_cash_per_share": 19,
        "share_list_date": 1,
    },
    "supported_events": 1452,
    "supported_fields": 1530,
    "unverified_events": 387,
    "primary_text_pages": 124,
    "primary_images_actually_inspected": 107,
    "received_issuer_documents": 110,
    "received_issuer_text_pages": 381,
    "related_images_actually_inspected": 39,
    "initial_primary_checks": 436,
    "initial_primary_failures_preserved": 5,
    "explained_primary_check_failures": 4,
    "unresolved_primary_source_aggregates": 1,
    "related_context_checks": 291,
    "preparation_failure_preserved": "field_preparation_failure.json",
    "prior_evidence_import_sha256": sha(base / "prior_reviews/publication_import.json"),
    "cash_corrections_required": 3,
    "stored_cash_values_to_preserve": 28,
    "runtime_inputs_changed": False,
    "facts_applied": False,
    "historical_results_unchanged": True,
    "whole_goal_complete": False,
    "next_step": "Publish and verify this source batch, then separately register economic integration. Independently rebuild actions/features/ranks/labels before full refitting and fixed-forecast/account/paper comparisons. Preserve existing facts, source conflicts, risk constraints and exhausted candidate budget; no all-label-equal assumption or return-based source selection.",
}
save(base / "completion.json", completion, immutable=True)
manifest["files"][(base / "completion.json").relative_to(root).as_posix()] = sha(
    base / "completion.json"
)
save(paths.manifest, manifest)
print(
    "SOURCE_REVIEW_VALIDATED_LOCALLY",
    "FROZEN_FILES",
    len(manifest["files"]),
    "TESTS",
    687,
    "MODELS",
    48,
)
