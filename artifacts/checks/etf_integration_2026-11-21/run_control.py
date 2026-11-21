"""Train and audit one registered ETF control through ordinary guarded entry points."""

import logging
import sys
from pathlib import Path

from quantfolio.evaluation import evaluate
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.training import run_training
from quantfolio.verification import verify, verify_paper

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
protocol_file = root / "configs/protocols/etf_integration.json"
protocol = read(protocol_file)
variant = sys.argv[1]
setting = protocol["variants"][variant]
paths = Paths(root, setting["configuration"])
checks = root / setting["checks_output"]
run = root / setting["training_output"]
evaluation = root / setting["evaluation_output"]
snapshot = read(root / protocol["checks_output"] / "snapshot_review.json")
parity = read(root / protocol["checks_output"] / "implementation/validation.json")
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert snapshot["status"] == parity["status"] == "PASS"
assert sources == snapshot["source_sha256"] == parity["source_sha256"]
assert (
    parity["models_reloaded"] == 48
    and parity["full_accounts_exact"] == 1425
    and parity["paper_accounts_exact"] == 75
)
assert (
    paths.config["selection_mode"] == "positive_score"
    and paths.config["turnover_policy"] == "legacy"
)
assert read(paths.manifest)["files"][setting["configuration"]] == sha(paths.configuration_path)
active_configuration = sha(root / "configs/project.toml")
assert not run.exists() and not evaluation.exists()
save(
    checks / "execution_protocol.json",
    {
        "started_at": now(),
        "variant": variant,
        "registration_commit": "f6b07fc8c590f131d59c8681c8a5d5e8206ad06e",
        "protocol_sha256": sha(protocol_file),
        "source_sha256": sources,
        "configuration_sha256": sha(paths.configuration_path),
        "bundle_sha256": sha(paths.bundle),
        "active_configuration_sha256": active_configuration,
        "complete_economic_exposure_certified": False,
    },
    immutable=True,
)
training = run_training(paths, run, include_latest=True)
assert training["status"] == "COMPLETE"
models = verify(paths, checks / "models", run=run)
assert models["models_reloaded"] == 48
result = evaluate(paths, run, evaluation, [10, 20, 30], all_policies=True)
assert result["accounts"] == 1425
paper = verify_paper(paths, checks / "paper")
assert paper["status"] == "PASS" and paper["accounts"] == 75
assert sources == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert sha(root / "configs/project.toml") == active_configuration
save(
    checks / "training_validation.json",
    {
        "status": "PASS",
        "completed_at": now(),
        "variant": variant,
        "source_sha256": sources,
        "configuration_sha256": sha(paths.configuration_path),
        "data_sha256": sha(paths.bundle),
        "protocol_sha256": sha(protocol_file),
        "identity": training["identity"],
        "fits": 48,
        "models_reloaded": 48,
        "account_audits": 1425,
        "paper": paper,
        "evaluation_sha256": sha(evaluation / "evaluation_summary.json"),
        "active_configuration_unchanged": True,
        "activated": False,
        "scope": "Complete retraining and accounts. Fixed forecasts, source/status exposure and attribution remain separate required reviews.",
        "complete_economic_exposure_certified": False,
        "limitations": protocol["limitations"],
    },
    immutable=True,
)
print("TRAINING AND ACCOUNTS PASSED", variant, flush=True)
