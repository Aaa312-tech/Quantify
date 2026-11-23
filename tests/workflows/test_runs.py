"""Receipts bind actual workflow inputs; the catalogue never chooses by return."""

import json
from argparse import Namespace
from copy import deepcopy

import pytest

from quantfolio import cli, evaluation, planning, runs, training
from quantfolio.settings import Paths
from quantfolio.storage import read, save, sha


def register(paths):
    files = {
        p.relative_to(paths.root).as_posix(): sha(p)
        for p in paths.root.rglob("*")
        if p.is_file() and p != paths.manifest
    }
    save(paths.manifest, {"files": files})


@pytest.fixture
def project(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/project.toml").write_text(
        'bundle="data.json"\nprotocol="configs/protocol.json"\narchive="configs/archive.toml"\nmanifest="configs/manifest.json"\n',
        encoding="utf-8",
    )
    (tmp_path / "configs/archive.toml").write_text('name="fixture"\n', encoding="utf-8")
    save(tmp_path / "data.json", {"fixture": True})
    save(tmp_path / "configs/protocol.json", {"fixture": True})
    paths = Paths(tmp_path)
    register(paths)
    return paths


def train_record(paths, name="example"):
    output = paths.root / "artifacts/experiments" / name
    with runs.audit_run(paths, "train", output, parameters={"first": 1, "last": 2}):
        save(output / "protocol.json", {"specification": {"model_candidates": ["momentum"]}})
        save(output / "training_status.json", {"status": "COMPLETE"})
    return output


def test_parent_closure_records_configs_sources_outputs_and_options(project):
    run = train_record(project)
    output = project.root / "artifacts/evaluations/example"
    with runs.audit_run(project, "evaluate", output, [run / "protocol.json"], {"costs": [10]}):
        save(
            output / "evaluation_summary.json",
            {"status": "COMPLETE", "source_run": run.relative_to(project.root).as_posix()},
        )
    receipt = runs.verify_run(project, output / "run.json", current_sources=True)
    assert receipt["parents"] == ["artifacts/experiments/example/protocol.json"]
    assert "artifacts/experiments/example/run.json" in receipt["inputs"]
    assert receipt["parameters"]["costs"] == [10]
    assert {"rules.py", "storage.py", "runs.py", "models.py"} <= receipt["source_sha256"].keys()
    assert "reporting.py" not in receipt["source_sha256"]
    assert receipt["configuration"]["effective"] == project.config
    assert receipt["configuration_files"]["configs/archive.toml"]["sha256"] == sha(
        project.root / "configs/archive.toml"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "input",
        "missing",
        "unregistered",
        "outputs",
        "missing_output",
        "format",
        "kind",
        "operation",
        "failed",
        "config_text",
        "config_values",
        "config_missing",
        "config_content",
        "source_missing",
        "source_changed",
        "closure_missing",
        "parent_missing",
    ],
)
def test_receipt_rejects_incomplete_or_changed_dependencies(project, fault):
    output = train_record(project)
    file = output / "run.json"
    record = read(file)
    if fault == "input":
        save(project.root / "data.json", {"changed": True})
    elif fault == "missing":
        (project.root / "data.json").unlink()
    elif fault == "outputs":
        save(output / "undeclared.json", {})
    elif fault == "missing_output":
        (output / "training_status.json").unlink()
    elif fault == "format":
        record["schema_version"] = 1
    elif fault == "kind":
        record["kind"] = "OTHER"
    elif fault == "operation":
        record["operation"] = "unknown"
    elif fault == "failed":
        record["status"] = "FAILED"
    elif fault == "config_text":
        record["configuration"]["text"] += "\n"
    elif fault == "config_values":
        record["configuration"]["effective"]["bundle"] = "absent.json"
    elif fault == "config_missing":
        record["configuration_files"] = {}
    elif fault == "config_content":
        record["configuration_files"]["configs/archive.toml"]["text"] += "\n"
    elif fault == "source_missing":
        record["source_sha256"].pop("rules.py")
    elif fault == "source_changed":
        record["source_sha256"]["rules.py"] = "changed"
    elif fault == "closure_missing":
        record["inputs"].pop("data.json")
    elif fault == "parent_missing":
        record["parents"] = ["missing.json"]
    if fault not in {"input", "missing", "outputs", "missing_output", "unregistered"}:
        save(file, record)
        register(project)
    if fault == "unregistered":
        manifest = read(project.manifest)
        del manifest["files"]["data.json"]
        save(project.manifest, manifest)
    with pytest.raises((ValueError, FileNotFoundError)):
        runs.verify_run(project, file, current_sources=True)


