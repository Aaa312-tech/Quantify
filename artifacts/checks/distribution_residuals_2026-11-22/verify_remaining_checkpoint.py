"""Preserve the published research baseline while binding the financial-notice review."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from verify_amendment_checkpoint import check_file, require

from quantfolio.storage import digest, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
BASELINE = "a5612a40307b1c46a0b394785652ed947f3c32ee"


def main():
    require(
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == BASELINE,
        "Baseline changed",
    )
    parent = json.loads(
        subprocess.check_output(["git", "show", f"{BASELINE}:configs/integrity_manifest.json"])
    )
    manifest = read("configs/integrity_manifest.json")["files"]
    require(all(manifest.get(p) == h for p, h in parent["files"].items()), "Frozen identities")
    with ThreadPoolExecutor(max_workers=4) as pool:
        size = sum(pool.map(check_file, manifest.items()))
    previous = read(BASE / "candidate_checkpoint.json")
    runtime = {}
    paths = subprocess.check_output(["git", "ls-files", "-z", "src/quantfolio"]).decode()
    for path in filter(None, paths.split("\0")):
        expected = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"])
        require(Path(path).read_bytes() == expected, f"Runtime changed: {path}")
        runtime[path] = sha(path)
    require(runtime == previous["unchanged_runtime"], "Runtime inventory")
    active = previous["unchanged_active_artifacts"]
    require(all(sha(p) == h for p, h in active.items()), "Active research artifacts")
    records = read("artifacts/checks/cash_entitlements_2026-11-22/source_review.json")["records"]
    require(digest(records) == previous["prior_records_sha256"], "Prior source records")
    require(sum(bool(r.get("verified_fields")) for r in records) == 1452, "Accepted event scope")

    result = read(BASE / "remaining_amendment_review.json")
    require(result["status"] == "PASS_SCOPED_FINANCIAL_RELATIONS_WITH_LIMITS", "Source review")
    require(not result["failures"], "Source failures remain")
    bindings = {
        "checker_sha256": BASE / "review_remaining_amendments.py",
        "helper_sha256": BASE / "review_candidate_amendments.py",
        "notes_sha256": BASE / "remaining_amendment_notes.json",
        "protocol_sha256": Path("configs/protocols/financial_notice_relations.json"),
    }
    require(all(result[k] == sha(p) for k, p in bindings.items()), "Executed source identities")
    require(all(sha(p) == h for p, h in result["dependencies"].items()), "Source dependencies")
    require(sha(result["text_output"]["path"]) == result["text_output"]["sha256"], "Reader output")
    require(result["documents_reextracted"] == 29 and result["numeric_page_pairs"] == 79, "Pages")
    require(
        result["new_human_full_text_documents"] == 17 and result["new_human_full_text_pages"] == 47,
        "New reading scope",
    )
    require(
        len(result["reused_prior_full_text"]) == 12 and result["new_observed_whole_pages"] == 25,
        "Reused/observed scope",
    )
    require(len(result["financial_checks"]) == 34 and len(result["relations"]) == 55, "Check scope")
    require(
        result["same_catalogue_period_relations"] == 16
        and result["other_catalogue_period_relations"] == 39,
        "Period scope",
    )
    single_reader = [r for r in result["financial_checks"] if r["reader"] == "pypdf"]
    aggregate_limits = [
        r
        for r in single_reader
        if r["status"] == "EXPECTED_TOTAL_DIFFERS_FROM_PUBLISHED_RATIO_PRODUCT"
    ]
    require(
        {r["id"] for r in aggregate_limits} == {"1223720996", "1225404007"}, "Expected total limits"
    )
    require(
        result["new_financial_facts_accepted"] == 0 and result["source_unverified_events"] == 387,
        "Source acceptance changed",
    )
    require(not any(r["event_accepted"] for r in result["relations"]), "Event acceptance changed")
    require(result["prior_records_sha256"] == digest(records), "Source scope changed")
    for key in (
        "account_rounding_certified",
        "active_inputs_changed",
        "complete_archive_relationships_reviewed",
        "whole_goal_complete",
    ):
        require(result[key] is False, f"Unsupported completion: {key}")
    priority = read("configs/protocols/financial_notice_relations.json")
    identifiers = {r["source"]["id"] for r in priority["sources"]}
    earlier = set(priority["priority_filter"]["previously_reviewed_ids"])
    require(
        not identifiers.intersection(earlier) and len(identifiers | earlier) == 53,
        "Title-priority scope",
    )
    log_path = BASE / "remaining_amendment_checks.log"
    log = log_path.read_text(encoding="utf-8-sig")
    require("All checks passed!" in log and "772 passed, 1 skipped" in log, "Local checks")
    guard = previous["prior_reference_guard_reused"]
    require(sha(BASE / "model_guard/verification.json") == guard["sha256"], "Prior model guard")
    checkpoint = {
        "status": "PASS_SOURCE_CHECKPOINT_NOT_FULL_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "helper_sha256": sha(BASE / "verify_amendment_checkpoint.py"),
        "baseline_commit": BASELINE,
        "baseline_frozen_files_preserved": len(parent["files"]),
        "current_frozen_files_verified": len(manifest),
        "verified_bytes": size,
        "unchanged_runtime": runtime,
        "unchanged_active_artifacts": active,
        "source_review_sha256": sha(BASE / "remaining_amendment_review.json"),
        "source_documents": 29,
        "source_pages": 79,
        "new_human_read_documents": 17,
        "new_human_read_pages": 47,
        "reused_prior_documents": 12,
        "observed_pages": 25,
        "financial_reader_checks": 34,
        "same_catalogue_period_relations": 16,
        "other_catalogue_period_relations": 39,
        "fixed_title_priority_documents_bound": 53,
        "expected_aggregate_discrepancies_preserved": aggregate_limits,
        "prior_accepted_events_preserved": 1452,
        "prior_records_sha256": digest(records),
        "residual_events": 387,
        "proposed_events": 95,
        "proposed_fields": 115,
        "local_tests": {"passed": 772, "skipped": 1, "log_sha256": sha(log_path)},
        "prior_reference_guard_reused": guard,
        "new_fits_or_model_reloads": 0,
        "facts_accepted": 0,
        "source_phase_complete": False,
        "whole_goal_complete": False,
        "remaining": previous["remaining"],
    }
    save(BASE / "remaining_checkpoint.json", checkpoint, immutable=True)
    print(checkpoint["status"], len(manifest), "frozen files; runtime and active inputs unchanged")


if __name__ == "__main__":
    main()
