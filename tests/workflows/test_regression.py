"""Regression fails closed when a registered input, output or comparison is incomplete."""

import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from quantfolio import cli, regression
from quantfolio.settings import Paths
from quantfolio.storage import read, save, sha


@pytest.mark.parametrize(
    "before,after",
    [
        ({"a": 1, "b": 2}, {"a": 1}),
        ({"a": 1}, {"a": 1, "b": 2}),
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}),
        ([1, 2], [1]),
        ([1], (1,)),
        (np.array([1], dtype="float32"), np.array([1], dtype="float64")),
        (np.array([1.0]), np.array([1.0 + 1e-14])),
        (pd.Series([1.0], index=["a"]), pd.Series([1.0], index=["b"])),
        (pd.date_range("2026-01-01", periods=2), pd.date_range("2026-01-02", periods=2)),
    ],
)
def test_exact_comparison_rejects_changed_values_or_structure(before, after):
    with pytest.raises((AssertionError, ValueError)):
        regression.compare_values(before, after, "account")


def test_exact_comparison_accepts_matching_missing_observations():
    frame = pd.DataFrame({"price": [1.0, np.nan]})
    regression.compare_values(
        {"data": frame, "values": np.array([np.nan])},
        {"data": frame.copy(), "values": np.array([np.nan])},
    )
    calendar = pd.date_range("2026-01-01", periods=2)
    regression.compare_values(calendar, calendar.copy())


@pytest.mark.parametrize("relative", ["../outside", "/outside", "missing"])
def test_registered_files_must_exist_inside_the_project(tmp_path, relative):
    with pytest.raises(ValueError, match="Missing or invalid"):
        regression.checked_file(tmp_path, relative, "unknown")


def test_missing_lfs_object_has_recovery_instruction(tmp_path):
    file = tmp_path / "model.joblib"
    file.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 12\n")
    with pytest.raises(ValueError, match="git lfs pull"):
        regression.checked_file(tmp_path, file.name, sha(file))


def test_registered_file_hash_is_checked(tmp_path):
    file = tmp_path / "input.json"
    file.write_bytes(b"original")
    before = sha(file)
    file.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        regression.checked_file(tmp_path, file.name, before)


@pytest.fixture
def registered_inputs(tmp_path):
    paths = Paths(tmp_path)
    paths.configuration_path.parent.mkdir()
    paths.configuration_path.write_text('manifest = "configs/manifest.json"', encoding="utf-8")
    for name in (
        "artifacts/experiments/active/result.json",
        "artifacts/evaluations/active/result.json",
    ):
        save(tmp_path / name, {"status": "PASS"})
    files = {p.relative_to(tmp_path).as_posix(): sha(p) for p in tmp_path.rglob("*") if p.is_file()}
    save(paths.manifest, {"files": files})
    return paths, {
        "inputs_sha256": files,
        "subjects": {},
        "configuration": "configs/project.toml",
        "training": "artifacts/experiments/active",
        "evaluation": "artifacts/evaluations/active",
    }


@pytest.mark.parametrize("change", ["empty", "missing", "extra", "changed", "unregistered"])
def test_input_inventory_rejects_incomplete_or_changed_research(registered_inputs, change):
    paths, baseline = registered_inputs
    target = paths.root / baseline["training"] / "result.json"
    if change == "empty":
        baseline["inputs_sha256"] = {}
    elif change == "missing":
        target.unlink()
    elif change == "extra":
        save(target.parent / "unexpected.json", {})
    elif change == "changed":
        save(target, {"status": "FAIL"})
    else:
        save(paths.manifest, {"files": {}})
    with pytest.raises(ValueError):
        regression.check_inputs(paths, baseline)


def test_input_inventory_accepts_registered_files(registered_inputs):
    regression.check_inputs(*registered_inputs)


def test_archive_configuration_is_checked_before_loading(registered_inputs):
    paths, baseline = registered_inputs
    archive = paths.root / "configs/archive.toml"
    archive.write_bytes(b'[features]\nold = "current"\n')
    baseline["configuration_sha256"] = {"configs/archive.toml": sha(archive)}
    archive.write_bytes(b'[features]\nold = "different"\n')
    with pytest.raises(ValueError, match="hash differs"):
        regression.check_inputs(paths, baseline)


@pytest.fixture
def baseline_repository(tmp_path):
    package = tmp_path / "src/quantfolio"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b'MARKER = "frozen"\n')
    for name in ("dataset", "evaluation", "models", "features", "training", "verification"):
        (package / (name + ".py")).write_bytes(b"")
    command = ["git", "-C", str(tmp_path)]
    for args in (
        ["init"],
        ["add", "src"],
        [
            "-c",
            "user.name=Regression test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Fixture",
        ],
    ):
        subprocess.run([*command, *args], check=True, capture_output=True)
    revision = subprocess.check_output([*command, "rev-parse", "HEAD"], text=True).strip()
    scope = {
        "baseline_commit": revision,
        "source_sha256": {p.relative_to(tmp_path).as_posix(): sha(p) for p in package.glob("*.py")},
    }
    return Paths(tmp_path), scope, revision


