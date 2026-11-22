"""Exercise the experiment-to-report flow with a small, executable market fixture."""

import json
from argparse import Namespace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantfolio import cli, evaluation, reporting
from quantfolio.dataset import eligible_rows
from quantfolio.market import EXECUTION_MODEL
from quantfolio.settings import Paths
from quantfolio.simulation import simulate
from quantfolio.storage import digest, read, save, sha
from tests.factories import market_bundle


def test_configured_status_and_default_report_use_current_evaluation(experiment):
    paths, _, run, _ = experiment
    output = paths.root / "artifacts/evaluations/current"
    result = evaluation.evaluate(paths, run, output, [10])
    paths.config.update(
        evaluation="artifacts/evaluations/current",
        manifest="configs/current_manifest.json",
        strategy="walk_forward_momentum",
        strategy_name="滚动选型动量",
    )
    save(
        paths.manifest,
        {
            "files": {
                "artifacts/evaluations/current/evaluation_summary.json": sha(
                    output / "evaluation_summary.json"
                )
            }
        },
    )
    status = cli.run(Namespace(command="status"), paths)
    assert status["displayed_results_scope"] == "CAUSAL_RETRAINING"
    assert not status["corrected_retraining_completed"]
    assert status["test_start"] == result["trade_start"]
    assert status["gates"] == {"superiority": False}
    assert status["superiority_assessment"] == "NO_POSITIVE_EXCESS_TO_MATCHED_MOMENTUM"
    reporting.build_report(paths)
    assert (paths.root / "reports/latest/index.html").is_file()


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    bundle, _ = market_bundle()
    bundle["table"]["eligible"] = True
    bundle["cutoff"] = str(bundle["calendar"][-1].date())
    folds = [
        {
            "fold": 1,
            "train_start": 0,
            "train_end": 0,
            "validation_start": 1,
            "validation_end": 4,
            "test_start": 5,
            "test_end": 14,
        },
        {
            "fold": 2,
            "train_start": 5,
            "train_end": 5,
            "validation_start": 6,
            "validation_end": 9,
            "test_start": 15,
            "test_end": 24,
        },
    ]
    (tmp_path / "configs").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "quantfolio-research"\n', encoding="utf-8"
    )
    (tmp_path / "configs/project.toml").write_text(
        f'bundle = "bundle.joblib"\ndata_cutoff = "{bundle["cutoff"]}"\n'
        'protocol = "configs/experiment.json"\n',
        encoding="utf-8",
    )
    (tmp_path / "bundle.joblib").write_bytes(b"synthetic dataset: loader replaced by fixture")
    save(tmp_path / "configs/experiment.json", {"name": "synthetic protocol"})
    run = tmp_path / "artifacts/experiments/partial_run"
    specification = {
        "data_sha256": sha(tmp_path / "bundle.joblib"),
        "plan_sha256": sha(tmp_path / "configs/experiment.json"),
        "folds": folds,
        "selected_folds": [1, 1],
        "execution_model": EXECUTION_MODEL,
        "source_sha256": {
            file.name: sha(file) for file in Path(evaluation.__file__).parent.glob("*.py")
        },
    }
    identity = digest(specification)
    save(run / "protocol.json", {"identity": identity, "specification": specification})
    save(
        run / "training_status.json",
        {
            "identity": identity,
            "status": "PARTIAL",
            "folds_completed": 1,
            "data_cutoff": bundle["cutoff"],
        },
    )
    folder = run / "training/fold_01"
    folder.mkdir(parents=True)
    rows = eligible_rows(bundle["table"], 5, 14)
    np.savez_compressed(
        folder / "outer_predictions.npz",
        row_indices=rows,
        common_mu=np.full(len(rows), 0.05),
        common_margin=np.zeros(len(rows)),
        momentum_rank=np.tile(np.linspace(1, 0.4, 7), 10),
    )
    save(
        folder / "result.json",
        {
            "identity": identity,
            "fold": folds[0],
            "final_fit": False,
            "outer_results_used_for_selection": False,
            "selected_candidate": "momentum",
            "selected_rules": "baseline",
            "predictions_sha256": sha(folder / "outer_predictions.npz"),
        },
    )
    monkeypatch.setattr(evaluation, "check_integrity", lambda paths: {"status": "PASS"})
    monkeypatch.setattr(evaluation, "load_bundle", lambda paths: bundle)
    monkeypatch.setattr(evaluation, "walk_forward_folds", lambda calendar: folds)
    monkeypatch.setattr(reporting, "check_integrity", lambda paths: {"status": "PASS"})
    return Paths(tmp_path), bundle, run, folds


