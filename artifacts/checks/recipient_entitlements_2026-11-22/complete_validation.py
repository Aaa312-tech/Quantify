"""Close all entitlement integration gates before changing the active configuration."""

import json
import subprocess
from pathlib import Path

from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
paths = Paths(root, p["configuration"])
checks = root / p["checks_output"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
changed = [name for name, value in source.items() if value != p["baseline_source_sha256"][name]]
if not sorted(changed) == ["__init__.py", "snapshots.py"]:
    raise ValueError("Audit condition failed: sorted(changed) == ['__init__.py', 'snapshots.py']")
if not sha(root / "configs/project.toml") == p["active_configuration_sha256"]:
    raise ValueError(
        "Audit condition failed: sha(root / 'configs/project.toml') == p['active_configuration_sha256']"
    )
if not sha(root / p["current_plan"]["path"]) == p["current_plan"]["sha256"]:
    raise ValueError(
        "Audit condition failed: sha(root / p['current_plan']['path']) == p['current_plan']['sha256']"
    )
names = (
    "numeric_acceptance.json",
    "compatibility/validation.json",
    "training_validation.json",
    "training_review.json",
    "account_review.json",
    "exposure_review.json",
    "settlement_review.json",
)
evidence = {}
for name in names:
    item = read(checks / name)
    if not (item["status"] == "PASS" and item["source_sha256"] == source):
        raise ValueError(
            "Audit condition failed: item['status'] == 'PASS' and item['source_sha256'] == source"
        )
    evidence[name] = sha(checks / name)
training = read(checks / "training_validation.json")
cohort = read(checks / "training_review.json")
accounts = read(checks / "account_review.json")
parity = read(checks / "compatibility/validation.json")
snapshot = read(checks / "numeric_acceptance.json")
fixed = read(root / p["fixed_forecast_output"] / "summary.json")
attribution = read(checks / "attribution.json")
if not (fixed["status"] == "PASS" and fixed["source_sha256"] == source):
    raise ValueError(
        "Audit condition failed: fixed['status'] == 'PASS' and fixed['source_sha256'] == source"
    )
if not (attribution["status"] == "COMPLETE" and attribution["source_sha256"] == source):
    raise ValueError(
        "Audit condition failed: attribution['status'] == 'COMPLETE' and attribution['source_sha256'] == source"
    )
if (
    not training["fits"]
    == training["models_reloaded"]
    == cohort["models_checked"]
    == parity["models_reloaded"]
    == 48
):
    raise ValueError(
        "Audit condition failed: training['fits'] == training['models_reloaded'] == cohort['models_checked'] == parity['models_reloaded'] == 48"
    )
if not training["account_audits"] == fixed["accounts"] == parity["full_accounts_exact"] == 1425:
    raise ValueError(
        "Audit condition failed: training['account_audits'] == fixed['accounts'] == parity['full_accounts_exact'] == 1425"
    )
if not training["paper"]["accounts"] == parity["paper_accounts_exact"] == 75:
    raise ValueError(
        "Audit condition failed: training['paper']['accounts'] == parity['paper_accounts_exact'] == 75"
    )
if not cohort["warning_reference_occurrences"] == cohort["warning_fit_occurrences"] == 0:
    raise ValueError(
        "Audit condition failed: cohort['warning_reference_occurrences'] == cohort['warning_fit_occurrences'] == 0"
    )
if not cohort["unknown_industry_training_rows"] == cohort["warning_inner_buys"] == 0:
    raise ValueError(
        "Audit condition failed: cohort['unknown_industry_training_rows'] == cohort['warning_inner_buys'] == 0"
    )
if not cohort["expected_training_stages_matched"] == 24:
    raise ValueError("Audit condition failed: cohort['expected_training_stages_matched'] == 24")
if not all((item["warning_buy_occurrences"] == 0 for item in accounts["arms"].values())):
    raise ValueError(
        "Audit condition failed: all((item['warning_buy_occurrences'] == 0 for item in accounts['arms'].values()))"
    )
if not snapshot["raw_calendar_metadata_status_eligibility_exact"]:
    raise ValueError(
        "Audit condition failed: snapshot['raw_calendar_metadata_status_eligibility_exact']"
    )
if not snapshot["all_values_outside_independently_predicted_changes_exact"]:
    raise ValueError(
        "Audit condition failed: snapshot['all_values_outside_independently_predicted_changes_exact']"
    )
if not snapshot[
    "full_feature_panels_scores_and_cross_section_ranks_match_independent_expectations"
]:
    raise ValueError(
        "Audit condition failed: snapshot['full_feature_panels_scores_and_cross_section_ranks_match_independent_expectations']"
    )
if (
    not snapshot["changed_label_tuples"]
    == read(checks / "label_scope.json")["expected_changed_tuples"]
):
    raise ValueError(
        "Audit condition failed: snapshot['changed_label_tuples'] == read(checks / 'label_scope.json')['expected_changed_tuples']"
    )
if not snapshot["changed_fields"] == {
    k: v for k, v in p["expected_changes"].items() if k != "events"
}:
    raise ValueError(
        "Audit condition failed: snapshot['changed_fields'] == {k: v for k, v in p['expected_changes'].items() if k != 'events'}"
    )
action_scope = read(checks / "action_scope.json")
if not action_scope["status"] == "PASS_ACTION_SCOPE_ONLY":
    raise ValueError("Audit condition failed: action_scope['status'] == 'PASS_ACTION_SCOPE_ONLY'")
if not action_scope["unscoped_actions_and_reviews_exact"] == 1808:
    raise ValueError(
        "Audit condition failed: action_scope['unscoped_actions_and_reviews_exact'] == 1808"
    )
if not action_scope["original_cash_binary64_preserved"] == 28:
    raise ValueError(
        "Audit condition failed: action_scope['original_cash_binary64_preserved'] == 28"
    )
if not action_scope["actions_sha256"] == snapshot["actions_sha256"]:
    raise ValueError(
        "Audit condition failed: action_scope['actions_sha256'] == snapshot['actions_sha256']"
    )
implementation = read(checks / "implementation.json")
if not (implementation["status"] == "PASS" and implementation["source_sha256"] == source):
    raise ValueError(
        "Audit condition failed: implementation['status'] == 'PASS' and implementation['source_sha256'] == source"
    )
if not implementation["old_operation_complete_ast_exact_after_specialization"]:
    raise ValueError(
        "Audit condition failed: implementation['old_operation_complete_ast_exact_after_specialization']"
    )
if not implementation["all_feature_label_model_execution_risk_and_planning_modules_byte_identical"]:
    raise ValueError(
        "Audit condition failed: implementation['all_feature_label_model_execution_risk_and_planning_modules_byte_identical']"
    )
if not read(checks / "local_validation.json")["passed"] == 736:
    raise ValueError(
        "Audit condition failed: read(checks / 'local_validation.json')['passed'] == 736"
    )
for name in (
    "implementation.json",
    "action_scope.json",
    "label_scope.json",
    "feature_expectations.json",
    "target_expectations.json",
):
    evidence[name] = sha(checks / name)
evidence["attribution.json"] = sha(checks / "attribution.json")
effects = read(checks / "forecast_effects_review.json")
if effects["status"] != "PASS" or effects["attribution_sha256"] != evidence["attribution.json"]:
    raise ValueError("Account component attribution is incomplete or stale")
if effects["evaluation_sha256"] != training["evaluation_sha256"]:
    raise ValueError("Component attribution refers to a different evaluation")
evidence["forecast_effects_review.json"] = sha(checks / "forecast_effects_review.json")
evidence["fixed_forecast_summary"] = sha(root / p["fixed_forecast_output"] / "summary.json")
baseline = json.loads(
    subprocess.check_output(
        ["git", "show", p["baseline_commit"] + ":configs/integrity_manifest.json"], encoding="utf-8"
    )
)
manifest = read(paths.manifest)
if not len(baseline["files"]) == p["baseline_frozen_files"] == 37730:
    raise ValueError(
        "Audit condition failed: len(baseline['files']) == p['baseline_frozen_files'] == 37730"
    )
if not all((manifest["files"][name] == expected for name, expected in baseline["files"].items())):
    raise ValueError(
        "Audit condition failed: all((manifest['files'][name] == expected for name, expected in baseline['files'].items()))"
    )
paper_file = checks / "paper/verification.json"
paper = read(paper_file)
if not (paper["status"] == "PASS" and paper["integrity"]["status"] == "PASS"):
    raise ValueError(
        "Audit condition failed: paper['status'] == 'PASS' and paper['integrity']['status'] == 'PASS'"
    )
if not paper["integrity"] == training["paper"]["integrity"]:
    raise ValueError("Audit condition failed: paper['integrity'] == training['paper']['integrity']")
if not paper["integrity"]["files"] == len(manifest["files"]):
    raise ValueError(
        "Audit condition failed: paper['integrity']['files'] == len(manifest['files'])"
    )
save(
    checks / "baseline_integrity.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "files": len(baseline["files"]),
        "baseline_commit": p["baseline_commit"],
        "manifest_sha256": sha(paths.manifest),
        "physical_hash_receipt": paper_file.relative_to(root).as_posix(),
        "physical_hash_receipt_sha256": sha(paper_file),
        "scope": "The ordinary paper guard read every file in the complete manifest and checked its SHA-256. All 37730 baseline paths retain their original registered hashes. This receipt binds that completed physical check rather than duplicating its reads; final publication integrity is checked again after new evidence is frozen.",
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
        "baseline_frozen_files_preserved": 37730,
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
print("REGISTERED ENTITLEMENT INTEGRATION VALIDATED", flush=True)