def test_baseline_is_prepared_from_git_and_removed_after_use(baseline_repository):
    paths, scope, revision = baseline_repository
    with regression.baseline_package(paths, scope, revision) as (previous, temporary):
        assert previous.MARKER == "frozen"
        assert temporary.exists()
        assert not temporary.is_relative_to(paths.root)
    assert not temporary.exists()
    assert "_quantfolio_regression_baseline" not in sys.modules


@pytest.mark.parametrize("change", ["commit", "hash", "member", "absent"])
def test_baseline_rejects_unregistered_source(baseline_repository, change):
    paths, scope, revision = baseline_repository
    if change == "commit":
        scope["baseline_commit"] = "0" * 40
    elif change == "hash":
        scope["source_sha256"]["src/quantfolio/__init__.py"] = "0" * 64
    elif change == "member":
        scope["source_sha256"]["../outside.py"] = "0" * 64
    else:
        revision = "not-an-existing-commit"
    with pytest.raises(ValueError):
        with regression.baseline_package(paths, scope, revision):
            pytest.fail("Invalid baseline was accepted")


def test_regression_failure_is_recorded_and_cannot_be_overwritten(tmp_path):
    output = tmp_path / "artifacts/checks/failed"
    with pytest.raises(FileNotFoundError):
        regression.run_regression(Paths(tmp_path), output, "HEAD")
    result = read(output / "result.json")
    assert result["status"] == "FAIL"
    assert result["failed_stage"] == "inputs"
    assert (output / "run.json").is_file()
    assert (output / "run.log").is_file()
    before = sha(output / "result.json")
    with pytest.raises(FileExistsError):
        regression.run_regression(Paths(tmp_path), output, "HEAD")
    assert sha(output / "result.json") == before


def test_core_cannot_register_compatibility(tmp_path, capsys):
    output = tmp_path / "artifacts/checks/core"
    with pytest.raises(ValueError, match="--scope full"):
        regression.run_regression(Paths(tmp_path), output, "HEAD", register=True)
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "regression",
                "--scope",
                "core",
                "--baseline",
                "HEAD",
                "--output",
                str(output),
                "--register",
            ]
        )
    assert error.value.code == 2
    assert "--scope full" in capsys.readouterr().err
    assert not output.exists()


def test_incomplete_result_cannot_supply_full_scope():
    with pytest.raises(ValueError, match="Incomplete regression"):
        regression.scope_counts({"models": []})


@pytest.mark.parametrize("change", ["core", "sources", "accounts", "prediction_error", "evidence"])
def test_registration_rejects_incomplete_or_unbound_evidence(tmp_path, monkeypatch, change):
    output = tmp_path / "artifacts/checks/replay"
    report = {
        "status": "PASS",
        "scope": "full",
        "source_sha256": {"code.py": "code"},
        "profile_sha256": "profile",
        "baseline_commit": "baseline",
        "new_model_fits": 0,
        "models": [{"max_error": 0}],
        "training": {"windows": [{}], "fit_inputs": [{}, {}]},
        "history": {"accounts": 25, "ledger_files": 2},
        "paper": {"accounts": 2, "sessions": 20},
        "features": {"securities": 10, "frozen_features": {"features": 57}},
    }
    profile = {
        "profile_sha256": "profile",
        "baseline_commit": "baseline",
        "subjects": {},
        "expected": regression.scope_counts(report),
    }
    evidence = {
        "status": "PASS",
        "scope": "full",
        "kind": "EXECUTION_DIFFERENTIAL",
        "runtime_sha256": report["source_sha256"],
        "subjects": {},
        "profile_sha256": "profile",
        **{k: report[k] for k in ("features", "training", "models")},
    }
    if change == "core":
        report["scope"] = "core"
    elif change == "sources":
        report["source_sha256"] = {"code.py": "changed"}
    elif change == "accounts":
        report["history"]["accounts"] -= 1
    elif change == "prediction_error":
        report["models"][0]["max_error"] = 1e-14
    else:
        evidence["runtime_sha256"] = {"code.py": "older"}
    save(output / "result.json", report)
    save(output / "differential.json", evidence)
    monkeypatch.setattr(regression, "read_baseline", lambda paths: profile)
    monkeypatch.setattr(regression, "check_inputs", lambda *args: None)
    monkeypatch.setattr(regression, "runtime_sources", lambda: {"code.py": "code"})
    monkeypatch.setattr(
        regression, "register_output", lambda *args: pytest.fail("Invalid evidence registered")
    )
    with pytest.raises((AssertionError, ValueError)):
        regression.register_compatibility(Paths(tmp_path), output)
    assert not (output / "source_compatibility.json").exists()
