"""Attribute corrected inputs with the parent forecasts and rule schedule held fixed."""

import logging
from pathlib import Path

import pandas as pd

from quantfolio.dataset import load_bundle, load_snapshot
from quantfolio.evaluation import POLICIES, evaluate_accounts, load_predictions
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
paths = Paths(root, p["configuration"])
base = root / p["checks_output"]
output = root / p["fixed_forecast_output"]
run = root / p["baseline_run"]
require(not output.exists(), "Fixed-forecast output already exists")
status, registration = read(run / "training_status.json"), read(run / "protocol.json")
require(status["status"] == "COMPLETE", "Parent training incomplete")
require(
    status["identity"] == registration["identity"] == digest(registration["specification"]),
    "Parent training identity differs",
)
require(
    registration["specification"]["data_sha256"] == p["source_bundle_sha256"],
    "Parent forecasts bind another input",
)
numeric = read(base / "numeric_acceptance.json")
require(
    numeric["status"] == "PASS" and numeric["bundle_sha256"] == sha(paths.bundle),
    "Corrected inputs not independently accepted",
)
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
require(
    sources
    == numeric["source_sha256"]
    == read(base / "compatibility/validation.json")["source_sha256"],
    "Executed runtime differs",
)
require(
    sha(root / "configs/project.toml") == p["active_configuration_sha256"], "Active config changed"
)
old = load_snapshot(paths, root / p["source_bundle"], p["source_data_cutoff"])
bundle = load_bundle(paths)
pd.testing.assert_index_equal(old["calendar"], bundle["calendar"], exact=True)
pd.testing.assert_frame_equal(
    old["table"][["date_index", "symbol", "eligible", "industry"]],
    bundle["table"][["date_index", "symbol", "eligible", "industry"]],
    check_exact=True,
)
maps, schedule, _, start, end = load_predictions(old, run, identity=status["identity"])
forecasts_sha, schedule_sha = digest(maps), digest(schedule)
del old
save(
    base / "fixed_forecast_protocol.json",
    {
        "started_at": now(),
        "runner_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "source_training_identity": status["identity"],
        "forecast_bundle_sha256": p["source_bundle_sha256"],
        "execution_bundle_sha256": sha(paths.bundle),
        "source_sha256": sources,
        "numeric_acceptance_sha256": sha(base / "numeric_acceptance.json"),
        "forecasts_sha256": forecasts_sha,
        "schedule_sha256": schedule_sha,
        "signal_start": start,
        "signal_end": end,
        "scope": "Original forecasts and rule schedules stay fixed. Corrected price features, labels, opening references and settlement inputs are accepted independently; whole-feature equality is neither assumed nor required. This is explicit data/execution attribution, not an ordinary refitted evaluation.",
    },
    immutable=True,
)
result = evaluate_accounts(
    bundle,
    maps,
    schedule,
    start,
    end,
    p["retraining"]["slippage_bps"],
    POLICIES,
    output,
    turnover="legacy",
)
require(
    result["accounts"] == p["retraining"]["fixed_forecast_accounts"],
    "Fixed account inventory differs",
)
require(
    digest(maps) == forecasts_sha and digest(schedule) == schedule_sha,
    "Parent forecasts or rules changed",
)
require(
    sources == {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))},
    "Runtime changed during attribution",
)
require(
    sha(root / "configs/project.toml") == p["active_configuration_sha256"], "Active config changed"
)
result.update(
    status="PASS",
    completed_at=now(),
    scope="FIXED_PARENT_FORECAST_ATTRIBUTION",
    source_training_identity=status["identity"],
    forecast_bundle_sha256=p["source_bundle_sha256"],
    execution_bundle_sha256=sha(paths.bundle),
    source_sha256=sources,
    forecasts_sha256=forecasts_sha,
    schedule_sha256=schedule_sha,
)
save(output / "summary.json", result, immutable=True)
print("FIXED_FORECAST_ACCOUNTS_PASSED", result["accounts"], flush=True)
