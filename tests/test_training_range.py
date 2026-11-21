"""Growing calendars must extend default runs without relabelling partial research."""

import json
from copy import deepcopy

import pandas as pd
import pytest

from quantfolio import cli, dataset, training
from quantfolio.settings import Paths
from quantfolio.storage import read, save


@pytest.fixture
def calendar_run(tmp_path, monkeypatch):
    def prepare(sessions=698):
        config = tmp_path / "configs"
        config.mkdir(exist_ok=True)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "quantfolio-research"\n', encoding="utf-8"
        )
        (config / "project.toml").write_text(
            'bundle = "bundle.joblib"\nprotocol = "configs/protocol.json"\n'
            'manifest = "configs/manifest.json"\n',
            encoding="utf-8",
        )
        (tmp_path / "bundle.joblib").write_bytes(b"synthetic calendar; no market observations")
        save(config / "protocol.json", {"scope": "synthetic range regression"})
        save(config / "manifest.json", {"files": {}})
        calendar = pd.bdate_range("2010-01-01", periods=sessions)
        bundle = {"calendar": calendar, "cutoff": str(calendar[-1].date())}
        monkeypatch.setattr(dataset, "load_bundle", lambda paths: bundle)
        calls = []

        def capture(bundle, fold, output, identity, **options):
            calls.append((deepcopy(fold), output, identity, options))

        monkeypatch.setattr(training, "run_window", capture)
        return Paths(tmp_path), tmp_path / "artifacts/experiments/range", calls

    return prepare


@pytest.mark.parametrize("sessions,windows", [(479, 1), (698, 11), (710, 12)])
@pytest.mark.parametrize("latest", [False, True])
def test_default_covers_all_complete_windows(calendar_run, sessions, windows, latest):
    paths, output, calls = calendar_run(sessions)
    result = training.run_training(paths, output, include_latest=latest)
    assert result["status"] == "COMPLETE"
    assert result["folds_completed"] == windows
    assert [call[0]["fold"] for call in calls] == list(range(1, windows + 1)) + (
        ["final"] if latest else []
    )
    protocol = read(output / "protocol.json")
    assert protocol["specification"]["selected_folds"] == [1, windows]
    assert all(call[2] == result["identity"] == protocol["identity"] for call in calls)
    if latest:
        assert calls[-1][0]["test_start"] == calls[-1][0]["test_end"] == sessions - 1


@pytest.mark.parametrize(
    "bounds,expected,status",
    [
        ({"first": 2}, list(range(2, 13)), "PARTIAL"),
        ({"first": 1, "last": 11}, list(range(1, 12)), "PARTIAL"),
        ({"first": 4, "last": 6}, [4, 5, 6], "PARTIAL"),
        ({"first": 1, "last": 12}, list(range(1, 13)), "COMPLETE"),
    ],
)
def test_explicit_ranges_keep_actual_scope(calendar_run, bounds, expected, status):
    paths, output, calls = calendar_run(710)
    result = training.run_training(paths, output, **bounds)
    assert [call[0]["fold"] for call in calls] == expected
    assert result["status"] == status
    assert result["folds_completed"] == len(expected)
    assert read(output / "protocol.json")["specification"]["selected_folds"] == [
        expected[0],
        expected[-1],
    ]


@pytest.mark.parametrize("bounds", [{}, {"include_latest": True}])
def test_no_complete_window_cannot_create_training(calendar_run, bounds):
    paths, output, calls = calendar_run(478)
    with pytest.raises(ValueError, match="Require 1 <= first <= last <= 0"):
        training.run_training(paths, output, **bounds)
    assert calls == [] and not output.exists()


@pytest.mark.parametrize("bounds", [{"first": 0}, {"last": 0}, {"first": 12}, {"last": 12}])
def test_invalid_range_cannot_create_training(calendar_run, bounds):
    paths, output, calls = calendar_run()
    with pytest.raises(ValueError, match="Require 1 <= first <= last <= 11"):
        training.run_training(paths, output, **bounds)
    assert calls == [] and not output.exists()


@pytest.mark.parametrize(
    "arguments,expected,status",
    [([], list(range(1, 13)), "COMPLETE"), (["--last", "11"], list(range(1, 12)), "PARTIAL")],
)
def test_cli_uses_complete_default_and_preserves_explicit_partial(
    calendar_run, capsys, arguments, expected, status
):
    paths, output, calls = calendar_run(710)
    cli.main(["--project", str(paths.root), "train", "--output", str(output), *arguments])
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status
    assert [call[0]["fold"] for call in calls] == expected


@pytest.mark.parametrize(
    "arguments", [["--first", "0"], ["--last", "0"], ["--first", "3", "--last", "2"]]
)
def test_cli_rejects_invalid_bounds_before_loading_a_project(tmp_path, capsys, arguments):
    output = tmp_path / "artifacts/experiments/invalid"
    with pytest.raises(SystemExit) as error:
        cli.main(["--project", str(tmp_path), "train", "--output", str(output), *arguments])
    assert error.value.code == 2
    assert "require 1 <= --first <= --last" in capsys.readouterr().err
    assert not output.exists()
