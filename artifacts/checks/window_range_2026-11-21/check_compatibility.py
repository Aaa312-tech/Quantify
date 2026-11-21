"""Replay the active research through ordinary guards after range-only maintenance."""

import logging
from pathlib import Path

from quantfolio.evaluation import evaluate
from quantfolio.feasibility import account_differences, fit_parity
from quantfolio.planning import verified_plan
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.verification import verify, verify_paper

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
paths = Paths(root)
p = read(root / "configs/protocols/window_range.json")
checks = root / p["checks_output"]
implementation = read(checks / "implementation.json")
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
assert implementation["status"] == "PASS" and sources == implementation["source_sha256"]
assert sha(paths.bundle) == p["bundle_sha256"]
assert sha(paths.configuration_path) == p["active_configuration_sha256"]
run = root / p["baseline_run"]
models = verify(paths, checks / "models", run=run)
assert models["status"] == "PASS" and models["models_reloaded"] == 48
assert all(row["max_error"] == 0 for row in models["models"])
# The preceding payment repair retained every fitted estimator and forecast.
# Compare these two distinct, already frozen runs with the new inventory guard.
parent = read(paths.protocol)
comparison_run = root / parent["baseline_run"]
fits = fit_parity(comparison_run, run)
assert fits["status"] == "PASS" and len(fits["models"]) == 48 and len(fits["forecasts"]) == 12
save(
    checks / "existing_fit_comparison.json",
    {
        "status": "PASS",
        "baseline": comparison_run.relative_to(root).as_posix(),
        "current": p["baseline_run"],
        "scope": "Inventory and equality of two pre-existing frozen runs; no new fitting.",
        **fits,
    },
    immutable=True,
)
evaluation = evaluate(paths, run, root / p["compatibility_output"], [10, 20, 30], all_policies=True)
accounts = account_differences(
    root / p["baseline_evaluation"],
    root / p["compatibility_output"],
    evaluation["artifacts"],
    exact=True,
)
assert len(accounts) == evaluation["accounts"] == p["validation"]["baseline_accounts"] == 1425
save(checks / "account_parity.json", accounts, immutable=True)
paper = verify_paper(paths, checks / "paper")
assert paper["status"] == "PASS" and paper["accounts"] == 75
_, plan = verified_plan(paths, p["current_plan"]["path"])
assert sha(root / p["current_plan"]["path"]) == p["current_plan"]["sha256"]
assert plan["status"] == "FROZEN_CONDITIONAL_PLAN" and not plan["orders"] and not plan["blockers"]
assert sources == {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
assert sha(paths.configuration_path) == p["active_configuration_sha256"]
save(
    checks / "compatibility.json",
    {
        "status": "PASS",
        "completed_at": now(),
        "source_sha256": sources,
        "models_reloaded": models["models_reloaded"],
        "model_max_error": 0,
        "fit_inventory_models": len(fits["models"]),
        "fit_inventory_forecasts": len(fits["forecasts"]),
        "full_accounts_exact": len(accounts),
        "paper_accounts_exact": paper["accounts"],
        "paper": paper,
        "refits": 0,
        "active_configuration_sha256": sha(paths.configuration_path),
        "bundle_sha256": sha(paths.bundle),
        "baseline_evaluation_unchanged": sha(
            root / p["baseline_evaluation"] / "evaluation_summary.json"
        )
        == p["baseline_evaluation_sha256"],
        "current_plan_sha256": sha(root / p["current_plan"]["path"]),
        "plan_orders": len(plan["orders"]),
        "evaluation_sha256": sha(root / p["compatibility_output"] / "evaluation_summary.json"),
        "scope": "All ordinary source, identity, data and plan guards remain enabled. Exact full-ledger replay and model reload; no new strategy, fits, source facts or forward observations.",
    },
    immutable=True,
)
print("COMPLETE RANGE MAINTENANCE COMPATIBILITY PASSED", flush=True)