@pytest.mark.parametrize("fault", ["input", "configuration", "archive", "sources"])
def test_mutation_during_execution_leaves_a_failed_receipt(project, monkeypatch, fault):
    output = project.root / "artifacts/experiments/interrupted"
    with pytest.raises(ValueError):
        with runs.audit_run(project, "train", output):
            save(output / "training_status.json", {"status": "COMPLETE"})
            if fault == "input":
                save(project.root / "data.json", {"changed": True})
            elif fault == "configuration":
                project.configuration_path.write_text('bundle="different"', encoding="utf-8")
            elif fault == "archive":
                (project.root / "configs/archive.toml").write_text(
                    'name="changed"', encoding="utf-8"
                )
            else:
                monkeypatch.setattr(runs, "source_closure", lambda roots: {})
    assert read(output / "run.json")["status"] == "FAILED"
    assert read(output / "run.json")["error"]["type"] == "ValueError"


def test_failure_before_output_does_not_create_a_receipt(project):
    output = project.root / "artifacts/experiments/absent"
    with pytest.raises(RuntimeError):
        with runs.audit_run(project, "train", output):
            raise RuntimeError("no outputs produced")
    assert not output.exists()


def test_unknown_operation_and_incomplete_parent_contracts(project):
    for operation, parents in [
        ("unknown", []),
        ("train", ["extra"]),
        ("evaluate", ["account.json"]),
        ("plan", ["a", "b"]),
    ]:
        with pytest.raises(ValueError):
            runs.dependency_inputs(project, operation, parents)
    with pytest.raises(ValueError, match="dataset or protocol"):
        runs.dependency_inputs(project, "train", [], {})
    project.config["bundle"] = "absent"
    with pytest.raises(ValueError):
        with runs.audit_run(project, "train", project.root / "unused"):
            pytest.fail("Changed configuration accepted")


def test_legacy_parents_and_cycles(project):
    run = project.root / "artifacts/experiments/legacy"
    save(run / "protocol.json", {"specification": {}})
    save(run / "training_status.json", {"status": "PARTIAL"})
    register(project)
    inputs = runs.dependency_inputs(
        project, "evaluate", ["artifacts/experiments/legacy/protocol.json"]
    )
    assert "artifacts/experiments/legacy/training_status.json" in inputs
    save(run / "protocol.json", {"plan": "artifacts/experiments/legacy/protocol.json"})
    register(project)
    with pytest.raises(ValueError, match="Cyclic"):
        runs.dependency_inputs(project, "evaluate", ["artifacts/experiments/legacy/protocol.json"])


def test_new_record_cycle_is_rejected(project):
    run = train_record(project)
    record = read(run / "run.json")
    record.update(operation="evaluate", parents=["artifacts/experiments/example/protocol.json"])
    save(run / "run.json", record)
    register(project)
    with pytest.raises(ValueError, match="Cyclic"):
        runs.verify_run(project, run / "run.json")


def test_index_preserves_failures_and_uses_configuration_not_profit(project):
    adopted = train_record(project)
    evaluation = project.root / "artifacts/evaluations/adopted"
    save(
        evaluation / "evaluation_summary.json",
        {
            "status": "COMPLETE",
            "source_run": adopted.relative_to(project.root).as_posix(),
            "results": {"10bps": {"return": 0.01}},
        },
    )
    other = project.root / "artifacts/evaluations/higher_return"
    save(
        other / "evaluation_summary.json",
        {"status": "FAILED", "source_run": "missing", "results": {"10bps": {"return": 9.0}}},
    )
    failed = project.root / "artifacts/experiments/failed"
    with pytest.raises(RuntimeError):
        with runs.audit_run(project, "train", failed):
            save(failed / "protocol.json", {})
            raise RuntimeError("training did not complete")
    register(project)
    project.config["evaluation"] = evaluation.relative_to(project.root).as_posix()
    before = deepcopy(project.config)
    index = cli.run(Namespace(command="runs"), project)
    assert project.config == before
    assert {row["path"] for row in index["runs"] if row["adopted"]} == {
        adopted.relative_to(project.root).as_posix(),
        project.config["evaluation"],
    }
    assert len([row for row in index["runs"] if row["status"] == "FAILED"]) == 2
    assert index == runs.run_index(project)
    assert cli.run(Namespace(command="integrity"), project)["status"] == "PASS"
    record = read(adopted / "run.json")
    record["schema_version"] = 99
    save(adopted / "run.json", record)
    register(project)
    with pytest.raises(ValueError, match="Unknown run"):
        runs.run_index(project)


