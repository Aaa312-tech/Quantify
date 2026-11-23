"""Command failures and artifact writes must remain reliable outside development mode."""

import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from quantfolio import __version__, cli, storage
from quantfolio.dataset import check_integrity, walk_forward_folds
from quantfolio.settings import Paths


def test_version_needs_no_project_or_numerical_imports(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "quantfolio", "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"quantfolio {__version__}"
    probe = subprocess.run(
        [sys.executable, "-B", "-c", "import sys, quantfolio; print('pandas' in sys.modules)"],
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "False"


def test_bad_arguments_do_not_create_an_experiment(tmp_path, capsys):
    output = tmp_path / "artifacts/experiments/bad"
    with pytest.raises(SystemExit) as error:
        cli.main(["--project", str(tmp_path), "train", "--first", "0", "--output", str(output)])
    assert error.value.code == 2
    assert not output.exists()
    assert "--first" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["replay", "backtest", "execution-bridge"])
@pytest.mark.parametrize("primary", [False, True])
@pytest.mark.parametrize("costs", [[], ["--cost", "30", "--cost", "10", "--cost", "30"]])
def test_account_commands_preserve_costs_policies_and_output(
    tmp_path, monkeypatch, command, primary, costs
):
    from quantfolio import evaluation

    paths = Paths(tmp_path)
    category = "evaluations" if command == "execution-bridge" else "replays"
    output = f"artifacts/{category}/comparison"
    arguments = [command, "--output", output, *costs]
    if primary:
        arguments.append("--primary-only")
    observed = []

    def capture(received_paths, target, selected_costs, policies):
        observed.append((received_paths, target, selected_costs, policies))
        return {"status": "CAPTURED"}

    function = "execution_bridge" if command == "execution-bridge" else "replay"
    monkeypatch.setattr(evaluation, function, capture)
    assert cli.run(cli._parser().parse_args(arguments), paths) == {"status": "CAPTURED"}
    expected_output = output if command == "execution-bridge" else tmp_path / output
    expected_policies = (
        ["selected_strategy", "matched_momentum"] if primary else evaluation.POLICIES
    )
    assert observed == [
        (paths, expected_output, [10, 30] if costs else [10, 20, 30], expected_policies)
    ]
    assert not (tmp_path / output).exists()


def test_missing_project_has_actionable_error_without_traceback(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["--project", str(tmp_path), "status"])
    assert error.value.code == 1
    message = capsys.readouterr().err
    assert "Project not found" in message
    assert "Traceback" not in message


def test_lfs_pointer_has_download_instruction(tmp_path):
    config = tmp_path / "configs"
    config.mkdir()
    (config / "project.toml").write_text(
        'manifest = "configs/integrity_manifest.json"', encoding="utf-8"
    )
    pointer = tmp_path / "bundle.joblib"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 9999\n", encoding="utf-8"
    )
    storage.save(config / "integrity_manifest.json", {"files": {"bundle.joblib": "missing"}})
    with pytest.raises(ValueError, match="git lfs pull"):
        check_integrity(Paths(tmp_path))


@pytest.mark.parametrize("arguments", [{"test": 0}, {"train": -1}, {"gap": -1}])
def test_invalid_window_cannot_loop_forever(arguments):
    with pytest.raises(ValueError, match="Window lengths"):
        walk_forward_folds(range(1000), **arguments)


def test_cache_validation_is_active_under_optimized_python(tmp_path):
    storage.save(tmp_path / "cached.json", {"identity": "wrong"})
    script = """
from pathlib import Path
import sys
from quantfolio.training import trial
try:
    trial({}, {}, {}, 'baseline', Path(sys.argv[1]) / 'cached', 'expected')
except ValueError as error:
    print(error)
else:
    sys.exit('Invalid cache was accepted')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-O", "-c", script, str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "identity" in result.stdout


def test_interrupted_gzip_write_preserves_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json.gz"
    storage.dump_gzip(target, {"cash": 100})
    before = target.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failure"):
        storage.dump_gzip(target, {"cash": 200})
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_immutable_writers_cannot_overwrite_each_other(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)
    atomic_write = storage._atomic_write

    def synchronized_write(*args, **kwargs):
        barrier.wait(timeout=10)
        return atomic_write(*args, **kwargs)

    monkeypatch.setattr(storage, "_atomic_write", synchronized_write)
    target = tmp_path / "result.json"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(storage.save, target, {"writer": number}, True) for number in range(2)
        ]
        errors = [future.exception() for future in futures]
    assert sum(isinstance(error, ValueError) for error in errors) == 1
    assert storage.read(target)["writer"] in (0, 1)
    assert not list(tmp_path.glob("*.tmp"))


def test_paths_reject_symlink_outside_project(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, project / "artifacts", target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit directory symlinks")
    with pytest.raises(ValueError, match="named directory"):
        Paths(project).output_path("artifacts/experiments/run", "experiments")
