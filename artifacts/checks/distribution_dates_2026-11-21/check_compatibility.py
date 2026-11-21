"""Reload models and reproduce all old-input accounts through guarded entry points."""

import logging
from pathlib import Path

from quantfolio.evaluation import evaluate
from quantfolio.feasibility import account_differences
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.verification import verify, verify_paper

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
paths = Paths(root)
p = read(root / "configs/protocols/distribution_dates.json")
checks = root / p["checks_output"] / "compatibility"
sources = {f.name: sha(f) for f in (root / "src/quantfolio").glob("*.py")}
assert sha(paths.bundle) == p["source_bundle_sha256"]
assert sha(paths.configuration_path) == p["active_configuration_sha256"]
run = root / p["baseline_run"]
models = verify(paths, checks / "models", run=run)
assert models["models_reloaded"] == 48
result = evaluate(paths, run, root / p["compatibility_output"], [10, 20, 30], all_policies=True)
comparison = account_differences(
    root / p["baseline_evaluation"],
    root / p["compatibility_output"],
    result["artifacts"],
    exact=True,
)
assert result["accounts"] == len(comparison) == 1425
save(checks / "account_parity.json", comparison, immutable=True)
paper = verify_paper(paths, checks / "paper")
assert paper["status"] == "PASS" and paper["accounts"] == 75
assert sources == {f.name: sha(f) for f in (root / "src/quantfolio").glob("*.py")}
assert sha(paths.configuration_path) == p["active_configuration_sha256"]
save(
    checks / "validation.json",
    {
        "status": "PASS",
        "completed_at": now(),
        "source_sha256": sources,
        "models_reloaded": 48,
        "full_accounts_exact": 1425,
        "paper_accounts_exact": 75,
        "baseline_run": p["baseline_run"],
        "baseline_evaluation": p["baseline_evaluation"],
        "data_sha256": sha(paths.bundle),
        "active_configuration_unchanged": True,
        "scope": "Ordinary model, evaluation and paper entry points; no identity guard is bypassed.",
    },
    immutable=True,
)
print("FULL BASELINE COMPATIBILITY PASSED", flush=True)
