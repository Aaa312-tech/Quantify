"""Check archive navigation coverage and preserve the published numerical baseline."""

import re
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

from quantfolio.storage import load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
BASELINE = "eedb314ab1cd2fc595e0cb3ace1f522289a813e3"
WORDS = [
    "更正",
    "勘误",
    "修订",
    "补充",
    "修正",
    "变更",
    "取消",
    "撤销",
    "终止",
    "中止",
    "延期",
    "延后",
    "延迟",
    "作废",
    "错误",
    "调整",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    require(
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == BASELINE,
        "Unexpected baseline",
    )
    parent = __import__("json").loads(
        subprocess.check_output(["git", "show", f"{BASELINE}:configs/integrity_manifest.json"])
    )
    manifest = read("configs/integrity_manifest.json")
    for path, expected in parent["files"].items():
        require(
            manifest["files"].get(path) == expected and sha(path) == expected,
            f"Frozen file changed: {path}",
        )
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z", "src/quantfolio"]).decode().split("\0")
    )
    runtime = {}
    for path in filter(None, tracked):
        expected = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"])
        require(Path(path).read_bytes() == expected, f"Runtime changed: {path}")
        runtime[path] = sha(path)
    active = {
        "configs/project.toml": "20de8238e81094a608cef08684c59f014bb5ddbe76e55215630efb844fafdb94",
        "data/processed/2026-11-20-recipient-entitlements/bundle.joblib": "c8d4fef3dfacac24c7321edc5a095716d47931096db25f80c61d5e43e0627df2",
        "artifacts/plans/recipient_entitlements_2026-11-23/plan.json": "89fd53a5b5d089d0c1c196b64f8f69d3aefc0958201c618daa4f092a583211b4",
    }
    for path, expected in active.items():
        require(sha(path) == expected, f"Active artifact changed: {path}")
    archive = load_gzip(BASE / "source_text.json.gz")["documents"]
    archive.update(load_gzip(BASE / "related_source_text.json.gz")["documents"])
    index = load_gzip(BASE / "notice_amendment_index.json.gz")
    require(
        set(archive) == set(index["documents"]) and len(archive) == 1135, "Document scope differs"
    )
    inventory_checks, mention_counts, empty = 0, Counter(), []
    for path, source in archive.items():
        indexed = index["documents"][path]
        require(source["source"] == indexed["source"], "Source metadata changed")
        for reader, extraction in source["readers"].items():
            page_counts = {}
            for number, raw in enumerate(extraction["pages"], 1):
                # This whitespace removal counts Chinese keywords only; no numbers are parsed.
                plain = re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw))
                expected = Counter({word: plain.count(word) for word in WORDS if word in plain})
                actual = Counter(
                    h["word"] for h in indexed["readers"][reader]["mentions"] if h["page"] == number
                )
                require(expected == actual, f"Keyword inventory differs: {path}:{reader}:{number}")
                page_counts[number] = actual
                mention_counts[reader] += sum(actual.values())
                inventory_checks += 1
                if not raw.strip():
                    empty.append({"source": path, "reader": reader, "page": number})
            require(
                sum(map(sum, (c.values() for c in page_counts.values())))
                == len(indexed["readers"][reader]["mentions"]),
                "Out-of-range page hit",
            )
    require(inventory_checks == 7560, "Full page-reader inventory incomplete")
    require(
        len(empty) == 2
        and all(Path(e["source"]).stem == "1224067734" and e["page"] == 5 for e in empty),
        "Empty-page inventory differs",
    )
    scoped = read(BASE / "scope.json")["records"]
    require(
        {(r["symbol"], r["ex_date"]) for r in scoped}
        == {(r["symbol"], r["ex_date"]) for r in index["events"]},
        "Residual scope differs",
    )
    require(
        len(index["events"]) == 387
        and sum(r["candidate_field_review"] for r in index["events"]) == 95,
        "Candidate and non-candidate scope differs",
    )
    companion = read(BASE / "companion_review.json")
    require(companion["status"] == "PASS_SCOPED_COMPANION_REVIEW", "Companion check failed")
    for path, expected in companion["dependencies"].items():
        require(sha(path) == expected, "Companion dependency changed")
    require(
        companion["checker_sha256"] == sha(BASE / "verify_companion_relations.py"),
        "Companion executed checker changed",
    )
    title = read(BASE / "title_amendment_review.json")
    require(
        title["documents_read_in_full"] == 10 and title["pages_read_in_full"] == 36,
        "Title full-reading scope differs",
    )
    writer = read(BASE / "notice_index_write_closure.json")
    for path, expected in writer["outputs"].items():
        require(sha(path) == expected, "Index writer output changed")
    require(
        writer["adapter_sha256"] == sha(BASE / "write_notice_index.py"), "Writer adapter changed"
    )
    checks = (BASE / "notice_checks.log").read_text(encoding="utf-8-sig")
    require("772 passed, 1 skipped" in checks, "Full local checks incomplete")
    guard = read(BASE / "model_guard/verification.json")
    require(guard["status"] == "PASS" and guard["models_reloaded"] == 48, "Reference guard missing")
    result = {
        "status": "PASS_SOURCE_CHECKPOINT_NOT_FULL_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "baseline_commit": BASELINE,
        "preserved_frozen_files": len(parent["files"]),
        "unchanged_runtime": runtime,
        "unchanged_active_artifacts": active,
        "archive_documents": len(archive),
        "independent_page_reader_keyword_inventories": inventory_checks,
        "keyword_mentions": dict(mention_counts),
        "empty_pages": empty,
        "residual_events": 387,
        "proposed_events": 95,
        "other_residuals": 292,
        "companion_review_sha256": sha(BASE / "companion_review.json"),
        "title_review_sha256": sha(BASE / "title_amendment_review.json"),
        "index_sha256": sha(BASE / "notice_amendment_index.json.gz"),
        "local_tests": {"passed": 772, "skipped": 1, "log_sha256": sha(BASE / "notice_checks.log")},
        "prior_reference_guard_reused": {
            "models": 48,
            "new_reload_or_fit": False,
            "sha256": sha(BASE / "model_guard/verification.json"),
        },
        "facts_accepted": 0,
        "source_phase_complete": False,
        "whole_goal_complete": False,
        "remaining": "Review archive body relationships and all residual dispositions before "
        "source acceptance. Numerical integration, full remediation and forward goals remain.",
    }
    save(BASE / "notice_checkpoint.json", result, immutable=True)
    new_files = [p for p in BASE.iterdir() if p.is_file() and p.as_posix() not in manifest["files"]]
    for path in new_files:
        manifest["files"][path.as_posix()] = sha(path)
    save("configs/integrity_manifest.json", manifest)
    print(
        {
            "status": result["status"],
            "preserved": len(parent["files"]),
            "runtime_files": len(runtime),
            "page_reader_checks": inventory_checks,
            "new_frozen_files": len(new_files),
            "frozen_total": len(manifest["files"]),
        }
    )


if __name__ == "__main__":
    main()
