"""Adopt accepted entitlement inputs only after complete integration validation."""

from pathlib import Path

from quantfolio.planning import freeze_plan, source_hashes
from quantfolio.reporting import build_report
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha

root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
checks = root / p["checks_output"]
validation = read(checks / "validation.json")
attribution = read(checks / "attribution.json")
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
scenarios = read(checks / "settlement_scenarios.json")
scenario_protocol = root / "configs/protocols/recipient_settlement_scenarios.json"
if scenarios["status"] != "PASS" or scenarios["source_sha256"] != source:
    raise ValueError("Supplemental settlement coverage is missing or stale")
if scenarios["synthetic_cases"] != 217 or scenarios["source_events"] != 31:
    raise ValueError("Supplemental settlement scope is incomplete")
if scenarios["bundle_sha256"] != validation["data_sha256"]:
    raise ValueError("Settlement cases used different inputs")
if scenarios["protocol_sha256"] != sha(scenario_protocol):
    raise ValueError("Settlement registration changed")
if scenarios["expectations_sha256"] != sha(checks / "settlement_scenario_expectations.json"):
    raise ValueError("Independent settlement expectations changed")
if not (validation["status"] == "PASS" and attribution["status"] == "COMPLETE"):
    raise ValueError(
        "Audit condition failed: validation['status'] == 'PASS' and attribution['status'] == 'COMPLETE'"
    )
if not source == validation["source_sha256"] == attribution["source_sha256"]:
    raise ValueError(
        "Audit condition failed: source == validation['source_sha256'] == attribution['source_sha256']"
    )
if not validation["baseline_frozen_files_preserved"] == 37730:
    raise ValueError(
        "Audit condition failed: validation['baseline_frozen_files_preserved'] == 37730"
    )
if not validation["fits"] == validation["models_reloaded"] == 48:
    raise ValueError(
        "Audit condition failed: validation['fits'] == validation['models_reloaded'] == 48"
    )
if not validation["account_audits"] == validation["fixed_forecast_accounts"] == 1425:
    raise ValueError(
        "Audit condition failed: validation['account_audits'] == validation['fixed_forecast_accounts'] == 1425"
    )
if not validation["paper"]["accounts"] == 75:
    raise ValueError("Audit condition failed: validation['paper']['accounts'] == 75")
if not paths.config["bundle"] == p["source_bundle"]:
    raise ValueError("Audit condition failed: paths.config['bundle'] == p['source_bundle']")
if not sha(paths.bundle) == p["source_bundle_sha256"]:
    raise ValueError("Audit condition failed: sha(paths.bundle) == p['source_bundle_sha256']")
if not sha(paths.configuration_path) == p["active_configuration_sha256"]:
    raise ValueError(
        "Audit condition failed: sha(paths.configuration_path) == p['active_configuration_sha256']"
    )
if not sha(root / p["current_plan"]["path"]) == p["current_plan"]["sha256"]:
    raise ValueError(
        "Audit condition failed: sha(root / p['current_plan']['path']) == p['current_plan']['sha256']"
    )


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
                or any((part in {"__pycache__", ".pytest_cache"} for part in file.parts))
            ):
                continue
            relative, value = (file.relative_to(root).as_posix(), sha(file))
            if not (relative not in manifest["files"] or manifest["files"][relative] == value):
                raise ValueError(relative)
            manifest["files"][relative] = value
    save(paths.manifest, manifest)


freeze_outputs()
paths.configuration_path.write_bytes((root / p["configuration"]).read_bytes())
paths = Paths(root)
folder = root / p["new_plan"]
freeze_plan(paths, "artifacts/accounts/forward_2026-11-23/account.json", folder)
plan_file = folder / "plan.json"
plan = read(plan_file)
if not (plan["status"] == "FROZEN_CONDITIONAL_PLAN" and (not plan["blockers"])):
    raise ValueError(
        "Audit condition failed: plan['status'] == 'FROZEN_CONDITIONAL_PLAN' and (not plan['blockers'])"
    )
if not plan["source_sha256"] == source_hashes():
    raise ValueError("Audit condition failed: plan['source_sha256'] == source_hashes()")
if not plan["active_configuration"] == paths.config:
    raise ValueError("Audit condition failed: plan['active_configuration'] == paths.config")
if not plan["identity"] == digest({key: value for key, value in plan.items() if key != "identity"}):
    raise ValueError(
        "Audit condition failed: plan['identity'] == digest({key: value for key, value in plan.items() if key != 'identity'})"
    )
if not (plan["signal_date"] == "2026-11-20" and plan["execution_date"] == "2026-11-23"):
    raise ValueError(
        "Audit condition failed: plan['signal_date'] == '2026-11-20' and plan['execution_date'] == '2026-11-23'"
    )
if not plan["intent"]["fund_mode"] == "reported":
    raise ValueError("Audit condition failed: plan['intent']['fund_mode'] == 'reported'")
signal = attribution["latest_signal"]
if (
    not plan["input_sha256"][p["training_output"] + "/final_fit/outer_predictions.npz"]
    == signal["forecast_sha256"]
):
    raise ValueError(
        "Audit condition failed: plan['input_sha256'][p['training_output'] + '/final_fit/outer_predictions.npz'] == signal['forecast_sha256']"
    )
for name, value in plan["input_sha256"].items():
    if not sha(root / name) == value:
        raise ValueError(name)
if signal["cost_scenarios"]["10bps"]["passes_new_buy_edge_filter"] == 0:
    if not not plan["orders"]:
        raise ValueError("Audit condition failed: not plan['orders']")
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
        "supplemental_settlement_protocol_sha256": sha(scenario_protocol),
        "supplemental_settlement_review_sha256": sha(checks / "settlement_scenarios.json"),
        "synthetic_settlement_cases": scenarios["synthetic_cases"],
        "attribution_sha256": sha(checks / "attribution.json"),
        "bundle": paths.config["bundle"],
        "bundle_sha256": sha(paths.bundle),
        "data_cutoff": p["source_data_cutoff"],
        "training": p["training_output"],
        "identity": validation["identity"],
        "evaluation": p["evaluation_output"],
        "status_review": paths.config["historical_status_review"],
        "unchanged_baseline_files": 37730,
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
        "scope": "Recipient entitlements, price references and availability dates adopted as verified fields after all registered gates, independently of return direction. Previous plans and results remain unchanged. Remote publication is checked separately.",
    },
    immutable=True,
)
freeze_outputs()
print("ENTITLEMENT FACTS ACTIVATED", plan["intent"]["decision"], flush=True)
print(build_report(paths), flush=True)
