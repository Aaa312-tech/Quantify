"""Freeze reviewed ETF report evidence while preserving the numerical baseline."""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

from quantfolio.dataset import check_integrity
from quantfolio.planning import source_hashes
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

ROOT = Path.cwd()
PATHS = Paths(ROOT)
PROTOCOL_FILE = ROOT / "configs/protocols/etf_holdings.json"
PROTOCOL = read(PROTOCOL_FILE)
CHECKS = ROOT / PROTOCOL["review_output"]
RAW_FOLDERS = [
    ROOT / "data/raw/etf_reports_2026-11-21",
    ROOT / "data/raw/etf_reports_retry_2026-11-21",
    ROOT / "data/raw/etf_reports_truncation_2026-11-21",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    completion_file = CHECKS / "completion.json"
    require(not completion_file.exists(), "Preserve earlier completion")
    require(sha(PATHS.manifest) == PROTOCOL["baseline_manifest_sha256"], "Registry changed")
    manifest = read(PATHS.manifest)
    baseline_files = dict(manifest["files"])
    require(len(baseline_files) == PROTOCOL["baseline_frozen_files"], "Baseline count")
    require(sha(PATHS.bundle) == PROTOCOL["baseline_bundle_sha256"], "Active bundle changed")
    require(
        sha(PATHS.configuration_path) == PROTOCOL["baseline_configuration_sha256"],
        "Active configuration changed",
    )
    baseline = PROTOCOL["baseline_commit"]
    source_files = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", baseline, "--", "src", "tests"], text=True
    ).splitlines()
    source_hashes_checked = {}
    for relative in source_files:
        original = subprocess.check_output(["git", "show", baseline + ":" + relative])
        require((ROOT / relative).read_bytes() == original, "Source or test changed: " + relative)
        source_hashes_checked[relative] = sha(ROOT / relative)
    require(
        not subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "--", "src", "tests"]
        ).strip(),
        "Unexpected untracked runtime files",
    )
    plan_file = ROOT / "artifacts/plans/dated_industries_2026-11-23/plan.json"
    require(
        sha(plan_file) == baseline_files[plan_file.relative_to(ROOT).as_posix()], "Plan changed"
    )
    require(read(plan_file)["source_sha256"] == source_hashes(), "Plan source binding changed")
    closure = read(CHECKS / "cell_closure.json")
    require(
        closure["status"] == "SOURCE_FIELDS_REVIEWED_EXPOSURE_NOT_ACTIVATED"
        and closure["fund_reports"] == 528
        and closure["funds"] == 22
        and closure["reviewed_rows"]["stocks"] == 33433
        and not closure["model_inputs_changed"],
        "Field review incomplete",
    )
    for receipt, source in [
        ("extraction.json", "extract_source.py"),
        ("extraction_reviewed.json", "followup_source.py"),
        ("pdf_health_review.json", "pdf_health_review_source.py"),
        ("tables_review.json", "review_source.py"),
        ("cell_review.json", "cell_review_source.py"),
        ("cell_closure.json", "closure_source.py"),
    ]:
        require(
            read(CHECKS / receipt)["source_sha256"] == sha(CHECKS / source),
            "Executed research source changed: " + source,
        )
    require(
        read(CHECKS / "pdf_health_review.json")["inspector_sha256"]
        == sha(CHECKS / "pdf_health_source.py"),
        "Original failed health source changed",
    )
    original_integrity = check_integrity(PATHS)

    received, failed, source_paths = 0, [], set()
    for folder in RAW_FOLDERS:
        require(
            read(folder / "collection.json")["source_sha256"]
            == sha(folder / "collector_source.py"),
            "Collector changed after execution",
        )
        for file in sorted(folder.rglob("*.receipt.json")):
            receipt = read(file)
            if receipt["status"] == "RECEIVED":
                target = (ROOT / receipt["path"]).resolve()
                require(target.is_relative_to(ROOT), "Receipt path escapes repository")
                require(sha(target) == receipt["sha256"], "Original PDF changed")
                require(target.stat().st_size == receipt["size_bytes"], "PDF size differs")
                require(target not in source_paths, "Duplicate source receipt")
                source_paths.add(target)
                received += 1
            else:
                failed.append(file.relative_to(ROOT).as_posix())
    raw_pdfs = {file.resolve() for folder in RAW_FOLDERS for file in folder.rglob("*.pdf")}
    require(
        raw_pdfs == source_paths and received == 553 and len(failed) == 2,
        "Original receipt inventory differs",
    )

    logs = CHECKS / "logs"
    require(not logs.exists(), "Preserve prior logs")
    logs.mkdir()
    for name, source in {
        "checks.log": "etf_holdings_checks.log",
        "collection.log": "etf_reports_collection.log",
        "collection_retry.log": "etf_reports_retry.log",
        "truncated_source_retry.log": "etf_reports_truncation.log",
        "initial_extraction.log": "etf_holdings_extraction.log",
        "initial_pdf_health.log": "etf_holdings_pdf_health.log",
        "pdf_health_review.log": "etf_holdings_pdf_health_review.log",
        "extraction_followup.log": "etf_holdings_followup.log",
        "initial_tables.log": "etf_holdings_tables.log",
        "cell_review.log": "etf_holdings_cells.log",
        "visual_render.log": "etf_holdings_visuals.log",
        "cell_closure.log": "etf_holdings_closure.log",
    }.items():
        (logs / name).write_bytes((ROOT / ".git" / source).read_bytes())
    checks_log = (logs / "checks.log").read_text(encoding="utf-8-sig")
    require(
        "375 passed, 1 skipped" in checks_log and "All checks passed!" in checks_log,
        "Local runtime checks missing",
    )
    identity_images = sorted((CHECKS / "visuals").glob("*.png"))
    require(len(identity_images) == 6, "Identity visual inventory")
    save(
        CHECKS / "identity_visual_review.json",
        {
            "recorded_at": now(),
            "scope": "Three overprinted cover dates and all three portfolio headings missing numeric text",
            "images": [
                {"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p)} for p in identity_images
            ],
            "findings": "Original cover dates and visible headings support the seven documented extraction adjudications; the seventh is the independently checked complete replacement PDF.",
            "all_six_images_viewed": True,
            "all_source_pages_visually_reviewed": False,
        },
        immutable=True,
    )
    folders = [
        *RAW_FOLDERS,
        ROOT / "data/processed/etf_holdings_2026-11-21",
        ROOT / "data/processed/etf_holdings_followup_2026-11-21",
        ROOT / "data/processed/etf_holdings_cells_2026-11-21",
        CHECKS,
    ]
    new_files = [
        file
        for folder in folders
        for file in folder.rglob("*")
        if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc"
    ]
    new_files.append(PROTOCOL_FILE)
    for file in new_files:
        key = file.relative_to(ROOT).as_posix()
        require(key not in manifest["files"], "Attempt to replace frozen file: " + key)
        manifest["files"][key] = sha(file)
    save(
        completion_file,
        {
            "status": "VALIDATED_LOCALLY_SOURCE_ONLY",
            "completed_at": now(),
            "baseline_commit": baseline,
            "protocol_sha256": sha(PROTOCOL_FILE),
            "finalizer_sha256": sha(Path(__file__)),
            "baseline_integrity": original_integrity,
            "old_frozen_files_unchanged": len(baseline_files),
            "new_frozen_files_including_completion": len(new_files) + 1,
            "expected_final_registry_files": len(manifest["files"]) + 1,
            "runtime_and_tests_unchanged": source_hashes_checked,
            "configuration_sha256": sha(PATHS.configuration_path),
            "bundle_sha256": sha(PATHS.bundle),
            "paper_plan_sha256": sha(plan_file),
            "paper_dependency_binding_exact": True,
            "cell_closure_sha256": sha(CHECKS / "cell_closure.json"),
            "received_source_receipts_checked": received,
            "failed_source_receipts_preserved": failed,
            "raw_pdf_copies": len(raw_pdfs),
            "fund_reports": 528,
            "publication_notices": 24,
            "funds": 22,
            "stock_rows": 33433,
            "visual_boundary_cases": 141,
            "tests_passed": 375,
            "tests_skipped": 1,
            "refits": 0,
            "new_backtests": 0,
            "new_forward_observations": 0,
            "numerical_inputs_or_results_changed": False,
            "superiority_assessment": "NOT_ESTABLISHED",
            "remaining": [
                "exact historical publication time and as-of policy",
                "report staleness",
                "ETF/CAPCO sector mapping",
                "residual and non-equity economic exposure",
                "registered integration controls and full validation",
                "historical universe bias",
                "other state/data gaps",
                "future paper observations",
                "competition fees and matching",
            ],
        },
        immutable=True,
    )
    manifest["files"][completion_file.relative_to(ROOT).as_posix()] = sha(completion_file)
    save(PATHS.manifest, manifest)
    final_integrity = check_integrity(PATHS)
    require(
        final_integrity["files"] == read(completion_file)["expected_final_registry_files"],
        "Final registry count differs",
    )
    for relative in (
        "README.md",
        "docs/etf_sources.md",
        "docs/etf_holdings.md",
        "docs/remediation.md",
        "docs/data_contract.md",
        "docs/reproducibility.md",
    ):
        file = ROOT / relative
        for target in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "#")):
                continue
            target = unquote(target.split("#")[0])
            require(not target or (file.parent / target).exists(), "Documentation link: " + target)
    save(
        ROOT / ".git/etf_holdings_local_validation.json",
        {
            "status": "PASS",
            "completed_at": now(),
            "integrity": final_integrity,
            "completion_sha256": sha(completion_file),
            "manifest_sha256": sha(PATHS.manifest),
            "documentation_links_checked": True,
        },
        immutable=True,
    )
    print(
        {
            "status": "PASS",
            "old_files": len(baseline_files),
            "new_files": len(new_files) + 1,
            "frozen_total": final_integrity["files"],
            "raw_pdfs": len(raw_pdfs),
        },
        flush=True,
    )


if __name__ == "__main__":
    main()