@pytest.mark.parametrize("stored_policy", ["selected_strategy", "archived_primary"])
def test_evaluate_and_report_use_actual_window_cost_and_policy(experiment, capsys, stored_policy):
    paths, _, run, _ = experiment
    output = paths.root / "artifacts/evaluations/partial_run"
    cli.main(
        [
            "--project",
            str(paths.root),
            "evaluate",
            "--run",
            str(run),
            "--output",
            str(output),
            "--cost",
            "20",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PARTIAL"
    assert result["folds"] == 1
    assert result["accounts"] == 4
    assert result["superiority_assessment"] == "NOT_PERFORMED"
    assert result["primary_policy"] == "selected_strategy"
    assert result["results"]["20bps"]["selected_strategy"]["aggregate"]["complete_episodes"] == 1
    assert len(result["artifacts"]) == 4
    if stored_policy != "selected_strategy":
        # Archived evaluations use their original policy in both keys and filenames.
        for section in ("results", "continuous"):
            policies = result[section]["20bps"]
            policies[stored_policy] = policies.pop("selected_strategy")
        for relative, checksum in list(result["artifacts"].items()):
            if "selected_strategy" in relative:
                renamed = relative.replace("selected_strategy", stored_policy)
                (output / relative).rename(output / renamed)
                del result["artifacts"][relative]
                result["artifacts"][renamed] = checksum
        result["primary_policy"] = stored_policy
        save(output / "evaluation_summary.json", result)
    cli.main(["--project", str(paths.root), "report", "--run", str(output)])
    report = Path(json.loads(capsys.readouterr().out)["report"]).read_text("utf-8")
    assert "部分窗口回测" in report
    assert "20bp" in report and "1 / 1" in report
    assert "历史基准" not in report
    assert result["trade_start"] in report and result["trade_end"] in report
    assert "$title" not in report


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("status", "COMPLETE", "registered fold range"),
        ("identity", "another-run", "identity mismatch"),
        ("data_cutoff", "2099-01-01", "cutoff"),
    ],
)
def test_rejects_inconsistent_training_metadata(experiment, field, value, message):
    paths, _, run, _ = experiment
    status = read(run / "training_status.json")
    status[field] = value
    save(run / "training_status.json", status)
    output = paths.root / "artifacts/evaluations/rejected"
    with pytest.raises(ValueError, match=message):
        evaluation.evaluate(paths, run, output, [10])
    assert not output.exists()


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("execution_model", "archived_full_bar_filter", "another execution model"),
        ("source_sha256", {}, "Execution source changed"),
        ("selection_mode", "positive_score", "adoption policy"),
    ],
)
def test_rejects_training_from_another_execution_engine(experiment, field, value, message):
    paths, bundle, run, _ = experiment
    protocol = read(run / "protocol.json")
    protocol["specification"][field] = value
    protocol["identity"] = digest(protocol["specification"])
    status = read(run / "training_status.json")
    status["identity"] = protocol["identity"]
    save(run / "protocol.json", protocol)
    save(run / "training_status.json", status)
    with pytest.raises(ValueError, match=message):
        evaluation.experiment_folds(paths, bundle, run)


