"""Close the registered date integration gates without changing the active configuration."""

import json
import subprocess
from pathlib import Path

from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
pfile = root / "configs/protocols/distribution_availability.json"
p = read(pfile)
paths = Paths(root, p["configuration"])
checks = root / p["checks_output"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
changed = [name for name, value in source.items() if value != p["baseline_source_sha256"][name]]
assert sorted(changed) == ["__init__.py", "snapshots.py"]
assert sha(root / "configs/project.toml") == p["active_configuration_sha256"]
assert sha(root / p["current_plan"]["path"]) == p["current_plan"]["sha256"]
names = (
    "snapshot_review.json",
    "compatibility/source_review.json",
    "training_validation.json",
    "training_review.json",
    "account_review.json",
    "exposure_review.json",
    "settlement_review.json",
)
evidence = {}
for name in names:
    item = read(checks / name)
    assert item["status"] == "PASS" and item["source_sha256"] == source
    evidence[name] = sha(checks / name)
training = read(checks / "training_validation.json")
cohort = read(checks / "training_review.json")
accounts = read(checks / "account_review.json")
parity = read(checks / "compatibility/validation.json")
closure = read(checks / "compatibility/source_review.json")
assert closure["original_compatibility_sha256"] == sha(checks / "compatibility/validation.json")
assert closure["final_source_models_reloaded"] == 48
snapshot = read(checks / "snapshot_review.json")
fixed = read(root / p["fixed_forecast_output"] / "summary.json")
attribution = read(checks / "attribution.json")
assert fixed["status"] == "PASS" and fixed["source_sha256"] == source
assert attribution["status"] == "COMPLETE" and attribution["source_sha256"] == source
assert (
    training["fits"]
    == training["models_reloaded"]
    == cohort["models_checked"]
    == parity["models_reloaded"]
    == 48
)
assert training["account_audits"] == fixed["accounts"] == parity["full_accounts_exact"] == 1425
assert training["paper"]["accounts"] == parity["paper_accounts_exact"] == 75
assert cohort["warning_reference_occurrences"] == cohort["warning_fit_occurrences"] == 0
assert cohort["unknown_industry_training_rows"] == cohort["warning_inner_buys"] == 0
assert cohort["expected_training_stages_matched"] == 24
assert all(item["warning_buy_occurrences"] == 0 for item in accounts["arms"].values())
assert snapshot["all_non_label_inputs_exact"] and snapshot["existing_facts_and_reviews_preserved"]
evidence["attribution.json"] = sha(checks / "attribution.json")
evidence["fixed_forecast_summary"] = sha(root / p["fixed_forecast_output"] / "summary.json")
baseline = json.loads(
    subprocess.check_output(
        ["git", "show", p["baseline_commit"] + ":configs/integrity_manifest.json"], encoding="utf-8"
    )
)
manifest = read(paths.manifest)
assert len(baseline["files"]) == p["baseline_frozen_files"] == 32700
assert all(manifest["files"][name] == expected for name, expected in baseline["files"].items())
# The completed ordinary paper gate hashes the complete manifest before replaying accounts.
# Recheck the original paths here as a distinct final preservation receipt.
for name, expected in baseline["files"].items():
    assert sha(root / name) == expected, name
save(
    checks / "baseline_integrity.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "files": len(baseline["files"]),
        "baseline_commit": p["baseline_commit"],
        "manifest_sha256": sha(paths.manifest),
        "scope": "Every pre-registration file was read and compared to its original SHA-256; old hashes were not rewritten.",
    },
    immutable=True,
)
evidence["baseline_integrity.json"] = sha(checks / "baseline_integrity.json")
status_protocol = read(root / "configs/protocols/historical_status_repair.json")
old_status = read(root / p["baseline_status_review"])
save(
    checks / "status_review.json",
    {
        "status": "REGISTERED_STATUS_REPAIR_VERIFIED",
        "blocking": False,
        "reviewed_at": now(),
        "source_bundle_sha256": sha(paths.bundle),
        "previous_review": p["baseline_status_review"],
        "previous_review_sha256": sha(root / p["baseline_status_review"]),
        "source_review": status_protocol["review"],
        "source_review_sha256": status_protocol["review_sha256"],
        "corrected_securities": old_status["corrected_securities"],
        "warning_sessions": old_status["warning_sessions"],
        "remaining_reviewed_warning_training_rows": 0,
        "remaining_reviewed_warning_buy_occurrences": 0,
        "models_reloaded": 48,
        "new_account_audits": 1425,
        "fixed_forecast_account_audits": 1425,
        "paper_accounts_exact": 75,
        "evidence": evidence,
        "scope": "Rechecks the ten sourced warning intervals across all fitted/reference cohorts and inner/outer buys. Six prior opening-status records and all eligibility inputs remain exact. Other historical status coverage is not certified.",
        "limitations": p["limitations"],
    },
    immutable=True,
)
save(
    checks / "validation.json",
    {
        **training,
        "completed_at": now(),
        "evidence": evidence,
        "baseline_frozen_files_preserved": 32700,
        "compatibility_models": 48,
        "compatibility_full_accounts": 1425,
        "compatibility_paper_accounts": 75,
        "fixed_forecast_accounts": 1425,
        "changed_runtime_modules": changed,
        "status_review_sha256": sha(checks / "status_review.json"),
        "superiority_assessment": "NOT_ESTABLISHED",
        "forward_observations_added": 0,
        "complete_economic_exposure_certified": False,
        "economic_exposure_upper_bound": None,
        "scope": "All registered data, model, account, settlement, status, exposure and paper gates passed; active inputs have not yet switched.",
    },
    immutable=True,
)
print("REGISTERED DATE INTEGRATION VALIDATED", flush=True)
