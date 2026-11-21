"""Fit the registered date repair and audit its stored models and complete accounts."""

import logging
from pathlib import Path

from quantfolio.evaluation import evaluate
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.training import run_training
from quantfolio.verification import verify, verify_paper

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
protocol_file = root / "configs/protocols/companion_payments.json"
p = read(protocol_file)
paths = Paths(root, p["configuration"])
checks = root / p["checks_output"]
run, evaluation = root / p["training_output"], root / p["evaluation_output"]
snapshot = read(checks / "snapshot_review.json")
parity = read(checks / "compatibility/validation.json")
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert snapshot["status"] == parity["status"] == "PASS"
assert sources == snapshot["source_sha256"] == parity["source_sha256"]
assert parity["models_reloaded"] == 48
assert parity["full_accounts_exact"] == 1425 and parity["paper_accounts_exact"] == 75
assert paths.config["selection_mode"] == "positive_score"
assert paths.config["turnover_policy"] == "legacy"
assert read(paths.manifest)["files"][p["configuration"]] == sha(paths.configuration_path)
assert sha(root / "configs/project.toml") == p["active_configuration_sha256"]
assert not run.exists() and not evaluation.exists()
save(
    checks / "execution_protocol.json",
    {
        "started_at": now(),
        "protocol_sha256": sha(protocol_file),
        "source_sha256": sources,
        "configuration_sha256": sha(paths.configuration_path),
        "bundle_sha256": sha(paths.bundle),
        "target_expectations_sha256": sha(checks / "target_expectations.json"),
        "active_configuration_sha256": p["active_configuration_sha256"],
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
assert sha(root / "configs/project.toml") == p["active_configuration_sha256"]
save(
    checks / "training_validation.json",
    {
        "status": "PASS",
        "completed_at": now(),
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
        "scope": "Complete fitting and guarded account checks; fixed-forecast, status and exposure attribution are separate required gates.",
        "complete_economic_exposure_certified": False,
        "limitations": p["limitations"],
    },
    immutable=True,
)
print("TRAINING AND ACCOUNTS PASSED", flush=True)
