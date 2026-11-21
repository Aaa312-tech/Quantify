"""Verify supplemental source evidence and preserve the published research baseline."""

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from quantfolio.storage import digest, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
BASELINE = "9a7d7fff7768aa09bf47de4e8e4a3cd2912f01d8"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_file(item):
    path, expected = item
    require(sha(path) == expected, f"Frozen file changed: {path}")
    return Path(path).stat().st_size


def main():
    require(
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == BASELINE,
        "Baseline",
    )
    parent = json.loads(
        subprocess.check_output(["git", "show", f"{BASELINE}:configs/integrity_manifest.json"])
    )
    manifest = read("configs/integrity_manifest.json")
    for path, expected in parent["files"].items():
        require(manifest["files"].get(path) == expected, f"Frozen identity changed: {path}")
    count, size = 0, 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for file_size in pool.map(check_file, manifest["files"].items()):
            count += 1
            size += file_size
            if count % 5000 == 0:
                print(f"Verified {count} frozen files", flush=True)
    runtime = {}
    for path in filter(
        None,
        subprocess.check_output(["git", "ls-files", "-z", "src/quantfolio"]).decode().split("\0"),
    ):
        previous = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"])
        require(Path(path).read_bytes() == previous, f"Runtime changed: {path}")
        runtime[path] = sha(path)
    prior_checkpoint = read(BASE / "notice_checkpoint.json")
    require(runtime == prior_checkpoint["unchanged_runtime"], "Runtime inventory differs")
    active = prior_checkpoint["unchanged_active_artifacts"]
    for path, expected in active.items():
        require(sha(path) == expected, f"Active artifact changed: {path}")
    review_path = "artifacts/checks/cash_entitlements_2026-11-22/source_review.json"
    old_records = read(review_path)["records"]
    supplemental = read(BASE / "supplemental_relation_review.json")
    financial = read(BASE / "financial_amendment_review.json")
    checks = [
        (supplemental, "verify_supplemental_relation.py", "PASS_SCOPED_SUPPLEMENTAL_RELATION"),
        (financial, "review_financial_amendments.py", "PASS_SCOPED_FINANCIAL_AMENDMENT_REVIEW"),
    ]
    for result, checker, status in checks:
        require(result["status"] == status, "Source relation check failed")
        require(result["checker_sha256"] == sha(BASE / checker), "Executed checker changed")
        for path, expected in result["dependencies"].items():
            require(sha(path) == expected, "Source dependency changed")
        require(result["new_financial_facts_accepted"] == 0, "Financial acceptance changed")
    require(
        supplemental["all_prior_records_sha256"] == digest(old_records), "Original records changed"
    )
    require(
        sum(bool(r.get("verified_fields")) for r in old_records) == 1452, "Prior acceptance scope"
    )
    text = load_gzip(BASE / "financial_amendment_text.json.gz")["documents"]
    require(
        financial["text_output"]["sha256"] == sha(BASE / "financial_amendment_text.json.gz"),
        "Reader evidence",
    )
    require(len(text) == 10 and financial["numeric_page_pairs"] == 22, "Financial reading scope")
    relations = financial["event_relations"]
    require(
        len(relations) == 22
        and sum(r["disposition"] == "SAME_PERIOD_CURRENT_TERMS_AGREE" for r in relations) == 6,
        "Period scope",
    )
    archive_index = load_gzip(BASE / "notice_amendment_index.json.gz")
    require(
        len(archive_index["documents"]) == 1135 and len(archive_index["events"]) == 387,
        "Original archive scope",
    )
    acquisition = read(BASE / "supplemental_acquisition.json")
    require(
        acquisition["supplement_documents"] == 1 and acquisition["supplement_pages"] == 2,
        "Supplement scope",
    )
    log_path = BASE / "amendment_checks.log"
    log = log_path.read_text(encoding="utf-8-sig")
    require(re.search(r"772 passed, 1 skipped", log), "Local checks did not pass")
    guard = prior_checkpoint["prior_reference_guard_reused"]
    require(
        sha(BASE / "model_guard/verification.json") == guard["sha256"], "Prior model guard changed"
    )
    guard_receipt = read(BASE / "model_guard/verification.json")
    require(
        guard_receipt["status"] == "PASS" and guard_receipt["models_reloaded"] == 48, "Prior guard"
    )
    result = {
        "status": "PASS_SOURCE_CHECKPOINT_NOT_FULL_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "baseline_commit": BASELINE,
        "baseline_frozen_files_preserved": len(parent["files"]),
        "current_frozen_files_verified": count,
        "verified_bytes": size,
        "unchanged_runtime": runtime,
        "unchanged_active_artifacts": active,
        "supplemental_review_sha256": sha(BASE / "supplemental_relation_review.json"),
        "financial_amendment_review_sha256": sha(BASE / "financial_amendment_review.json"),
        "original_archive_documents": 1135,
        "original_archive_pages": 3780,
        "supplement_documents": 1,
        "supplement_pages": 2,
        "new_financial_amendment_documents": 10,
        "new_financial_amendment_pages": 22,
        "same_period_candidate_relations": 6,
        "other_period_relations": 16,
        "prior_accepted_events_preserved": 1452,
        "prior_records_sha256": digest(old_records),
        "residual_events": 387,
        "proposed_events": 95,
        "proposed_fields": 115,
        "local_tests": {"passed": 772, "skipped": 1, "log_sha256": sha(log_path)},
        "prior_reference_guard_reused": guard,
        "new_fits_or_model_reloads": 0,
        "facts_accepted": 0,
        "source_phase_complete": False,
        "whole_goal_complete": False,
        "remaining": [
            "Complete body relationships and explicit dispositions for all 387 residual events",
            "Accept scoped source fields only after their complete source gates pass",
            "Register any later integration separately before changing active data or retraining",
            "Continue status/quote gaps, historical universe/timing, exposure and competition execution work",
            "Retain exhausted alpha budget and await genuinely new forward observations",
        ],
    }
    save(BASE / "amendment_checkpoint.json", result, immutable=True)
    print(result["status"], count, "frozen files; 39 runtime files unchanged", flush=True)


if __name__ == "__main__":
    main()
