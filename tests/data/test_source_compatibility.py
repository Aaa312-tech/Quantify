"""A maintenance certificate is valid only for its frozen subject and exact runtime."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from quantfolio import dataset
from quantfolio.storage import read, save, sha


@pytest.fixture
def certificate(tmp_path, monkeypatch):
    source = tmp_path / "runtime"
    source.mkdir()
    for name in ("dataset.py", "simulation.py"):
        (source / name).write_text("# current source\n", encoding="utf-8")
    monkeypatch.setattr(dataset, "__file__", str(source / "dataset.py"))
    paths = SimpleNamespace(root=tmp_path, manifest=tmp_path / "manifest.json")
    subject = tmp_path / "artifacts/experiments/example/protocol.json"
    save(subject, {"historical": True})
    expected = {"simulation.py": "original source hash"}
    current = {"simulation.py": sha(source / "simulation.py")}
    subjects = {
        subject.relative_to(tmp_path).as_posix(): {
            "sha256": sha(subject),
            "source_sha256": expected,
        }
    }
    runtime = {p.name: sha(p) for p in source.glob("*.py")}
    evidence = {}
    for kind in ("EXECUTION_DIFFERENTIAL", "HISTORICAL_LEDGER_PARITY"):
        relative = f"artifacts/checks/example/{kind.lower()}.json"
        save(
            tmp_path / relative,
            {"kind": kind, "status": "PASS", "runtime_sha256": runtime, "subjects": subjects},
        )
        evidence[kind] = {"path": relative, "sha256": sha(tmp_path / relative)}
    file = tmp_path / "artifacts/checks/example/source_compatibility.json"
    save(
        file,
        {
            "schema_version": 1,
            "kind": "EXECUTION_REFACTOR",
            "runtime_sha256": runtime,
            "subjects": subjects,
            "evidence": evidence,
        },
    )
    manifest = {
        p.relative_to(tmp_path).as_posix(): sha(p) for p in tmp_path.glob("artifacts/**/*.json")
    }
    save(paths.manifest, {"files": manifest})
    return paths, subject, expected, current, file


def test_exact_source_match_needs_no_certificate(tmp_path):
    assert dataset.source_compatible(None, tmp_path / "absent", {"x": "a"}, {"x": "a"})


def test_complete_certificate_preserves_original_identity(certificate):
    paths, subject, expected, current, _ = certificate
    before = subject.read_bytes()
    assert dataset.source_compatible(paths, subject, expected, current)
    assert subject.read_bytes() == before
    assert expected != current


@pytest.mark.parametrize(
    "change",
    [
        "missing_certificate",
        "certificate_bytes",
        "malformed_certificate",
        "unknown_subject",
        "subject_bytes",
        "subject_registration",
        "runtime_bytes",
        "extra_runtime_file",
        "missing_runtime_file",
        "wrong_current_map",
        "wrong_expected_map",
        "wrong_kind",
        "wrong_schema",
        "missing_evidence",
        "extra_evidence",
        "evidence_bytes",
        "evidence_registration",
        "failed_report",
        "wrong_report_kind",
        "wrong_report_sources",
        "wrong_report_subject",
        "escaped_report",
    ],
)
def test_incomplete_or_changed_proof_is_rejected(certificate, change):
    paths, subject, expected, current, file = certificate
    record = read(file)
    original_record = deepcopy(record)
    manifest = read(paths.manifest)
    key = file.relative_to(paths.root).as_posix()
    evidence = record["evidence"]["EXECUTION_DIFFERENTIAL"]
    report_file = paths.root / evidence["path"]
    report = read(report_file)
    if change == "missing_certificate":
        del manifest["files"][key]
    elif change in ("certificate_bytes", "malformed_certificate"):
        file.write_text("{}" if change == "certificate_bytes" else "{", encoding="utf-8")
        if change == "malformed_certificate":
            manifest["files"][key] = sha(file)
    elif change == "unknown_subject":
        subject = subject.with_name("another.json")
        save(subject, {"unrelated": True})
        manifest["files"][subject.relative_to(paths.root).as_posix()] = sha(subject)
    elif change == "subject_bytes":
        save(subject, {"historical": False})
    elif change == "subject_registration":
        del manifest["files"][subject.relative_to(paths.root).as_posix()]
    elif change == "runtime_bytes":
        (paths.root / "runtime/dataset.py").write_text("changed", encoding="utf-8")
    elif change == "extra_runtime_file":
        (paths.root / "runtime/extra.py").write_text("extra", encoding="utf-8")
    elif change == "missing_runtime_file":
        (paths.root / "runtime/dataset.py").unlink()
    elif change == "wrong_current_map":
        current = {"simulation.py": "unverified"}
    elif change == "wrong_expected_map":
        expected = {"simulation.py": "another original"}
    elif change == "wrong_kind":
        record["kind"] = "TRAINING"
    elif change == "wrong_schema":
        record["schema_version"] = 2
    elif change == "missing_evidence":
        del record["evidence"]["HISTORICAL_LEDGER_PARITY"]
    elif change == "extra_evidence":
        record["evidence"]["OTHER"] = evidence
    elif change == "evidence_bytes":
        save(report_file, {})
    elif change == "evidence_registration":
        del manifest["files"][evidence["path"]]
    elif change.startswith("wrong_report") or change == "failed_report":
        field, value = {
            "failed_report": ("status", "FAIL"),
            "wrong_report_kind": ("kind", "UNRELATED"),
            "wrong_report_sources": ("runtime_sha256", {}),
            "wrong_report_subject": ("subjects", {}),
        }[change]
        report[field] = value
        save(report_file, report)
        evidence["sha256"] = sha(report_file)
        manifest["files"][evidence["path"]] = evidence["sha256"]
    elif change == "escaped_report":
        # Even a correctly hashed, registered report outside checks cannot authorize execution.
        evidence["path"] = "artifacts/checks/../../outside.json"
        save(paths.root / "outside.json", report)
        manifest["files"][evidence["path"]] = sha(paths.root / "outside.json")
        evidence["sha256"] = manifest["files"][evidence["path"]]
    if record != original_record:
        save(file, record)
        manifest["files"][key] = sha(file)
    save(paths.manifest, manifest)
    assert not dataset.source_compatible(paths, subject, expected, current)
