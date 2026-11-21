"""Freeze the reviewed ETF source phase without changing active research inputs."""

import json
import subprocess
from pathlib import Path

from quantfolio.storage import now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
OUTPUT = ROOT / "data/processed/etf_exposures_2026-11-21"


def main():
    protocol_file = ROOT / "configs/protocols/etf_exposures.json"
    protocol = read(protocol_file)
    baseline = read(ROOT / ".git/etf_exposure_baseline_integrity.json")
    manifest_file = ROOT / "configs/integrity_manifest.json"
    manifest = read(manifest_file)
    if baseline["status"] != "PASS" or baseline["files"] != protocol["baseline_frozen_files"]:
        raise ValueError("Full baseline integrity verification did not pass")
    if (
        sha(manifest_file) != baseline["manifest_sha256"]
        or baseline["manifest_sha256"] != protocol["baseline_manifest_sha256"]
    ):
        raise ValueError("Original manifest changed")
    changes = subprocess.check_output(
        ["git", "diff", protocol["baseline_commit"], "--name-only"], text=True
    ).splitlines()
    if set(changes) & manifest["files"].keys():
        raise ValueError("A frozen tracked file changed after baseline verification")
    runtime = subprocess.check_output(
        [
            "git",
            "diff",
            protocol["baseline_commit"],
            "--name-only",
            "--",
            "src",
            "tests",
            "configs/project.toml",
            "configs/experiment.json",
            "configs/competition.json",
        ],
        text=True,
    ).strip()
    if runtime:
        raise ValueError("Active runtime or configuration changed")
    tests = ROOT / ".git/etf_exposure_local_tests.log"
    if "375 passed, 1 skipped" not in tests.read_text(encoding="utf-8"):
        raise ValueError("Expected complete local checks")
    source_checks = [
        ("extract_notes_source.py", "notes_source_identity.json", "source_sha256"),
        ("review_notes_source.py", "notes_followup_source_identity.json", "source_sha256"),
        ("review_financial_fields.py", "financial_fields_source_identity.json", "source_sha256"),
        ("render_financial_review.py", "financial_render_identity.json", "source_sha256"),
        ("resolve_financial_layouts.py", "financial_followup_identity.json", "source_sha256"),
        (
            "inventory_followup/render_source.py",
            "inventory_followup/render_adapter_identity.json",
            "adapter_sha256",
        ),
        (
            "inventory_followup/resolve_source.py",
            "inventory_followup/source_identity.json",
            "source_sha256",
        ),
        ("audit_financial_fields.py", "financial_audit.json", "source_sha256"),
        ("build_exposure_history.py", "history_source_identity.json", "source_sha256"),
        ("audit_exposure_history.py", "history_audit.json", "source_sha256"),
        ("check_information_boundaries.py", "information_boundaries.json", "source_sha256"),
    ]
    for source, receipt, key in source_checks:
        if sha(CHECKS / source) != read(CHECKS / receipt)[key]:
            raise ValueError("Executed source was modified: " + source)
    financial = read(CHECKS / "financial_audit.json")
    history = read(CHECKS / "history_audit.json")
    boundaries = read(CHECKS / "information_boundaries.json")
    if any(r["status"] != "PASS" for r in (financial, history, boundaries)):
        raise ValueError("A source or information-boundary audit failed")
    if financial["input_sha256"] != sha(OUTPUT / "financial_observations.json.gz"):
        raise ValueError("Reviewed financial fields changed")
    for name, expected in history["inputs"].items():
        if sha(OUTPUT / name) != expected:
            raise ValueError("Reviewed exposure history changed")
    if (
        sha(ROOT / protocol["baseline_bundle"]) != protocol["baseline_bundle_sha256"]
        or sha(ROOT / "configs/project.toml") != protocol["baseline_configuration_sha256"]
    ):
        raise ValueError("Active input identity changed")
    save(CHECKS / "baseline_integrity.json", baseline, immutable=True)
    save(
        CHECKS / "implementation_identity.json",
        {
            "checked_at": now(),
            "baseline_commit": protocol["baseline_commit"],
            "runtime_sha256": {
                f.relative_to(ROOT).as_posix(): sha(f)
                for f in sorted((ROOT / "src/quantfolio").rglob("*.py"))
            },
            "configuration_sha256": sha(ROOT / "configs/project.toml"),
            "active_bundle_sha256": sha(ROOT / protocol["baseline_bundle"]),
            "executed_evidence_sources": {
                source: sha(CHECKS / source) for source, _, _ in source_checks
            },
            "local_checks": {
                "tests_passed": 375,
                "tests_skipped": 1,
                "skip_reason": "Host does not permit directory symlinks",
                "log_sha256": sha(tests),
            },
        },
        immutable=True,
    )
    candidates = [protocol_file] + [
        f
        for folder in (CHECKS, OUTPUT)
        for f in folder.rglob("*")
        if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"
    ]
    completion = {
        "status": "PASS_SOURCE_CONSTRUCTION_NOT_ACTIVATED",
        "completed_at": now(),
        "source_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(protocol_file),
        "baseline_commit": protocol["baseline_commit"],
        "baseline_frozen_files_unchanged": baseline["files"],
        "new_frozen_files": len(candidates) + 1,
        "reports": 528,
        "funds": 22,
        "additional_note_pages": 375,
        "financial_counts": financial["counts"],
        "nominal_market_reconciliations": len(financial["nominal_market_reconciliations"]),
        "history_counts": history["counts"],
        "timing_counts": boundaries["counts"],
        "audit_sha256": {
            name: sha(CHECKS / name)
            for name in (
                "financial_audit.json",
                "history_audit.json",
                "information_boundaries.json",
                "implementation_identity.json",
                "baseline_integrity.json",
            )
        },
        "tests": {"passed": 375, "skipped": 1},
        "new_refits": 0,
        "new_backtests": 0,
        "forward_observations_added": 0,
        "model_inputs_or_returns_changed": False,
        "complete_economic_exposure_certified": False,
        "historical_publication_time_certified": False,
        "superiority_established": False,
        "limitations": [
            "11000 fund sessions retain undisclosed stock amounts; 10030 retain unmatched named stocks.",
            "Quarterly schedules are partial and stale; identity and benchmark are not daily constituent weights.",
            "Source sectors are separate from CAPCO divisions; signed residuals are not allocated.",
            "Unknown derivatives, security/gold lending treatment and non-equity risks remain outside complete economic-exposure certification.",
            "Whitepaper precedence is confirmed; competition fees/matching and forward observations remain unresolved.",
        ],
        "next_step": "Register coverage, identity and reported-exposure controls with explicit unknown handling, then complete runtime integration, unchanged-input compatibility, refits, independent accounts and paper parity before activation.",
    }
    save(CHECKS / "completion.json", completion, immutable=True)
    candidates.append(CHECKS / "completion.json")
    for file in sorted(candidates):
        name = file.relative_to(ROOT).as_posix()
        if name in manifest["files"]:
            raise ValueError("New source output collides with frozen evidence: " + name)
        manifest["files"][name] = sha(file)
    save(manifest_file, manifest)
    save(
        ROOT / ".git/etf_exposure_freeze.json",
        {
            "status": "PASS",
            "completed_at": now(),
            "baseline_files": baseline["files"],
            "added_files": len(candidates),
            "total_files": len(manifest["files"]),
            "manifest_sha256": sha(manifest_file),
            "completion_sha256": sha(CHECKS / "completion.json"),
        },
        immutable=True,
    )
    print(
        json.dumps(
            {
                "status": completion["status"],
                "frozen_files": len(manifest["files"]),
                "added_files": len(candidates),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
