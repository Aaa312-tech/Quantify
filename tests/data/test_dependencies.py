"""Dependency checks fail closed without turning unrelated source edits into drift."""

import hashlib
import os

import pytest

from quantfolio import dataset, integrity
from quantfolio.settings import Paths
from quantfolio.storage import read, save, sha


def test_runtime_sources_detect_same_size_edits_and_inventory_changes(tmp_path):
    first = tmp_path / "a.py"
    first.write_bytes(b"VALUE = 1\n")
    (tmp_path / "z.py").write_bytes(b"VALUE = 2\n")
    (tmp_path / "notes.txt").write_text("not source", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "hidden.py").write_text("nested source", encoding="utf-8")

    before = integrity.runtime_sources(tmp_path)
    assert list(before) == ["a.py", "z.py"]
    assert before["a.py"] == hashlib.sha256(b"VALUE = 1\n").hexdigest()
    stamp = first.stat()
    first.write_bytes(b"VALUE = 3\n")
    os.utime(first, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    (tmp_path / "z.py").unlink()
    (tmp_path / "b.py").write_bytes(b"VALUE = 4\n")

    after = integrity.runtime_sources(tmp_path)
    assert list(after) == ["a.py", "b.py"]
    assert after["a.py"] == hashlib.sha256(b"VALUE = 3\n").hexdigest()
    assert after["a.py"] != before["a.py"]


def register(paths):
    save(paths.manifest, {"files": {}})
    integrity.register_output(paths, paths.root / "artifacts")


@pytest.fixture
def source_tree(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    for name, code in {
        "__init__": "",
        "training": "from .rules import VALUE\nfrom . import storage\n",
        "rules": "VALUE = 1\n",
        "storage": "import json\n",
        "reporting": "TITLE = 'unrelated'\n",
        "planning": "def compute():\n    from .rules import VALUE\n    return VALUE\n",
    }.items():
        (directory / (name + ".py")).write_text(code, encoding="utf-8")
    return directory


def test_transitive_local_imports_and_unrelated_modules(source_tree):
    result = integrity.source_closure(["training", "planning"], source_tree)
    assert set(result) == {"__init__.py", "training.py", "rules.py", "storage.py", "planning.py"}
    (source_tree / "reporting.py").write_text("TITLE = 'changed'", encoding="utf-8")
    assert integrity.source_closure(["training", "planning"], source_tree) == result
    (source_tree / "rules.py").write_text("VALUE = 2", encoding="utf-8")
    assert integrity.source_closure(["training", "planning"], source_tree) != result


@pytest.mark.parametrize(
    "code",
    [
        "import importlib as loader\nloader.import_module('quantfolio.rules')",
        "from importlib import import_module as load\nload('quantfolio.rules')",
        "__import__('quantfolio.rules')",
        "loader = __import__\nloader('quantfolio.rules')",
        "exec('import rules')",
        "eval('1')",
        "from .. import rules",
        "from quantfolio import rules",
    ],
)
def test_unresolved_dynamic_dependencies_are_rejected(source_tree, code):
    (source_tree / "training.py").write_text(code, encoding="utf-8")
    with pytest.raises(ValueError, match="import|dependency"):
        integrity.source_closure(["training"], source_tree)


def test_absolute_package_imports_and_dependency_cycles(source_tree):
    (source_tree / "training.py").write_text(
        "import quantfolio.rules\nfrom quantfolio.storage import save", encoding="utf-8"
    )
    (source_tree / "rules.py").write_text("from . import training", encoding="utf-8")
    assert len(integrity.source_closure(["training"], source_tree)) == 4


@pytest.fixture
def certificate(tmp_path, source_tree, monkeypatch):
    monkeypatch.setattr(integrity, "__file__", str(source_tree / "integrity.py"))
    config = tmp_path / "configs"
    config.mkdir()
    (config / "project.toml").write_text('manifest="configs/manifest.json"', encoding="utf-8")
    paths = Paths(tmp_path)
    subject = tmp_path / "artifacts/experiments/example/protocol.json"
    save(subject, {"original": True})
    expected = {"rules.py": "original hash"}
    subjects = {
        subject.relative_to(tmp_path).as_posix(): {
            "sha256": sha(subject),
            "source_sha256": expected,
        }
    }
    runtime = {p.name: sha(p) for p in source_tree.glob("*.py")}
    output = tmp_path / "artifacts/checks/complete"
    report = {
        "status": "PASS",
        "scope": "full",
        "baseline_commit": "baseline",
        "new_model_fits": 0,
        "source_sha256": runtime,
        "models": [{"max_error": 0}],
        "training": {"windows": [{}], "fit_inputs": [{}, {}]},
        "history": {"accounts": 25, "ledger_files": 2},
        "paper": {"accounts": 2, "sessions": 20},
        "features": {"securities": 10, "frozen_features": {"features": 57}},
    }
    scope = tmp_path / "artifacts/checks/baseline/scope.json"
    save(scope, {"baseline_commit": "baseline", "expected": integrity.scope_counts(report)})
    (config / "regression.toml").write_text(
        f'baseline_commit="baseline"\nscope="{scope.relative_to(tmp_path).as_posix()}"\nscope_sha256="{sha(scope)}"\n',
        encoding="utf-8",
    )
    report["profile_sha256"] = sha(config / "regression.toml")
    save(output / "result.json", report)
    evidence = {}
    for filename, kind in [
        ("differential", "EXECUTION_DIFFERENTIAL"),
        ("history", "HISTORICAL_LEDGER_PARITY"),
    ]:
        file = output / (filename + ".json")
        fields = (
            {key: report[key] for key in ("features", "training", "models")}
            if filename == "differential"
            else report["history"]
        )
        save(
            file,
            {
                "status": "PASS",
                "scope": "full",
                "kind": kind,
                "runtime_sha256": runtime,
                "subjects": subjects,
                "profile_sha256": report["profile_sha256"],
                **fields,
            },
        )
        evidence[kind] = {"path": file.relative_to(tmp_path).as_posix(), "sha256": sha(file)}
    save(
        output / "source_compatibility.json",
        {
            "schema_version": 1,
            "kind": "EXECUTION_REFACTOR",
            "baseline_commit": "baseline",
            "runtime_sha256": runtime,
            "subjects": subjects,
            "evidence": evidence,
        },
    )
    integrity.write_dependency_certificate(paths, output)
    register(paths)
    return paths, subject, expected, source_tree, output


def test_registered_proof_survives_an_unrelated_report_edit(certificate):
    paths, subject, expected, source, _ = certificate
    assert integrity.dependency_compatible(paths, subject, expected, source)
    (source / "reporting.py").write_text("TITLE = 'new'", encoding="utf-8")
    assert integrity.dependency_compatible(paths, subject, expected, source)
    (source / "rules.py").write_text("VALUE = 99", encoding="utf-8")
    assert not integrity.dependency_compatible(paths, subject, expected, source)


def test_dataset_loader_uses_scoped_certificate(certificate, monkeypatch):
    paths, subject, expected, source, _ = certificate
    monkeypatch.setattr(dataset, "__file__", str(source / "dataset.py"))
    current = {name: sha(source / name) for name in expected}
    assert dataset.source_compatible(paths, subject, expected, current)
    (source / "reporting.py").write_text("TITLE = 'different'", encoding="utf-8")
    assert dataset.source_compatible(paths, subject, expected, current)


def test_full_registration_emits_both_certificate_formats(certificate, monkeypatch):
    from quantfolio import regression

    paths, _, _, _, original = certificate
    output = paths.root / "artifacts/checks/registered"
    report = read(original / "result.json")
    subjects = read(original / "source_compatibility.json")["subjects"]
    for name in ("result.json", "differential.json", "history.json"):
        record = read(original / name)
        if name == "history.json":
            record["records"] = [{}, {}]
        save(output / name, record)
    baseline = {
        "baseline_commit": "baseline",
        "profile_sha256": report["profile_sha256"],
        "subjects": subjects,
        "expected": integrity.scope_counts(report),
    }
    monkeypatch.setattr(regression, "read_baseline", lambda paths: baseline)
    monkeypatch.setattr(regression, "check_inputs", lambda *args: None)
    monkeypatch.setattr(regression, "runtime_sources", lambda: report["source_sha256"])
    regression.register_compatibility(paths, output)
    assert read(output / "source_compatibility.json")["schema_version"] == 1
    assert read(output / "dependency_compatibility.json")["schema_version"] == 2


@pytest.mark.parametrize(
    "fault",
    [
        "format",
        "kind",
        "omitted_source",
        "roots",
        "legacy_kind",
        "legacy_subject",
        "missing_evidence",
        "evidence_status",
        "evidence_scope",
        "evidence_values",
        "result_scope",
        "result_models",
        "result_accounts",
        "result_refit",
        "profile",
        "escape",
        "unregistered",
        "missing",
        "changed",
    ],
)
def test_forged_or_incomplete_compatibility_is_rejected(certificate, fault):
    paths, subject, expected, source, output = certificate
    file = output / "dependency_compatibility.json"
    record = read(file)
    if fault in {"format", "kind", "omitted_source", "roots", "escape"}:
        closure = record["closures"][subject.relative_to(paths.root).as_posix()]
        if fault == "format":
            record["schema_version"] = 3
        elif fault == "kind":
            record["kind"] = "UNPROVEN"
        elif fault == "omitted_source":
            closure["source_sha256"].pop("rules.py")
        elif fault == "roots":
            closure["roots"] = ["reporting"]
        else:
            record["legacy_certificate"]["path"] = "../outside.json"
    elif fault.startswith("legacy") or fault.startswith("evidence") or fault == "missing_evidence":
        legacy_file = output / "source_compatibility.json"
        legacy = read(legacy_file)
        if fault == "legacy_kind":
            legacy["kind"] = "UNPROVEN"
        elif fault == "legacy_subject":
            legacy["subjects"] = {}
        elif fault == "missing_evidence":
            legacy["evidence"].pop("HISTORICAL_LEDGER_PARITY")
        else:
            proof = output / "differential.json"
            item = read(proof)
            if fault == "evidence_status":
                item["status"] = "FAIL"
            elif fault == "evidence_scope":
                item["scope"] = "core"
            else:
                item["models"][0]["max_error"] = 1e-15
            save(proof, item)
            legacy["evidence"]["EXECUTION_DIFFERENTIAL"]["sha256"] = sha(proof)
        save(legacy_file, legacy)
        record["legacy_certificate"]["sha256"] = sha(legacy_file)
    elif fault.startswith("result"):
        result = output / "result.json"
        item = read(result)
        if fault == "result_scope":
            item["scope"] = "core"
        elif fault == "result_models":
            item["models"] = []
        elif fault == "result_accounts":
            item["history"]["accounts"] -= 1
        else:
            item["new_model_fits"] = 1
        save(result, item)
        record["regression"]["sha256"] = sha(result)
    elif fault == "profile":
        (paths.root / "configs/regression.toml").write_text('scope="absent"', encoding="utf-8")
    save(file, record)
    register(paths)
    if fault == "unregistered":
        manifest = read(paths.manifest)
        del manifest["files"][record["legacy_certificate"]["path"]]
        save(paths.manifest, manifest)
    elif fault == "missing":
        (output / "result.json").unlink()
    elif fault == "changed":
        (output / "result.json").write_text("{}", encoding="utf-8")
    assert not integrity.dependency_compatible(paths, subject, expected, source)


def test_missing_source_or_unknown_subject_is_not_compatible(certificate):
    paths, subject, expected, source, _ = certificate
    assert not integrity.dependency_compatible(paths, paths.root / "unknown.json", expected, source)
    (source / "rules.py").unlink()
    assert not integrity.dependency_compatible(paths, subject, expected, source)


def test_partial_regression_cannot_create_a_certificate(certificate):
    paths, _, _, _, output = certificate
    report = read(output / "result.json")
    report["scope"] = "core"
    save(output / "result.json", report)
    with pytest.raises(ValueError, match="full regression"):
        integrity.write_dependency_certificate(paths, output)


def test_release_scan_still_detects_an_unrelated_archived_change(certificate):
    paths, subject, _, _, _ = certificate
    archive = paths.root / "artifacts/checks/old/failure.json"
    save(archive, {"status": "FAIL"})
    integrity.register_output(paths, archive.parent)
    save(archive, {"status": "PASS"})
    assert integrity.verify_files(paths, [subject.relative_to(paths.root).as_posix()])
    with pytest.raises(ValueError, match="integrity"):
        dataset.check_integrity(paths)


def test_registration_and_path_guards(certificate):
    paths, subject, _, _, _ = certificate
    with pytest.raises(ValueError, match="escapes"):
        integrity.verify_files(paths, ["../outside.json"])
    save(subject, {"changed": True})
    with pytest.raises(ValueError, match="Frozen output"):
        integrity.register_output(paths, subject.parent)
    with pytest.raises(ValueError, match="Unregistered or changed"):
        integrity.verify_files(paths, [subject.relative_to(paths.root).as_posix()])
