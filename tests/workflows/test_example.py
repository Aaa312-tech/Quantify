"""The standalone tutorial must execute installed APIs without research inputs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from examples.offline_backtest import momentum_signals, synthetic_market


@pytest.mark.integration
def test_offline_example_runs_outside_the_repository(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples/offline_backtest.py"
    command = [sys.executable, "-B", str(script), "--output", str(tmp_path / "example")]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["kind"] == "SYNTHETIC" and receipt["trades"] > 0
    checks = json.loads((tmp_path / "example/checks.json").read_text("utf-8"))
    assert checks["accounting"]["status"] == checks["execution"]["status"] == "PASS"
    assert subprocess.run(command, cwd=tmp_path, capture_output=True).returncode != 0


def test_tutorial_signal_does_not_read_a_future_close():
    bundle = synthetic_market()
    original = momentum_signals(bundle)
    bundle["panels"]["close"].iloc[-10:] *= 100
    changed = momentum_signals(bundle)
    assert {day: value for day, value in original.items() if day < 70} == {
        day: value for day, value in changed.items() if day < 70
    }
