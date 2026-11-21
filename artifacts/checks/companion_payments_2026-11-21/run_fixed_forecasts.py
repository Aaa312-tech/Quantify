"""Measure changed dates with unchanged parent forecasts and rule schedules."""

import logging
from pathlib import Path

import pandas as pd

from quantfolio.dataset import load_bundle, load_snapshot
from quantfolio.evaluation import POLICIES, evaluate_accounts, load_predictions
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
protocol_file = root / "configs/protocols/companion_payments.json"
p = read(protocol_file)
paths = Paths(root, p["configuration"])
checks = root / p["checks_output"]
evaluation = root / p["fixed_forecast_output"]
previous_run = root / p["baseline_run"]
old_file = root / p["source_bundle"]
assert not evaluation.exists()
status = read(previous_run / "training_status.json")
registration = read(previous_run / "protocol.json")
assert status["status"] == "COMPLETE"
assert status["identity"] == registration["identity"] == digest(registration["specification"])
assert registration["specification"]["data_sha256"] == sha(old_file)
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert sources == read(checks / "snapshot_review.json")["source_sha256"]
assert sources == read(checks / "compatibility/validation.json")["source_sha256"]
assert sha(root / "configs/project.toml") == p["active_configuration_sha256"]
old = load_snapshot(paths, old_file, p["source_data_cutoff"])
bundle = load_bundle(paths)
labels = {f"{field}{h}" for field in ("y", "end", "status", "delay") for h in (1, 3, 5)}
columns = [name for name in old["table"] if name not in labels]
pd.testing.assert_frame_equal(old["table"][columns], bundle["table"][columns], check_exact=True)
maps, schedule, _, start, end = load_predictions(old, previous_run, identity=status["identity"])
forecast_hash, schedule_hash = digest(maps), digest(schedule)
del old
save(
    checks / "fixed_forecast_protocol.json",
    {
        "started_at": now(),
        "source_training_identity": status["identity"],
        "forecast_bundle_sha256": sha(old_file),
        "execution_bundle_sha256": sha(paths.bundle),
        "source_sha256": sources,
        "protocol_sha256": sha(protocol_file),
        "forecasts_sha256": forecast_hash,
        "schedule_sha256": schedule_hash,
        "signal_start": start,
        "signal_end": end,
        "scope": "Explicit fixed-parent-forecast attribution; every forecast and rule is unchanged. No ordinary evaluator identity guard is bypassed.",
    },
    immutable=True,
)
result = evaluate_accounts(
    bundle, maps, schedule, start, end, [10, 20, 30], POLICIES, evaluation, turnover="legacy"
)
assert result["accounts"] == 1425
assert digest(maps) == forecast_hash and digest(schedule) == schedule_hash
assert sources == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert sha(root / "configs/project.toml") == p["active_configuration_sha256"]
result.update(
    status="PASS",
    completed_at=now(),
    scope="FIXED_PARENT_FORECAST_ATTRIBUTION",
    source_training_identity=status["identity"],
    forecast_bundle_sha256=sha(old_file),
    execution_bundle_sha256=sha(paths.bundle),
    source_sha256=sources,
    forecasts_sha256=forecast_hash,
    schedule_sha256=schedule_hash,
)
save(evaluation / "summary.json", result, immutable=True)
print("FIXED FORECAST ACCOUNTS PASSED", result["accounts"], flush=True)