@pytest.mark.parametrize(
    "mutation,message", [("duplicate", "Prediction rows"), ("nan", "Invalid prediction array")]
)
def test_rejects_bad_prediction_rows_and_nonfinite_values(experiment, mutation, message):
    paths, _, run, _ = experiment
    folder = run / "training/fold_01"
    with np.load(folder / "outer_predictions.npz") as stored:
        arrays = dict(stored)
    if mutation == "duplicate":
        arrays["row_indices"][1] = arrays["row_indices"][0]
    else:
        arrays["common_mu"][0] = np.nan
    np.savez_compressed(folder / "outer_predictions.npz", **arrays)
    result = read(folder / "result.json")
    result["predictions_sha256"] = sha(folder / "outer_predictions.npz")
    save(folder / "result.json", result)
    with pytest.raises(ValueError, match=message):
        evaluation.evaluate(paths, run, paths.root / "artifacts/evaluations/rejected", [10])


def test_duplicate_fold_cannot_silently_overwrite_predictions(experiment):
    _, bundle, run, folds = experiment
    with pytest.raises(ValueError, match="Overlapping"):
        evaluation.load_predictions(bundle, run, [folds[0], folds[0]])


def test_complete_run_requires_every_registered_fold(experiment):
    paths, bundle, run, folds = experiment
    protocol = read(run / "protocol.json")
    protocol["specification"]["selected_folds"] = [1, 2]
    identity = digest(protocol["specification"])
    protocol["identity"] = identity
    save(run / "protocol.json", protocol)
    status = read(run / "training_status.json")
    status.update(identity=identity, status="COMPLETE", folds_completed=2)
    save(run / "training_status.json", status)
    first = run / "training/fold_01"
    metadata = read(first / "result.json")
    metadata["identity"] = identity
    save(first / "result.json", metadata)
    output = paths.root / "artifacts/evaluations/complete_run"
    with pytest.raises(FileNotFoundError):
        evaluation.evaluate(paths, run, output, [10])
    assert not output.exists()

    second = run / "training/fold_02"
    second.mkdir()
    with np.load(first / "outer_predictions.npz") as stored:
        arrays = dict(stored)
    arrays["row_indices"] = eligible_rows(bundle["table"], 15, 24)
    np.savez_compressed(second / "outer_predictions.npz", **arrays)
    metadata.update(fold=folds[1], predictions_sha256=sha(second / "outer_predictions.npz"))
    save(second / "result.json", metadata)
    result = evaluation.evaluate(paths, run, output, [10])
    assert result["status"] == "COMPLETE"
    assert result["folds"] == 2 and result["accounts"] == 6


@pytest.mark.parametrize("policy", ["selected_strategy", "matched_momentum"])
def test_report_rejects_changed_evaluation_ledger(experiment, policy):
    paths, _, run, _ = experiment
    output = paths.root / "artifacts/evaluations/changed"
    evaluation.evaluate(paths, run, output, [10])
    (output / f"evaluation/continuous/10bps/{policy}.json.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="ledger hash mismatch"):
        reporting.build_report(paths, output)
    assert not (paths.root / "reports/experiments/changed/equity_curve.png").exists()
    assert not (paths.root / "reports/experiments/changed/index.html").exists()


@pytest.mark.parametrize("previous", [True, False])
def test_simulation_restores_callers_pandas_setting(previous):
    bundle, forecasts = market_bundle()
    with pd.option_context("compute.use_numexpr", previous):
        simulate(bundle, 6, 8, forecasts)
        assert pd.get_option("compute.use_numexpr") is previous


@pytest.mark.parametrize(
    "start,end,cost", [(0, 3, 0.001), (6, 5, 0.001), (6, 30, 0.001), (6, 8, -0.001), (6, 8, np.nan)]
)
def test_invalid_execution_range_or_cost_is_rejected(start, end, cost):
    bundle, forecasts = market_bundle()
    with pytest.raises(ValueError):
        simulate(bundle, start, end, forecasts, slippage=cost)
