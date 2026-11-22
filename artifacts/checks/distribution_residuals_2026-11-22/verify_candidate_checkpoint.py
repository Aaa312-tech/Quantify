"""Bind the candidate-issuer notice review without changing the research baseline."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from verify_amendment_checkpoint import check_file, require

from quantfolio.storage import digest, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
BASELINE = "c5be18bf74b1d871419c6b1aac3c5f37b66d31d0"


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

    previous = read(BASE / "amendment_checkpoint.json")
    paths = subprocess.check_output(["git", "ls-files", "-z", "src/quantfolio"]).decode()
    runtime = {}
    for path in filter(None, paths.split("\0")):
        expected = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"])
        require(Path(path).read_bytes() == expected, f"Runtime changed: {path}")
        runtime[path] = sha(path)
    require(runtime == previous["unchanged_runtime"], "Runtime inventory")
    active = previous["unchanged_active_artifacts"]
    require(all(sha(p) == h for p, h in active.items()), "Active research inputs changed")
    records = read("artifacts/checks/cash_entitlements_2026-11-22/source_review.json")["records"]
    require(digest(records) == previous["prior_records_sha256"], "Prior source records changed")
    require(sum(bool(r.get("verified_fields")) for r in records) == 1452, "Accepted event scope")

    review = read(BASE / "candidate_amendment_review.json")
    bindings = {
        "checker_sha256": BASE / "review_candidate_amendments.py",
        "protocol_sha256": Path("configs/protocols/candidate_amendment_relations.json"),
        "notes_sha256": BASE / "candidate_amendment_notes.json",
    }
    require(all(review[key] == sha(path) for key, path in bindings.items()), "Executed review")
    require(all(sha(p) == h for p, h in review["dependencies"].items()), "Review dependencies")
    require(review["status"] == "PASS_SCOPED_RELATIONS_WITH_DISCLOSURE_LIMITS", "Review status")
    output = review["text_output"]
    require(sha(output["path"]) == output["sha256"], "Reader output changed")
    documents = load_gzip(output["path"])["documents"]
    require(len(documents) == review["documents_read_in_full"] == 14, "Document scope")
    require(review["pages_read_in_full"] == review["numeric_page_pairs"] == 35, "Page scope")
    require(review["whole_pages_observed"] == 20, "Observed page scope")
    require(review["schemes"] == len(review["scheme_arithmetic"]) == 15, "Financial schemes")
    require(len(review["reader_checks"]) == 30, "Reader checks")
    relations = review["event_relations"]
    require(len(relations) == 22 and not any(r["event_accepted"] for r in relations), "Relations")
    require(
        review["same_period_relations"] == 6 and review["other_period_relations"] == 16, "Periods"
    )
    require(review["new_financial_facts_accepted"] == 0, "Facts accepted")
    require(review["all_residual_events_still_unverified"] == 387, "Residual scope")
    for key in (
        "active_inputs_changed",
        "account_rounding_certified",
        "full_archive_relationships_complete",
        "whole_goal_complete",
    ):
        require(review[key] is False, f"Unsupported completion: {key}")

    log_path = BASE / "candidate_amendment_checks.log"
    log = log_path.read_text(encoding="utf-8-sig")
    require("All checks passed!" in log and "772 passed, 1 skipped" in log, "Local checks")
    guard = previous["prior_reference_guard_reused"]
    require(sha(BASE / "model_guard/verification.json") == guard["sha256"], "Prior model guard")
    result = {
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
        "source_review_sha256": sha(BASE / "candidate_amendment_review.json"),
        "source_documents": 14,
        "source_pages": 35,
        "observed_pages": 20,
        "schemes": 15,
        "reader_checks": 30,
        "same_period_relations": 6,
        "other_period_relations": 16,
        "disclosure_limits_preserved": review["disclosure_limitations"],
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
    save(BASE / "candidate_checkpoint.json", result, immutable=True)
    print(result["status"], len(manifest), "frozen files; runtime and active inputs unchanged")


if __name__ == "__main__":
    main()
