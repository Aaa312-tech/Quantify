"""Refit all registered windows and verify saved models and complete accounts."""

import logging
from pathlib import Path

from quantfolio.evaluation import evaluate
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.training import run_training
from quantfolio.verification import verify, verify_paper


def require(condition, message):
    if not condition:
        raise ValueError(message)


logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
paths = Paths(root, p["configuration"])
base = root / p["checks_output"]
run, evaluation = root / p["training_output"], root / p["evaluation_output"]
preparation = read(base / "fit_preparation.json")
numeric = read(base / "numeric_acceptance.json")
parity = read(base / "compatibility/validation.json")
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
require(preparation["status"] == "FROZEN_BEFORE_TRAINING", "Fit inputs incomplete")
require(
    numeric["status"] == parity["status"] == "PASS", "Input or compatibility acceptance missing"
)
require(
    sources == preparation["source_sha256"] == numeric["source_sha256"] == parity["source_sha256"],
    "Executed source changed",
)
require(preparation["data_sha256"] == sha(paths.bundle), "Training data changed")
require(
    preparation["target_expectations_sha256"] == sha(base / "target_expectations.json"),
    "Fit expectations changed",
)
require(
    preparation["configuration_sha256"] == sha(paths.configuration_path),
    "Experiment config changed",
)
require(
    paths.config["selection_mode"] == "positive_score"
    and paths.config["turnover_policy"] == "legacy",
    "Strategy policy changed",
)
require(
    sha(root / "configs/project.toml") == p["active_configuration_sha256"], "Active config changed"
)
require(
    not run.exists() and not evaluation.exists(), "Existing run requires explicit recovery review"
)
save(
    base / "execution_protocol.json",
    {
        "started_at": now(),
        "runner_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "source_sha256": sources,
        "configuration_sha256": sha(paths.configuration_path),
        "bundle_sha256": sha(paths.bundle),
        "target_expectations_sha256": sha(base / "target_expectations.json"),
        "active_configuration_sha256": p["active_configuration_sha256"],
        "complete_economic_exposure_certified": False,
    },
    immutable=True,
)
training = run_training(paths, run, include_latest=True)
require(training["status"] == "COMPLETE", "Training range incomplete")
models = verify(paths, base / "models", run=run)
require(models["models_reloaded"] == p["retraining"]["fits"], "Model inventory differs")
result = evaluate(paths, run, evaluation, p["retraining"]["slippage_bps"], all_policies=True)
require(
    result["accounts"] == p["retraining"]["evaluation_accounts"], "Evaluation inventory differs"
)
paper = verify_paper(paths, base / "paper")
require(
    paper["status"] == "PASS" and paper["accounts"] == p["retraining"]["paper_accounts"],
    "Paper verification differs",
)
require(
    sources == {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))},
    "Runtime changed during fitting or evaluation",
)
require(
    sha(root / "configs/project.toml") == p["active_configuration_sha256"], "Active config changed"
)
save(
    base / "training_validation.json",
    {
        "status": "PASS",
        "completed_at": now(),
        "runner_sha256": sha(Path(__file__)),
        "source_sha256": sources,
        "configuration_sha256": sha(paths.configuration_path),
        "data_sha256": sha(paths.bundle),
        "protocol_sha256": sha(pfile),
        "identity": training["identity"],
        "fits": models["models_reloaded"],
        "models_reloaded": models["models_reloaded"],
        "account_audits": result["accounts"],
        "paper": paper,
        "evaluation_sha256": sha(evaluation / "evaluation_summary.json"),
        "active_configuration_unchanged": True,
        "activated": False,
        "scope": "Complete fitting and ordinary guarded model/account/paper checks. Independent fit expectation, fixed-forecast, settlement, status and exposure attribution remain mandatory before activation.",
        "complete_economic_exposure_certified": False,
        "limitations": p["limitations"],
    },
    immutable=True,
)
print("TRAINING_AND_ACCOUNTS_PASSED", flush=True)