@pytest.mark.integration
def test_training_entrypoint_registers_without_new_research_fits(tmp_path, monkeypatch):
    from tests.workflows.test_training_range import calendar_run

    paths, output, calls = calendar_run.__wrapped__(tmp_path, monkeypatch)(479)
    register(paths)
    monkeypatch.setattr(training, "audit_run", runs.audit_run)
    result = training.run_training(paths, output)
    record = runs.verify_run(paths, output / "run.json", current_sources=True)
    assert result["status"] == record["status"] == "COMPLETE" and len(calls) == 1
    assert all(call[3]["turnover"] == "legacy" for call in calls)


@pytest.mark.integration
def test_evaluation_entrypoint_records_the_synthetic_parent(tmp_path, monkeypatch):
    from tests.workflows.test_evaluation import experiment

    paths, _, run, _ = experiment.__wrapped__(tmp_path, monkeypatch)
    with paths.configuration_path.open("a", encoding="utf-8") as stream:
        stream.write('manifest="configs/manifest.json"\n')
    register(paths)
    monkeypatch.setattr(evaluation, "audit_run", runs.audit_run)
    output = tmp_path / "artifacts/evaluations/receipt"
    result = evaluation.evaluate(paths, run, output, [10])
    record = runs.verify_run(paths, output / "run.json", current_sources=True)
    assert record["status"] == result["status"] == "PARTIAL"
    assert record["parameters"]["costs"] == [10]
    assert any(name.endswith("outer_predictions.npz") for name in record["inputs"])


@pytest.mark.integration
def test_plan_entrypoint_records_synthetic_dependencies(tmp_path, monkeypatch):
    from tests.workflows.test_planning import opening_quotes, paper_project

    paths, _, _, clock = paper_project(tmp_path, monkeypatch)
    with paths.configuration_path.open("a", encoding="utf-8") as stream:
        stream.write(
            'protocol="configs/protocol.json"\nevaluation="artifacts/evaluations/fixture"\n'
        )
    save(tmp_path / "configs/protocol.json", {"synthetic": True})
    save(tmp_path / "artifacts/experiments/fixture/protocol.json", {})
    save(tmp_path / "artifacts/experiments/fixture/training_status.json", {"status": "COMPLETE"})
    save(
        tmp_path / "artifacts/evaluations/fixture/evaluation_summary.json",
        {"status": "COMPLETE", "source_run": "artifacts/experiments/fixture"},
    )
    register(paths)
    monkeypatch.setattr(planning, "audit_run", runs.audit_run)
    output = tmp_path / "artifacts/plans/receipt"
    result = planning.freeze_plan(paths, "account.json", output)
    record = runs.verify_run(paths, output / "run.json", current_sources=True)
    assert record["status"] == result["status"] == "FROZEN_CONDITIONAL_PLAN"
    assert {"account.json", "data.bin"} <= record["inputs"].keys()
    plan = read(output / "plan.json")
    assert plan["orders"] and json.dumps(record)
    assert planning.verified_plan(paths, output / "plan.json")[1] == plan
    clock[0] = "2026-01-13T09:26:00+08:00"
    save(
        tmp_path / "opening.json",
        {
            "session": "2026-01-13",
            "source": "synthetic",
            "recorded_at": "2026-01-13T09:25:30+08:00",
            "quotes": opening_quotes(plan["intent"]),
        },
    )
    resolved = planning.resolve_plan(
        paths, output / "plan.json", "opening.json", "artifacts/plans/opening"
    )
    clock[0] = "2026-01-13T16:01:00+08:00"
    save(
        tmp_path / "closing.json",
        {
            "session": "2026-01-13",
            "source": "synthetic",
            "recorded_at": "2026-01-13T15:01:00+08:00",
            "quotes": {
                symbol: {"close": 10.2, "market_at": "2026-01-13T15:00:00+08:00"}
                for symbol in plan["intent"]["contracts"]
            },
        },
    )
    closed = planning.close_account(
        paths, resolved["resolution"], "closing.json", "artifacts/accounts/closing"
    )
    followup = planning.freeze_plan(paths, closed["account"], "artifacts/plans/followup")
    followup_record = runs.verify_run(
        paths, tmp_path / "artifacts/plans/followup/run.json", current_sources=True
    )
    assert followup["status"] == "BLOCKED"
    assert {"opening.json", "closing.json"} <= followup_record["inputs"].keys()


