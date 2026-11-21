"""Preserve full archive coverage while indexing amendment and status passages."""

import re
from collections import Counter
from pathlib import Path

from read_primary_financials import normalize

from quantfolio.storage import digest, dump_gzip, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/residual_notice_relations.json")
WORDS = re.compile(
    "更正|勘误|修订|补充|修正|变更|取消|撤销|终止|中止|延期|延后|延迟|作废|错误|调整"
)
OBSERVED = {
    "1220465181": {1: "Printed aggregate correction 19,674,000 to 18,674,000; ratios unchanged."},
    "1225574968": {
        1: "Original notice explicitly prints 0.08008 and the two-decimal 0.08 parenthesis.",
        2: "Original calculation and conclusion repeat the rounding parenthesis and actual total.",
    },
    "1225579045": {
        1: "Revised notice retains 0.08008 and removes the two-decimal parenthesis.",
        2: "Revised calculation and actual total remain; record date is left to implementation.",
    },
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def contexts(raw, page):
    text = normalize(raw)
    return [
        {
            "page": page,
            "word": match[0],
            "start": match.start(),
            "end": match.end(),
            "context": text[max(0, match.start() - 100) : match.end() + 180],
        }
        for match in WORDS.finditer(text)
    ]


def main():
    for item in read(PROTOCOL)["inputs"]:
        require(sha(item["path"]) == item["sha256"], "Registered source changed")
    first = load_gzip(BASE / "source_text.json.gz")["documents"]
    second = load_gzip(BASE / "related_source_text.json.gz")["documents"]
    require(not set(first).intersection(second), "Duplicate source collections")
    archive = first | second
    require(len(archive) == 1135, "Received archive incomplete")
    notes = read(BASE / "title_amendment_notes.json")["records"]
    manual = {note["id"]: note for note in notes}
    require(len(manual) == len(notes) == 10, "Title review count differs")
    companions = {r["companion"]["id"] for r in read(BASE / "companion_review.json")["records"]}
    old_navigation = load_gzip(BASE / "dated_event_navigation.json.gz")["documents"]
    old_flagged = {
        Path(path).stem for path, d in old_navigation.items() if d["title_has_amendment_word"]
    }
    require(len(old_flagged) == 9 and old_flagged < set(manual), "Title scope changed")
    documents, title_records, observations = {}, [], []
    for path, doc in archive.items():
        source = doc["source"]
        require(sha(path) == source["sha256"], f"Changed PDF: {path}")
        require(sha(source["receipt"]) == source["receipt_sha256"], f"Changed receipt: {path}")
        readers = {}
        for reader, result in doc["readers"].items():
            pages = result["pages"]
            require(len(pages) == result["page_count"], "Reader page count differs")
            readers[reader] = {
                "page_count": len(pages),
                "raw_page_text_sha256": [digest(p) for p in pages],
                "empty_pages": [n for n, p in enumerate(pages, 1) if not p.strip()],
                "mentions": [hit for n, p in enumerate(pages, 1) for hit in contexts(p, n)],
            }
        require(set(readers) == {"pypdf", "mupdf"}, "Independent archive reader missing")
        require(
            readers["pypdf"]["page_count"] == readers["mupdf"]["page_count"],
            "Cross-reader page inventory differs",
        )
        ident = source["id"]
        status = "BODY_RELATION_REVIEW_PENDING"
        if ident in companions:
            status = "COMPANION_BODY_REVIEWED_ARCHIVE_RELATION_PENDING"
        if ident in manual:
            status = "TITLE_BODY_REVIEWED_ARCHIVE_RELATION_PENDING"
            note = manual[ident]
            require(
                note["pages_read"] == list(range(1, readers["pypdf"]["page_count"] + 1)),
                "Manual full-text reading inventory differs",
            )
            title_records.append(
                {"source": source, "manual_review": note, "reader_inventories": readers}
            )
        documents[path] = {
            "source": source,
            "title_words": sorted(set(WORDS.findall(normalize(source["title"])))),
            "readers": readers,
            "status": status,
            "no_hits_is_financial_acceptance": False,
        }
        for page, finding in OBSERVED.get(ident, {}).items():
            image = Path(".git/amendment_page_review") / f"{ident}_page_{page:02d}.png"
            observations.append(
                {
                    "source": path,
                    "source_sha256": source["sha256"],
                    "page": page,
                    "render_sha256": sha(image),
                    "finding": finding,
                }
            )
    require(
        sum(d["readers"]["pypdf"]["page_count"] for d in documents.values()) == 3780,
        "Complete received page inventory differs",
    )
    residuals = read(BASE / "scope.json")["records"]
    require(len(residuals) == 387, "Residual event scope differs")
    proposed = {
        (r["symbol"], r["ex_date"])
        for r in read("configs/protocols/residual_field_review.json")["records"]
    }
    events = []
    for event in residuals:
        key = event["symbol"], event["ex_date"]
        paths = [path for path, d in documents.items() if key[0] in d["source"]["scoped_symbols"]]
        events.append(
            {
                "symbol": key[0],
                "ex_date": key[1],
                "prior_status": event["status"],
                "candidate_field_review": key in proposed,
                "documents": paths,
                "status": "RESIDUAL_DISPOSITION_PENDING",
            }
        )
    result = {
        "status": "NAVIGATION_ONLY_NOT_FINANCIAL_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "protocol_sha256": sha(PROTOCOL),
        "normalizer_sha256": sha(BASE / "read_primary_financials.py"),
        "patterns": WORDS.pattern,
        "documents": documents,
        "events": events,
        "limitations": "Full-text keyword inspection is navigation. Absence of a keyword cannot "
        "certify an unchanged event or complete source coverage.",
    }
    dump_gzip(BASE / "notice_amendment_index.json.gz", result, immutable=True)
    title_result = {
        "status": "FULL_TEXT_REVIEWS_BOUND_RELATIONSHIP_REVIEW_PENDING",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "manual_notes_sha256": sha(BASE / "title_amendment_notes.json"),
        "index_sha256": sha(BASE / "notice_amendment_index.json.gz"),
        "prior_title_flags": sorted(old_flagged),
        "documents_read_in_full": len(title_records),
        "pages_read_in_full": sum(len(r["manual_review"]["pages_read"]) for r in title_records),
        "records": title_records,
        "whole_pages_observed": observations,
        "facts_accepted": 0,
        "source_phase_complete": False,
    }
    save(BASE / "title_amendment_review.json", title_result, immutable=True)
    summary = {
        "status": result["status"],
        "checker_sha256": sha(__file__),
        "index_sha256": sha(BASE / "notice_amendment_index.json.gz"),
        "documents": len(documents),
        "pages": 3780,
        "residual_events": len(events),
        "title_keyword_documents": sum(bool(d["title_words"]) for d in documents.values()),
        "body_keyword_documents": sum(
            any(r["mentions"] for r in d["readers"].values()) for d in documents.values()
        ),
        "document_review_states": dict(Counter(d["status"] for d in documents.values())),
        "mention_counts": {
            reader: sum(len(d["readers"][reader]["mentions"]) for d in documents.values())
            for reader in ("pypdf", "mupdf")
        },
        "title_full_text_documents": len(title_records),
        "title_full_text_pages": 36,
        "facts_accepted": 0,
        "source_phase_complete": False,
    }
    save(BASE / "notice_amendment_index.json", summary, immutable=True)
    print(summary)


if __name__ == "__main__":
    main()
