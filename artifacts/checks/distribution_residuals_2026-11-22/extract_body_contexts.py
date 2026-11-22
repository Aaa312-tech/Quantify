"""Extract lossless sentence contexts for the registered non-adjustment word queue."""

import re
from collections import Counter
from importlib.metadata import version
from pathlib import Path

import pymupdf
from pypdf import PdfReader
from review_candidate_amendments import prose, require
from review_remaining_amendments import inventory

from quantfolio.storage import digest, dump_gzip, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/notice_body_contexts.json")


def contexts(identifier, reader, pages, words):
    normalized = [prose(page) for page in pages]
    text = "\n\f\n".join(normalized)
    offsets, cursor = [], 0
    for page in normalized:
        offsets.append((cursor, cursor + len(page)))
        cursor += len(page) + 3
    boundaries = [-1] + [m.start() for m in re.finditer(r"[。！？]", text)] + [len(text)]
    results = []
    pattern = "|".join(map(re.escape, words))
    for page_number, page in enumerate(normalized, 1):
        start = offsets[page_number - 1][0]
        for match in re.finditer(pattern, page):
            position = start + match.start()
            left = max(b for b in boundaries if b < position) + 1
            right = min(b for b in boundaries if b >= position)
            if right < len(text):
                right += 1
            sentence = text[left:right]
            sentence_pages = [i for i, (a, b) in enumerate(offsets, 1) if a < right and b > left]
            results.append(
                {
                    "id": f"{identifier}:{reader}:{page_number}:{match.start()}",
                    "document_id": identifier,
                    "reader": reader,
                    "page": page_number,
                    "word": match.group(0),
                    "page_offset": match.start(),
                    "sentence_pages": sentence_pages,
                    "sentence": sentence,
                    "sentence_sha256": digest(sentence),
                    "preceding_context": text[max(0, left - 160) : left],
                    "following_context": text[right : right + 160],
                    "status": "SEMANTIC_REVIEW_PENDING",
                }
            )
    return results


def main():
    protocol = read(PROTOCOL)
    dependencies = {r["path"]: r["sha256"] for r in protocol["inputs"]}
    require(all(sha(p) == h for p, h in dependencies.items()), "Registered inputs changed")
    index = load_gzip(BASE / "notice_amendment_index.json.gz")
    archive = load_gzip(BASE / "source_text.json.gz")["documents"]
    archive.update(load_gzip(BASE / "related_source_text.json.gz")["documents"])
    documents, extracted, failures = {}, [], []
    page_count = tokens = 0
    for item in protocol["documents"]:
        source = index["documents"][item["path"]]["source"]
        identifier = item["id"]
        require(source == archive[item["path"]]["source"], "Source metadata binding")
        require(digest(source) == item["source_metadata_sha256"], "Source metadata changed")
        require(
            sha(item["path"]) == item["source_sha256"] == source["sha256"], "Source PDF changed"
        )
        require(sha(source["receipt"]) == source["receipt_sha256"], "Source receipt changed")
        pypdf_pages = [p.extract_text() for p in PdfReader(item["path"]).pages]
        with pymupdf.open(item["path"]) as pdf:
            mupdf_pages = [p.get_text(sort=False) for p in pdf]
        require(
            pypdf_pages == archive[item["path"]]["readers"]["pypdf"]["pages"],
            "Original extraction changed",
        )
        require(len(pypdf_pages) == len(mupdf_pages) == item["page_count"], "Physical pages")
        for page, (first, second) in enumerate(zip(pypdf_pages, mupdf_pages), 1):
            left, right = inventory(first), inventory(second)
            if left != right:
                failures.append(
                    {
                        "id": identifier,
                        "page": page,
                        "kind": "NUMERIC_INVENTORY",
                        "pypdf_only": dict(left - right),
                        "mupdf_only": dict(right - left),
                    }
                )
            page_count += 1
            tokens += sum(left.values())
        readers = {"pypdf": pypdf_pages, "mupdf_unsorted": mupdf_pages}
        documents[identifier] = {"source": source, "readers": readers}
        for reader, pages in readers.items():
            rows = contexts(identifier, reader, pages, protocol["selection"]["words"])
            actual = Counter(r["word"] for r in rows)
            if actual != Counter(item["nonadjust_words"]):
                failures.append(
                    {
                        "id": identifier,
                        "reader": reader,
                        "kind": "WORD_INVENTORY",
                        "actual": dict(actual),
                        "expected": item["nonadjust_words"],
                    }
                )
            extracted.extend(rows)
    require(len(documents) == 172 and page_count == 509, "Registered extraction scope")
    outputs = []
    for name, value in [
        ("body_context_text.json.gz", {"documents": documents}),
        ("body_contexts.json.gz", {"contexts": extracted}),
    ]:
        target = BASE / name
        require(not target.exists(), "Extraction output must be new")
        dump_gzip(target, value)
        outputs.append({"path": target.as_posix(), "sha256": sha(target)})
    result = {
        "status": "EXTRACTED_WITH_DISCREPANCIES"
        if failures
        else "EXTRACTED_PENDING_SEMANTIC_REVIEW",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "protocol_sha256": sha(PROTOCOL),
        "dependencies": dependencies,
        "helper_sha256": {
            name: sha(BASE / name)
            for name in ["review_candidate_amendments.py", "review_remaining_amendments.py"]
        },
        "reader_versions": {"pypdf": version("pypdf"), "pymupdf": version("pymupdf")},
        "documents": len(documents),
        "numeric_page_pairs": page_count,
        "numeric_tokens_per_reader": tokens,
        "occurrences_by_reader": dict(Counter(r["reader"] for r in extracted)),
        "outputs": outputs,
        "failures": failures,
        "semantic_review_complete": False,
        "new_financial_facts_accepted": 0,
        "active_inputs_changed": False,
        "whole_goal_complete": False,
    }
    save(BASE / "body_context_extraction.json", result, immutable=True)
    print(
        result["status"],
        len(extracted),
        "reader occurrences;",
        len(failures),
        "discrepancies",
        flush=True,
    )


if __name__ == "__main__":
    main()
