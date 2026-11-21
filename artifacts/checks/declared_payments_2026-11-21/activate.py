"""Adopt independently verified date facts after the registered integration gates pass."""

from pathlib import Path

from quantfolio.planning import freeze_plan, source_hashes
from quantfolio.reporting import build_report
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha

root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/declared_payments.json"
p = read(pfile)
checks = root / p["checks_output"]
validation = read(checks / "validation.json")
attribution = read(checks / "attribution.json")
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert validation["status"] == "PASS" and attribution["status"] == "COMPLETE"
assert source == validation["source_sha256"] == attribution["source_sha256"]
assert validation["baseline_frozen_files_preserved"] == 35158
assert validation["fits"] == validation["models_reloaded"] == 48
assert validation["account_audits"] == validation["fixed_forecast_accounts"] == 1425
assert validation["paper"]["accounts"] == 75
assert paths.config["bundle"] == p["source_bundle"]
assert sha(paths.bundle) == p["source_bundle_sha256"]
assert sha(paths.configuration_path) == p["active_configuration_sha256"]
assert sha(root / p["current_plan"]["path"]) == p["current_plan"]["sha256"]


def freeze_outputs():
    manifest = read(paths.manifest)
    folders = [checks] + [
        root / p[key]
        for key in (
            "snapshot_output",
            "training_output",
            "evaluation_output",
            "fixed_forecast_output",
            "compatibility_output",
        )
    ]
    for folder in folders:
        for file in sorted(folder.rglob("*")):
            if (
                not file.is_file()
                or file.suffix == ".pyc"
                or any(part in {"__pycache__", ".pytest_cache"} for part in file.parts)
            ):
                continue
            relative, value = file.relative_to(root).as_posix(), sha(file)
            assert relative not in manifest["files"] or manifest["files"][relative] == value, (
                relative
            )
            manifest["files"][relative] = value
    save(paths.manifest, manifest)


freeze_outputs()
paths.configuration_path.write_bytes((root / p["configuration"]).read_bytes())
paths = Paths(root)
folder = root / "artifacts/plans/declared_payments_2026-11-23"
freeze_plan(paths, "artifacts/accounts/forward_2026-11-23/account.json", folder)
plan_file = folder / "plan.json"
plan = read(plan_file)
assert plan["status"] == "FROZEN_CONDITIONAL_PLAN" and not plan["blockers"]
assert plan["source_sha256"] == source_hashes()
assert plan["active_configuration"] == paths.config
assert plan["identity"] == digest({key: value for key, value in plan.items() if key != "identity"})
assert plan["signal_date"] == "2026-11-20" and plan["execution_date"] == "2026-11-23"
assert plan["intent"]["fund_mode"] == "reported"
signal = attribution["latest_signal"]
assert (
    plan["input_sha256"][p["training_output"] + "/final_fit/outer_predictions.npz"]
    == signal["forecast_sha256"]
)
for name, value in plan["input_sha256"].items():
    assert sha(root / name) == value, name
if signal["cost_scenarios"]["10bps"]["passes_new_buy_edge_filter"] == 0:
    assert not plan["orders"]
save(
    checks / "activation.json",
    {
        "status": "ACTIVATED_FOR_RESEARCH",
        "activated_at": now(),
        "configuration_sha256": sha(paths.configuration_path),
        "previous_configuration_sha256": p["active_configuration_sha256"],
        "source_sha256": source,
        "protocol_sha256": sha(pfile),
        "validation_sha256": sha(checks / "validation.json"),
        "attribution_sha256": sha(checks / "attribution.json"),
        "bundle": paths.config["bundle"],
        "bundle_sha256": sha(paths.bundle),
        "data_cutoff": p["source_data_cutoff"],
        "training": p["training_output"],
        "identity": validation["identity"],
        "evaluation": p["evaluation_output"],
        "status_review": paths.config["historical_status_review"],
        "unchanged_baseline_files": 35158,
        "plan": plan_file.relative_to(root).as_posix(),
        "plan_sha256": sha(plan_file),
        "orders": len(plan["orders"]),
        "expires_at": plan["expires_at"],
        "selection_mode": "positive_score",
        "turnover_policy": "legacy",
        "risk": p["risk"],
        "complete_economic_exposure_certified": False,
        "economic_exposure_upper_bound": None,
        "historical_availability_certified": False,
        "superiority_assessment": "NOT_ESTABLISHED",
        "forward_observations_added": 0,
        "limitations": p["limitations"],
        "scope": "Missing dates adopted as verified facts after all registered gates, independently of return direction. Previous plans and results remain unchanged. Remote publication is checked separately.",
    },
    immutable=True,
)
freeze_outputs()
print("DATE FACTS ACTIVATED", plan["intent"]["decision"], flush=True)
print(build_report(paths), flush=True)
