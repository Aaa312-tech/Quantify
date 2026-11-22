"""Risk, output isolation and project portability invariants."""

from pathlib import Path

import pytest

from quantfolio.risk import RiskState
from quantfolio.settings import Paths, project_root
from quantfolio.storage import read, save


def test_risk_never_recovers_or_resets_original_peak():
    state = RiskState()
    for nav, phase in [
        (1_000_000, "NORMAL"),
        (960_000, "CAUTION45"),
        (990_000, "CAUTION45"),
        (940_000, "FROZEN20"),
        (999_000, "FROZEN20"),
        (920_000, "HALTED0"),
        (1_100_000, "HALTED0"),
    ]:
        result = state.update(nav, True)
        assert result["phase"] == phase
        assert result["peak"] >= 1_000_000
    assert not result["can_buy"]


@pytest.mark.parametrize("nav", [0, -1, float("nan"), float("inf")])
def test_invalid_nav_is_rejected(nav):
    with pytest.raises(ValueError):
        RiskState().update(nav, False)


def test_recovery_mode_is_rejected():
    with pytest.raises(ValueError):
        RiskState(mode="recovery").update(1_000_000, True)


def test_new_outputs_cannot_overwrite_frozen_artifacts(tmp_path):
    paths = Paths(tmp_path)
    with pytest.raises(ValueError):
        paths.output_path("artifacts/reference_run", "experiments")
    with pytest.raises(ValueError):
        paths.output_path("artifacts/experiments/../../outside", "experiments")
    first = paths.output_path("artifacts/experiments/new_run", "experiments")
    assert not first.exists()
    first.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        paths.output_path(first, "experiments")


def test_project_lookup_from_another_directory(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(tmp_path)
    assert project_root(root) == root


def test_immutable_json_rejects_changed_values(tmp_path):
    path = tmp_path / "result.json"
    save(path, {"status": "PASS"}, immutable=True)
    save(path, {"status": "PASS"}, immutable=True)
    with pytest.raises(ValueError):
        save(path, {"status": "FAIL"}, immutable=True)
    assert read(path) == {"status": "PASS"}


def test_experiment_configuration_does_not_modify_the_active_configuration(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    active = configs / "project.toml"
    alternate = configs / "comparison.toml"
    active.write_text('bundle = "original.joblib"\n', encoding="utf-8")
    alternate.write_text('bundle = "comparison.joblib"\n', encoding="utf-8")
    before = active.read_bytes()
    selected = Paths(tmp_path, "configs/comparison.toml")
    assert selected.bundle == tmp_path / "comparison.joblib"
    assert Paths(tmp_path).bundle == tmp_path / "original.joblib"
    assert active.read_bytes() == before


@pytest.mark.parametrize(
    "name",
    ["../external.toml", "configs/../../external.toml", "data/config.toml", "configs/config.json"],
)
def test_experiment_configuration_must_stay_under_configs(tmp_path, name):
    with pytest.raises(ValueError, match="Configuration"):
        _ = Paths(tmp_path, name).config
