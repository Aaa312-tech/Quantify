"""Exercise ordinary model, account and paper guards on the unchanged active inputs."""

import logging
from pathlib import Path

from quantfolio.evaluation import evaluate
from quantfolio.feasibility import account_differences
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.verification import verify, verify_paper


def require(condition, message):
    if not condition:
        raise ValueError(message)


logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
paths = Paths(root)
p = read(root / "configs/protocols/recipient_entitlements.json")
checks = root / p["checks_output"] / "compatibility"
sources = {f.name: sha(f) for f in (root / "src/quantfolio").glob("*.py")}
require(sha(paths.bundle) == p["source_bundle_sha256"], "Active bundle changed")
require(sha(paths.configuration_path) == p["active_configuration_sha256"], "Active config changed")
run = root / p["baseline_run"]
models = verify(paths, checks / "models", run=run)
require(
    models["models_reloaded"] == p["compatibility"]["models_reloaded"], "Model inventory differs"
)
result = evaluate(paths, run, root / p["compatibility_output"], [10, 20, 30], all_policies=True)
comparison = account_differences(
    root / p["baseline_evaluation"],
    root / p["compatibility_output"],
    result["artifacts"],
    exact=True,
)
require(
    result["accounts"] == len(comparison) == p["compatibility"]["full_accounts_exact"],
    "Account inventory differs",
)
save(checks / "account_parity.json", comparison, immutable=True)
paper = verify_paper(paths, checks / "paper")
require(
    paper["status"] == "PASS" and paper["accounts"] == p["compatibility"]["paper_accounts_exact"],
    "Paper parity differs",
)
require(
    sources == {f.name: sha(f) for f in (root / "src/quantfolio").glob("*.py")},
    "Runtime changed during compatibility",
)
require(sha(paths.configuration_path) == p["active_configuration_sha256"], "Active config changed")
save(
    checks / "validation.json",
    {
        "status": "PASS",
        "completed_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "source_sha256": sources,
        "models_reloaded": models["models_reloaded"],
        "full_accounts_exact": len(comparison),
        "paper_accounts_exact": paper["accounts"],
        "baseline_run": p["baseline_run"],
        "baseline_evaluation": p["baseline_evaluation"],
        "data_sha256": sha(paths.bundle),
        "active_configuration_unchanged": True,
        "scope": "Ordinary guarded entry points; original training/source identities retained.",
    },
    immutable=True,
)
print("FULL_BASELINE_COMPATIBILITY_PASSED", flush=True)
