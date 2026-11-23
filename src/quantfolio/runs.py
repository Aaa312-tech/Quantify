"""Dependency receipts and a rebuildable catalogue; adoption remains in project config."""

from __future__ import annotations

import tomllib
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from .integrity import local_path, register_output, source_closure, verify_files
from .storage import now, read, save, sha

OPERATIONS = {"train": "training", "evaluate": "evaluation", "plan": "planning"}


def _configuration_files(paths, operation, config):
    names = [config["archive"]] if "archive" in config else []
    if operation == "plan":
        names.append("configs/competition.json")
    result = {}
    for name in names:
        path = local_path(paths, name)
        if not path.is_relative_to(paths.root / "configs"):
            raise ValueError("Configuration dependency must be inside configs")
        raw = path.read_bytes()
        result[name] = {"sha256": sha256(raw).hexdigest(), "text": raw.decode("utf-8")}
    return result


def _files(paths, directory):
    return {
        local_path(paths, p).relative_to(paths.root).as_posix(): sha(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file() and p != directory / "run.json"
    }


def _parents(paths, names, active, seen):
    result = {}
    for name in names:
        path = local_path(paths, name)
        relative = path.relative_to(paths.root).as_posix()
        if relative in active:
            raise ValueError(f"Cyclic artifact dependency: {relative}")
        if relative in seen:
            continue
        record_file = path.parent / "run.json"
        if (
            record_file.exists()
            or record_file.relative_to(paths.root).as_posix() in read(paths.manifest)["files"]
        ):
            record = verify_run(
                paths, record_file, active=active | {relative}, current_sources=True
            )
            result.update(record["inputs"])
            result.update(record["outputs"])
            result.update(verify_files(paths, [record_file.relative_to(paths.root).as_posix()]))
            seen.add(relative)
            continue
        result.update(verify_files(paths, [relative]))
        record = read(path)
        parents = []
        if path.name == "protocol.json":
            required = [path.parent / "training_status.json"]
            result.update(
                verify_files(paths, [p.relative_to(paths.root).as_posix() for p in required])
            )
            result.update(verify_files(paths, _files(paths, path.parent)))
            for field in ("data_sha256", "plan_sha256"):
                expected = record.get("specification", {}).get(field)
                if expected is not None:
                    matches = [
                        key
                        for key, value in read(paths.manifest)["files"].items()
                        if value == expected
                    ]
                    if not matches:
                        raise ValueError(f"Unregistered training dependency: {field}")
                    result.update(verify_files(paths, matches))
        elif path.name == "evaluation_summary.json":
            parents.append(record["source_run"] + "/protocol.json")
            result.update(verify_files(paths, _files(paths, path.parent)))
        references = {}
        for key in ("plan", "quotes"):
            if isinstance(record.get(key), str):
                if record[key] in active | {relative}:
                    raise ValueError(f"Cyclic artifact dependency: {record[key]}")
                parents.append(record[key])
                references[record[key]] = record.get(key + "_sha256")
        parent = record.get("parent") or record.get("account", {}).get("parent")
        if parent:
            parents.extend([parent["resolution"], parent["closing"]])
            references.update(
                {
                    parent["resolution"]: parent["sha256"],
                    parent["closing"]: parent["closing_sha256"],
                }
            )
        if any(sha(local_path(paths, key)) != value for key, value in references.items()):
            raise ValueError(f"Parent reference changed: {relative}")
        inputs = record.get("input_sha256", {})
        configurations = {
            key: value for key, value in inputs.items() if Path(key).parts[0] == "configs"
        }
        checked = {key: sha(local_path(paths, key)) for key in configurations}
        checked.update(verify_files(paths, inputs.keys() - configurations.keys()))
        if checked != inputs:
            raise ValueError(f"Parent input hashes differ: {relative}")
        result.update(checked)
        parents.extend(
            key
            for key in inputs
            if Path(key).name in {"protocol.json", "evaluation_summary.json", "account.json"}
        )
        result.update(_parents(paths, parents, active | {relative}, seen))
        seen.add(relative)
    return result


def dependency_inputs(paths, operation, parents, configuration=None, active=frozenset()):
    config = paths.config if configuration is None else configuration
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown run operation: {operation}")
    expected_parents = {"train": 0, "evaluate": 1, "plan": 2}
    if len(parents) != expected_parents[operation] or len(set(parents)) != len(parents):
        raise ValueError("Incomplete parent dependency list")
    if operation == "evaluate" and Path(parents[0]).name != "protocol.json":
        raise ValueError("Evaluation requires its training protocol")
    if operation == "plan" and not any(Path(p).name == "evaluation_summary.json" for p in parents):
        raise ValueError("Plan requires its active evaluation")
    keys = ["bundle", "protocol"]
    if operation == "plan":
        keys += ["calendar", "historical_status_review"]
    names = [config[key] for key in keys if key in config]
    if not {"bundle", "protocol"} <= config.keys():
        raise ValueError("Run is missing its dataset or protocol")
    result = verify_files(paths, names)
    result.update(_parents(paths, parents, active, set()))
    return dict(sorted(result.items()))


@contextmanager
def audit_run(paths, operation, output, parents=(), parameters=None):
    """Check before reading and again before publishing. Never cache file metadata."""
    output = local_path(paths, output)
    if operation == "plan":
        parents = [*parents, paths.config["evaluation"] + "/evaluation_summary.json"]
    parents = [local_path(paths, p).relative_to(paths.root).as_posix() for p in parents]
    inputs = dependency_inputs(paths, operation, parents)
    config_bytes = paths.configuration_path.read_bytes()
    effective = tomllib.loads(config_bytes.decode("utf-8"))
    if effective != paths.config:
        raise ValueError("Configuration changed after it was loaded")
    sources = source_closure([OPERATIONS[operation]])
    configuration_files = _configuration_files(paths, operation, effective)
    record = {
        "schema_version": 2,
        "kind": "RESEARCH_RUN",
        "operation": operation,
        "created_at": now(),
        "status": "FAILED",
        "configuration": {
            "path": paths.configuration_path.relative_to(paths.root).as_posix(),
            "sha256": sha256(config_bytes).hexdigest(),
            "text": config_bytes.decode("utf-8"),
            "effective": effective,
        },
        "source_sha256": sources,
        "configuration_files": configuration_files,
        "inputs": inputs,
        "parents": parents,
        "parameters": parameters or {},
    }
    try:
        yield
        if (
            inputs != dependency_inputs(paths, operation, parents)
            or paths.configuration_path.read_bytes() != config_bytes
            or sources != source_closure([OPERATIONS[operation]])
            or configuration_files != _configuration_files(paths, operation, effective)
        ):
            raise ValueError("Run dependencies changed during execution")
        summary = {
            "train": "training_status.json",
            "evaluate": "evaluation_summary.json",
            "plan": "plan.json",
        }[operation]
        record["status"] = read(output / summary)["status"]
        if operation == "train":
            specification = read(output / "protocol.json")["specification"]
            record["parameters"].update(
                {
                    key: specification[key]
                    for key in ("model_candidates", "rule_configs", "selected_folds")
                    if key in specification
                }
            )
    except BaseException as error:
        record["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        if output.is_dir():
            record["outputs"] = _files(paths, output)
            save(output / "run.json", record, immutable=True)
            register_output(paths, output)


def verify_run(paths, file, *, active=frozenset(), current_sources=False):
    file = local_path(paths, file)
    relative = file.relative_to(paths.root).as_posix()
    if relative in active:
        raise ValueError(f"Cyclic artifact dependency: {relative}")
    verify_files(paths, [relative])
    record = read(file)
    if record.get("schema_version") != 2 or record.get("kind") != "RESEARCH_RUN":
        raise ValueError("Unknown run record format")
    operation = record["operation"]
    statuses = {
        "train": {"COMPLETE", "PARTIAL"},
        "evaluate": {"COMPLETE", "PARTIAL"},
        "plan": {"FROZEN_CONDITIONAL_PLAN", "BLOCKED"},
    }
    if operation not in OPERATIONS or record["status"] not in statuses[operation]:
        raise ValueError("Run cannot be consumed")
    configuration = record["configuration"]
    if (
        sha256(configuration["text"].encode("utf-8")).hexdigest() != configuration["sha256"]
        or tomllib.loads(configuration["text"]) != configuration["effective"]
    ):
        raise ValueError("Recorded configuration differs")
    configuration_files = _configuration_files(paths, operation, configuration["effective"])
    if set(configuration_files) != set(record["configuration_files"]) or any(
        sha256(item["text"].encode("utf-8")).hexdigest() != item["sha256"]
        for item in record["configuration_files"].values()
    ):
        raise ValueError("Incomplete configuration dependency closure")
    if current_sources and configuration_files != record["configuration_files"]:
        raise ValueError("Run configuration dependencies changed")
    inputs = dependency_inputs(
        paths, operation, record["parents"], configuration["effective"], active | {relative}
    )
    if inputs != record["inputs"] or not record["outputs"]:
        raise ValueError("Incomplete run dependency closure")
    if _files(paths, file.parent) != record["outputs"]:
        raise ValueError("Run output inventory differs")
    verify_files(paths, record["outputs"])
    current = source_closure([OPERATIONS[operation]])
    if set(current) != set(record["source_sha256"]):
        raise ValueError("Incomplete source dependency closure")
    if current_sources and current != record["source_sha256"]:
        raise ValueError("Run source dependencies changed; exact regression is required")
    return record


def run_index(paths):
    """Build from frozen records, including failures; never rank or change adoption."""
    manifest = read(paths.manifest)["files"]
    names_by_hash: dict[str, list[str]] = {}
    for name, checksum in manifest.items():
        names_by_hash.setdefault(checksum, []).append(name)
    active_evaluation = paths.config.get("evaluation")
    active_training = None
    if active_evaluation:
        name = active_evaluation + "/evaluation_summary.json"
        verify_files(paths, [name])
        active_training = read(local_path(paths, name)).get("source_run")
    rows = []
    for name in sorted(manifest):
        path = Path(name)
        if path.parts[:2] not in {
            ("artifacts", "experiments"),
            ("artifacts", "evaluations"),
            ("artifacts", "plans"),
        }:
            continue
        if path.name not in {"run.json", "protocol.json", "evaluation_summary.json", "plan.json"}:
            continue
        if path.name != "run.json" and (path.parent / "run.json").as_posix() in manifest:
            continue
        verify_files(paths, [name])
        record = read(local_path(paths, name))
        folder = path.parent.as_posix()
        if path.name == "run.json":
            if record.get("schema_version") != 2 or record.get("kind") != "RESEARCH_RUN":
                raise ValueError(f"Unknown run record format: {name}")
            row = {
                key: record[key]
                for key in (
                    "operation",
                    "status",
                    "parents",
                    "parameters",
                    "outputs",
                    "configuration",
                )
            }
            row["format"] = "dependency_record"
        else:
            specification = record.get("specification", {})
            status_file = path.parent / "training_status.json"
            status = record.get("status", "INCOMPLETE")
            if status_file.as_posix() in manifest:
                verify_files(paths, [status_file.as_posix()])
                status = read(local_path(paths, status_file))["status"]
            row = {
                "format": "legacy",
                "operation": {
                    "protocol.json": "train",
                    "evaluation_summary.json": "evaluate",
                    "plan.json": "plan",
                }[path.name],
                "status": status,
                "parents": record.get("input_sha256", {})
                or {
                    key: {"sha256": value, "paths": names_by_hash.get(value, [])}
                    for key, value in specification.items()
                    if key in {"data_sha256", "plan_sha256"}
                },
                "parameters": {
                    key: specification[key]
                    for key in (
                        "model_candidates",
                        "rule_configs",
                        "selected_folds",
                        "selection_mode",
                        "turnover_policy",
                    )
                    if key in specification
                },
                "costs": list(record.get("results", {})),
                "outputs": {
                    key: value for key, value in manifest.items() if key.startswith(folder + "/")
                },
                "configuration": record.get("active_configuration"),
            }
        row.update(
            path=folder,
            configured=folder in {active_evaluation, active_training},
            adopted=folder in {active_evaluation, active_training} and row["status"] == "COMPLETE",
        )
        rows.append(row)
    return {
        "schema_version": 1,
        "adoption_source": paths.configuration_path.relative_to(paths.root).as_posix(),
        "runs": rows,
    }