def test_account_initialization_registers_snapshot_and_rejects_empty_id(tmp_path, monkeypatch):
    from tests.workflows.test_planning import paper_project

    project, _, _, clock = paper_project(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        planning.initialize_account(project, "artifacts/accounts/invalid", "", "2026-01-12")
    result = planning.initialize_account(
        project, "artifacts/accounts/initial", "synthetic", "2026-01-12"
    )
    assert read(result["account"])["cash"] == 1000000
    clock[0] = "2026-01-12T15:00:00+08:00"
    with pytest.raises(ValueError, match="complete session"):
        planning.initialize_account(project, "artifacts/accounts/early", "synthetic", "2026-01-12")


def test_experiment_loader_cannot_bypass_failed_dependency_receipt(project):
    output = train_record(project)
    record = read(output / "run.json")
    record["status"] = "FAILED"
    save(output / "run.json", record)
    register(project)
    with pytest.raises(ValueError, match="cannot be consumed"):
        evaluation.experiment_folds(project, {}, output)
    (output / "run.json").unlink()
    with pytest.raises(ValueError, match="Unregistered or changed"):
        evaluation.experiment_folds(project, {}, output)
    with pytest.raises(ValueError, match="Unregistered or changed"):
        runs.dependency_inputs(project, "evaluate", ["artifacts/experiments/example/protocol.json"])


@pytest.mark.parametrize("fault", [None, "changed", "missing", "unregistered_training"])
def test_legacy_account_closure_includes_closing_quotes(project, fault):
    root = project.root
    save(root / "opening.json", {"quotes": {"synthetic": {"open": 10}}})
    save(root / "closing.json", {"quotes": {"synthetic": {"close": 11}}})
    save(
        root / "resolution.json",
        {"quotes": "opening.json", "quotes_sha256": sha(root / "opening.json")},
    )
    save(
        root / "account.json",
        {
            "parent": {
                "resolution": "resolution.json",
                "sha256": sha(root / "resolution.json"),
                "closing": "closing.json",
                "closing_sha256": sha(root / "closing.json"),
            }
        },
    )
    run = root / "artifacts/experiments/legacy"
    save(
        run / "protocol.json",
        {
            "specification": {"data_sha256": sha(root / "data.json")},
            "input_sha256": {"account.json": sha(root / "account.json")},
        },
    )
    save(run / "training_status.json", {"status": "COMPLETE"})
    register(project)
    if fault == "changed":
        save(root / "closing.json", {"quotes": {"synthetic": {"close": 999}}})
        register(project)
    elif fault == "missing":
        (root / "closing.json").unlink()
    elif fault == "unregistered_training":
        record = read(run / "protocol.json")
        record["specification"]["data_sha256"] = "not-registered"
        save(run / "protocol.json", record)
        register(project)
    if fault:
        with pytest.raises((ValueError, FileNotFoundError)):
            runs.dependency_inputs(
                project, "evaluate", ["artifacts/experiments/legacy/protocol.json"]
            )
    else:
        inputs = runs.dependency_inputs(
            project, "evaluate", ["artifacts/experiments/legacy/protocol.json"]
        )
        assert {"account.json", "opening.json", "closing.json", "resolution.json"} <= inputs.keys()
