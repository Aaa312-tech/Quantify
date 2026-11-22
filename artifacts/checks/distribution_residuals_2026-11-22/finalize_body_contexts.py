"""Bind the scoped manual context review and preserve the active research baseline."""

import json
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from review_candidate_amendments import prose
from review_remaining_amendments import inventory
from verify_amendment_checkpoint import check_file, require

from quantfolio.storage import digest, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
BASELINE = "019b859dd126310b5b4033e27e72b2e62ed55ade"
PROTOCOL = Path("configs/protocols/notice_body_contexts.json")


def presentation_characters(text):
    """Compare reader character order only; never parse numbers from this representation."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def content_page_segments(context, document):
    pages = [prose(p) for p in document["readers"][context["reader"]]]
    text = "\n\f\n".join(pages)
    start = sum(len(p) + 3 for p in pages[: context["page"] - 1]) + context["page_offset"]
    left = max(text.rfind(mark, 0, start) for mark in "。！？") + 1
    endings = [position for mark in "。！？" if (position := text.find(mark, start)) >= 0]
    right = min(endings) + 1 if endings else len(text)
    require(text[left:right] == context["sentence"], "Independent complete sentence boundary")
    raw_spans, segments, cursor = [], {}, 0
    for number, page in enumerate(pages, 1):
        end = cursor + len(page)
        if cursor < right and end > left:
            raw_spans.append(number)
            segment = text[max(cursor, left) : min(end, right)]
            segments[str(number)] = segment
        cursor = end + 3
    require(raw_spans == context["sentence_pages"], "Raw page intersections changed")
    return segments


def main():
    require(
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == BASELINE,
        "Published baseline changed",
    )
    protocol = read(PROTOCOL)
    extraction = read(BASE / "body_context_extraction.json")
    notes = read(BASE / "body_context_notes.json")
    errata = read(BASE / "body_context_note_errata.json")
    require(extraction["checker_sha256"] == sha(BASE / "extract_body_contexts.py"), "Extractor")
    require(extraction["protocol_sha256"] == notes["protocol_sha256"] == sha(PROTOCOL), "Protocol")
    require(errata["notes_sha256"] == sha(BASE / "body_context_notes.json"), "Original notes")
    dependencies = {r["path"]: r["sha256"] for r in protocol["inputs"]}
    require(dependencies == extraction["dependencies"], "Input scope")
    require(all(sha(p) == h for p, h in dependencies.items()), "Registered inputs changed")
    require(
        all(sha(BASE / p) == h for p, h in extraction["helper_sha256"].items()), "Reader helpers"
    )
    require(all(sha(r["path"]) == r["sha256"] for r in extraction["outputs"]), "Reader archive")
    require(not extraction["failures"], "Extraction discrepancies remain")
    require(notes["context_archive_sha256"] == sha(BASE / "body_contexts.json.gz"), "Contexts")

    documents = load_gzip(BASE / "body_context_text.json.gz")["documents"]
    contexts = load_gzip(BASE / "body_contexts.json.gz")["contexts"]
    by_id = {r["id"]: r for r in contexts}
    require(len(by_id) == len(contexts) == 666, "Unique reader occurrences")
    require(len(documents) == len(protocol["documents"]) == 172, "Document scope")
    page_count = numeric_tokens = 0
    for item in protocol["documents"]:
        document = documents[item["id"]]
        source = document["source"]
        require(digest(source) == item["source_metadata_sha256"], "Source metadata")
        require(source["path"] == item["path"], "Source path")
        require(sha(source["path"]) == source["sha256"] == item["source_sha256"], "Source PDF")
        require(sha(source["receipt"]) == source["receipt_sha256"], "Source receipt")
        left, right = document["readers"].values()
        require(len(left) == len(right) == item["page_count"], "Page scope")
        for first, second in zip(left, right):
            # Numeric boundaries stay intact in this independent token comparison.
            require(inventory(first) == inventory(second), "Numeric page inventory")
            numeric_tokens += sum(inventory(first).values())
            page_count += 1
    require(page_count == 509 and numeric_tokens == 12921, "Extraction totals")

    by_slot = defaultdict(lambda: defaultdict(list))
    for context in contexts:
        document = documents[context["document_id"]]
        page = prose(document["readers"][context["reader"]][context["page"] - 1])
        start = context["page_offset"]
        require(page[start : start + len(context["word"])] == context["word"], "Word offset")
        require(digest(context["sentence"]) == context["sentence_sha256"], "Full sentence hash")
        require(context["page"] in context["sentence_pages"], "Sentence page span")
        by_slot[(context["document_id"], context["page"], context["word"])][
            context["reader"]
        ].append(context)
    span_protocol_path = Path("configs/protocols/body_context_spans.json")
    span_protocol = read(span_protocol_path)
    span_failure = read(BASE / "body_context_span_failure.json")
    require(span_protocol["parent_sha256"] == sha(PROTOCOL), "Boundary parent protocol")
    require(
        span_protocol["failed_checker_sha256"] == sha(BASE / "review_body_contexts.py"),
        "Failed checker preserved",
    )
    require(
        span_protocol["failure_receipt_sha256"] == sha(BASE / "body_context_span_failure.json"),
        "Failed span receipt preserved",
    )
    require(
        span_failure["log_sha256"] == sha(BASE / "body_context_review.log"), "Original failure log"
    )
    expected_spans = {(r["pypdf"], r["mupdf_unsorted"]) for r in span_protocol["allowed_pairs"]}
    span_checks = []
    pairs = {}
    for readers in by_slot.values():
        left = sorted(readers["pypdf"], key=lambda r: r["page_offset"])
        right = sorted(readers["mupdf_unsorted"], key=lambda r: r["page_offset"])
        require(len(left) == len(right), "Per-page word counts differ")
        for first, second in zip(left, right):
            require(
                presentation_characters(first["sentence"])
                == presentation_characters(second["sentence"]),
                "Reader sentence character order differs",
            )
            if first["sentence_pages"] != second["sentence_pages"]:
                require(
                    (first["id"], second["id"]) in expected_spans,
                    "Unregistered page-span difference",
                )
                segments = {
                    row["reader"]: content_page_segments(row, documents[row["document_id"]])
                    for row in (first, second)
                }
                meaningful = {
                    reader: {
                        page: presentation_characters(text)
                        for page, text in pages.items()
                        if text.strip()
                    }
                    for reader, pages in segments.items()
                }
                require(
                    meaningful["pypdf"] == meaningful["mupdf_unsorted"],
                    "Physical-page characters differ",
                )
                span_checks.append(
                    {
                        "pypdf": first["id"],
                        "mupdf_unsorted": second["id"],
                        "raw_page_segments": segments,
                        "meaningful_pages": list(meaningful["pypdf"]),
                        "difference_is_whitespace_only": True,
                    }
                )
            pairs[first["id"]] = second["id"]
    require(len(pairs) == 333, "Paired occurrence scope")
    require(
        {(r["pypdf"], r["mupdf_unsorted"]) for r in span_checks} == expected_spans
        and len(span_checks) == 6,
        "Exact page-span closure scope",
    )

    corrections = {r["context_id"]: r for r in errata["corrections"]}
    require(len(corrections) == 6, "Note correction scope")
    roles = dict(notes["roles"])
    roles["future_stop_end_date_reference"] = {
        "meaning": "Narrative reference to a conversion stop-end/resumption date to be published later.",
        "financial_context": False,
    }
    decisions = []
    seen = set()
    for group in notes["groups"]:
        require(group["source"] == documents[group["document_id"]]["source"], "Manual source")
        for decision in group["decisions"]:
            identifier = decision["context_id"]
            require(identifier not in seen and identifier in pairs, "Duplicate/unscoped decision")
            seen.add(identifier)
            context = by_id[identifier]
            require(group["sentence_sha256"] == context["sentence_sha256"], "Manual sentence")
            require(group["pages"] == context["sentence_pages"], "Manual physical pages")
            require(
                all(decision[k] == context[k] for k in ("word", "page", "page_offset")),
                "Manual word position",
            )
            role = decision["role"]
            if identifier in corrections:
                correction = corrections[identifier]
                require(correction["prior_role"] == role, "Prior manual role")
                require(correction["sentence_sha256"] == context["sentence_sha256"], "Errata text")
                role = correction["role"]
            require(role in roles, "Unknown semantic role")
            for reader_id in (identifier, pairs[identifier]):
                row = by_id[reader_id]
                text = row["sentence"]
                page = prose(
                    documents[row["document_id"]]["readers"][row["reader"]][row["page"] - 1]
                )
                offset = row["page_offset"]
                if role == "stop_end_column_label":
                    require(
                        page[:offset].endswith("停牌期间停牌")
                        and page[offset:].startswith("终止日复牌日"),
                        "Column label versus narrative reference",
                    )
                elif role == "future_stop_end_date_reference":
                    require("后续" in text and "转股" in text, "Future conversion date context")
                elif role == "negated_correction_statement":
                    require(re.search(r"不存在需要[^。]{0,20}(?:更正|补充)", text), "Negation lost")
                elif role == "investment_project_delay":
                    require("募集资金投资项目" in text and "延期" in text, "Project delay scope")
                elif role == "no_control_change_statement":
                    require("不会导致公司实际控制人发生变更" in text, "Negative control statement")
                elif role == "no_delisting_intent":
                    require("不以终止" in text and "上市地位为目的" in text, "Delisting intent")
                elif role == "distribution_revision_reference":
                    require("修订版" in text and "0.08008" in text, "Distribution revision")
            decisions.append(
                {
                    "group": group["key"],
                    "document_id": context["document_id"],
                    "word": context["word"],
                    "role": role,
                    "financial_context": roles[role]["financial_context"],
                    "pypdf_context_id": identifier,
                    "mupdf_context_id": pairs[identifier],
                    "pypdf_sentence_sha256": context["sentence_sha256"],
                    "mupdf_sentence_sha256": by_id[pairs[identifier]]["sentence_sha256"],
                    "financial_field_accepted": False,
                }
            )
    require(seen == set(pairs) and len(notes["groups"]) == 274, "Complete scoped review")
    counts = Counter(row["role"] for row in decisions)
    require(counts["stop_end_column_label"] == 106, "Exact table headers")
    require(counts["future_stop_end_date_reference"] == 6, "Future date references")
    observations = notes["whole_page_observations"]
    require(len({(r["id"], r["page"]) for r in observations}) == len(observations) == 14, "Views")
    require(all(sha(r["source"]) == r["source_sha256"] for r in observations), "Observed sources")
    prior_revision = notes["prior_distribution_revision"]
    require(sha(prior_revision["review_path"]) == prior_revision["review_sha256"], "Prior revision")
    prior_rows = {r["source"]["id"]: r for r in read(prior_revision["review_path"])["records"]}
    for item in prior_revision["records"]:
        prior = prior_rows[item["source"]["id"]]
        require(item["source"] == prior["source"], "Original/revised source relation")
        require(digest(prior["manual_review"]) == item["manual_review_sha256"], "Prior scope")
        require(sha(item["source"]["path"]) == item["source"]["sha256"], "Prior revision PDF")

    previous = read(BASE / "remaining_checkpoint.json")
    parent = json.loads(
        subprocess.check_output(["git", "show", f"{BASELINE}:configs/integrity_manifest.json"])
    )["files"]
    manifest = read("configs/integrity_manifest.json")["files"]
    require(all(manifest.get(p) == h for p, h in parent.items()), "Frozen identities")
    with ThreadPoolExecutor(max_workers=4) as pool:
        total_bytes = sum(pool.map(check_file, manifest.items()))
    runtime = previous["unchanged_runtime"]
    require(all(sha(p) == h for p, h in runtime.items()), "Runtime bytes")
    paths = subprocess.check_output(["git", "ls-files", "-z", "src/quantfolio"]).decode()
    require(set(filter(None, paths.split("\0"))) == set(runtime), "Runtime inventory")
    active = previous["unchanged_active_artifacts"]
    require(all(sha(p) == h for p, h in active.items()), "Active research artifacts")
    records = read("artifacts/checks/cash_entitlements_2026-11-22/source_review.json")["records"]
    require(digest(records) == previous["prior_records_sha256"], "Prior records")
    require(sum(bool(r.get("verified_fields")) for r in records) == 1452, "Accepted event scope")
    log_path = BASE / "body_context_checks.log"
    log = log_path.read_text(encoding="utf-8-sig")
    require("All checks passed!" in log and "772 passed, 1 skipped" in log, "Local checks")
    guard = previous["prior_reference_guard_reused"]
    require(sha(BASE / "model_guard/verification.json") == guard["sha256"], "Reference guard")
    source_results = []
    for identifier, document in documents.items():
        rows = [r for r in decisions if r["document_id"] == identifier]
        require(bool(rows), "Unreviewed selected document")
        source_results.append(
            {
                "source": document["source"],
                "reviewed_word_occurrences": len(rows),
                "roles": dict(Counter(r["role"] for r in rows)),
                "status": "SCOPED_CONTEXTS_REVIEWED_NOT_FINANCIAL_ACCEPTANCE",
                "complete_document_relationships_accepted": False,
            }
        )
    result = {
        "status": "PASS_SCOPED_BODY_CONTEXT_REVIEW_WITH_LIMITS",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "protocol_sha256": sha(PROTOCOL),
        "dependencies": {
            **dependencies,
            **{
                (BASE / p).as_posix(): sha(BASE / p)
                for p in (
                    "body_context_extraction.json",
                    "extract_body_contexts.py",
                    "body_context_text.json.gz",
                    "body_contexts.json.gz",
                    "body_context_notes.json",
                    "body_context_note_errata.json",
                    "title_amendment_review.json",
                    "review_candidate_amendments.py",
                    "review_remaining_amendments.py",
                    "verify_amendment_checkpoint.py",
                    "remaining_checkpoint.json",
                )
            },
        },
        "documents": source_results,
        "numeric_page_pairs": page_count,
        "numeric_tokens_per_reader": numeric_tokens,
        "paired_word_occurrences": len(decisions),
        "unique_sentence_groups_read": 274,
        "observed_whole_pages": 14,
        "all_509_pages_newly_read_or_visually_observed": False,
        "roles": roles,
        "role_counts": dict(counts),
        "decisions": decisions,
        "manual_note_corrections": errata["corrections"],
        "page_span_closure": {
            "protocol_sha256": sha(span_protocol_path),
            "failure_receipt_sha256": sha(BASE / "body_context_span_failure.json"),
            "failed_checker_sha256": sha(BASE / "review_body_contexts.py"),
            "checks": span_checks,
            "raw_text_and_failure_preserved": True,
        },
        "preserved_source_issues": notes["preserved_issues"],
        "baseline_commit": BASELINE,
        "baseline_frozen_files_preserved": len(parent),
        "current_frozen_files_verified": len(manifest),
        "verified_bytes": total_bytes,
        "unchanged_runtime": runtime,
        "unchanged_active_artifacts": active,
        "prior_records_sha256": digest(records),
        "prior_accepted_events_preserved": 1452,
        "source_unverified_events": 387,
        "proposed_events": 95,
        "proposed_fields": 115,
        "local_tests": {"passed": 772, "skipped": 1, "log_sha256": sha(log_path)},
        "prior_reference_guard_reused": guard,
        "new_fits_or_model_reloads": 0,
        "new_financial_facts_accepted": 0,
        "active_inputs_changed": False,
        "complete_archive_relationships_reviewed": False,
        "whole_goal_complete": False,
        "remaining": previous["remaining"],
    }
    save(BASE / "body_context_review.json", result, immutable=True)
    print(result["status"], len(decisions), "paired occurrences;", len(parent), "old files exact")


if __name__ == "__main__":
    main()
