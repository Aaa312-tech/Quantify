"""Close all registered engineering checks; do not infer investment superiority."""

from pathlib import Path

from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol_file = root / "configs/protocols/etf_integration.json"
p = read(protocol_file)
checks = root / p["checks_output"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
snapshot = read(checks / "snapshot_review.json")
parity = read(checks / "implementation/validation.json")
baseline = read(checks / "baseline_integrity.json")
exposure = read(checks / "reported/exposure_review.json")
assert all(item["status"] == "PASS" for item in (snapshot, parity, baseline, exposure))
assert source == snapshot["source_sha256"] == parity["source_sha256"] == exposure["source_sha256"]
assert baseline["files"] == p["baseline_frozen_files"] == 29762
status_protocol = read(root / "configs/protocols/historical_status_repair.json")
for variant, setting in p["variants"].items():
    folder = root / setting["checks_output"]
    training = read(folder / "training_validation.json")
    cohort = read(folder / "training_review.json")
    accounts = read(folder / "account_review.json")
    fixed = read(root / setting["fixed_forecast_output"] / "summary.json")
    assert all(item["status"] == "PASS" for item in (training, cohort, accounts, fixed))
    assert (
        source
        == training["source_sha256"]
        == cohort["source_sha256"]
        == accounts["source_sha256"]
        == fixed["source_sha256"]
    )
    assert training["fits"] == training["models_reloaded"] == cohort["models_checked"] == 48
    assert (
        training["account_audits"] == fixed["accounts"] == 1425
        and training["paper"]["accounts"] == 75
    )
    assert cohort["warning_reference_occurrences"] == cohort["warning_fit_occurrences"] == 0
    assert cohort["unknown_industry_training_rows"] == cohort["warning_inner_buys"] == 0
    assert all(item["warning_buy_occurrences"] == 0 for item in accounts["arms"].values())
    evidence = {
        name: sha(folder / name)
        for name in ("training_validation.json", "training_review.json", "account_review.json")
    }
    evidence["fixed_forecasts_summary"] = sha(
        root / setting["fixed_forecast_output"] / "summary.json"
    )
    save(
        folder / "validation.json",
        {
            **training,
            "completed_at": now(),
            "fixed_forecast_accounts": 1425,
            "snapshot_review_sha256": sha(checks / "snapshot_review.json"),
            "baseline_parity_sha256": sha(checks / "implementation/validation.json"),
            "evidence": evidence,
            "scope": "All registered data, training, independent account, historical-status and paper checks passed. Return and complete-economic-exposure claims remain separate.",
            "complete_economic_exposure_certified": False,
            "economic_exposure_upper_bound": None,
        },
        immutable=True,
    )
    save(
        folder / "status_review.json",
        {
            "status": "REGISTERED_STATUS_REPAIR_VERIFIED",
            "blocking": False,
            "reviewed_at": now(),
            "source_bundle_sha256": training["data_sha256"],
            "variant": variant,
            "registration_commit": "f6b07fc8c590f131d59c8681c8a5d5e8206ad06e",
            "previous_review": paths.config["historical_status_review"],
            "previous_review_sha256": sha(root / paths.config["historical_status_review"]),
            "source_review": status_protocol["review"],
            "source_review_sha256": status_protocol["review_sha256"],
            "corrected_securities": 10,
            "warning_sessions": 2755,
            "remaining_reviewed_warning_training_rows": 0,
            "remaining_reviewed_warning_buy_occurrences": 0,
            "models_reloaded": 48,
            "new_account_audits": 1425,
            "fixed_forecast_account_audits": 1425,
            "paper_accounts_exact": 75,
            "evidence": evidence,
            "scope": "Rechecks the ten previously sourced warning intervals in every fitted/reference cohort and all inner/outer buys. Other security status coverage is not certified.",
            "limitations": p["limitations"],
        },
        immutable=True,
    )
print("ALL THREE CONTROL VALIDATIONS CLOSED", flush=True)
