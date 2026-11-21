"""Index dated and amendment mentions across all received documents for scoped issuers."""

import datetime as dt
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


base = Path("artifacts/checks/distribution_residuals_2026-11-22")
protocol = read("configs/protocols/distribution_residuals.json")
field_protocol = read("configs/protocols/residual_field_review.json")
require(
    sha(base / "related_source_text.json.gz") == field_protocol["related_source_text_sha256"],
    "Related source text changed",
)
sources = load_gzip(base / "source_text.json.gz")["documents"]
related = load_gzip(base / "related_source_text.json.gz")["documents"]
require(not set(sources).intersection(related), "Text inventories overlap")
sources = sources | related
require(len(sources) == 1135, "Received document inventory changed")
scope = read(protocol["scope"]["path"])
original = {
    (row["symbol"], row["ex_date"]): row["stored_action"]
    for row in read(protocol["source_inputs"]["structured_actions"]["path"])["records"]
}
by_issuer = defaultdict(list)
event_matches = {}
for row in scope["records"]:
    key = row["symbol"], row["ex_date"]
    by_issuer[key[0]].append((key, original[key]))
    event_matches[key] = {
        "symbol": key[0],
        "ex_date": key[1],
        "record_date": original[key]["record_date"],
        "prior_status": row["status"],
        "documents": [],
        "status": "NAVIGATION_ONLY_NOT_SOURCE_ACCEPTANCE",
    }
pattern = re.compile(r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?")
amendment = re.compile(r"更正|修订|补充|取消|撤销|终止|延期")
documents, empty_pages = {}, []
for path, document in sources.items():
    metadata = document["source"]
    require(sha(path) == metadata["sha256"], (path, "Archived PDF changed"))
    readers = {}
    for reader, extraction in document["readers"].items():
        dates, notices = defaultdict(list), []
        for number, page in enumerate(extraction.get("pages", []), 1):
            text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", page))
            if not text:
                empty_pages.append({"source": path, "reader": reader, "page": number})
            for match in pattern.finditer(text):
                try:
                    value = dt.date(*(int(part) for part in match.groups())).isoformat()
                except ValueError:
                    continue
                dates[value].append(
                    {"page": number, "text": text[max(0, match.start() - 55) : match.end() + 90]}
                )
            for match in amendment.finditer(text):
                notices.append(
                    {
                        "page": number,
                        "word": match[0],
                        "text": text[max(0, match.start() - 70) : match.end() + 140],
                    }
                )
        readers[reader] = {"dated_mentions": dict(dates), "amendment_word_mentions": notices}
    documents[path] = {
        "source": metadata,
        "title_has_amendment_word": bool(amendment.search(metadata["title"])),
        "readers": readers,
    }
    for symbol in metadata["scoped_symbols"]:
        for key, action in by_issuer[symbol]:
            hits = {}
            for reader, extracted in readers.items():
                selected = {
                    field: extracted["dated_mentions"].get(action[field], [])
                    for field in ("record_date", "ex_date")
                }
                if any(selected.values()):
                    hits[reader] = selected
            if hits:
                event_matches[key]["documents"].append(
                    {
                        "source": path,
                        "catalog_date": metadata["catalog_date"],
                        "catalogued_after_ex_date": metadata["catalog_date"] > action["ex_date"],
                        "title": metadata["title"],
                        "title_has_amendment_word": documents[path]["title_has_amendment_word"],
                        "readers": hits,
                    }
                )
require(len(event_matches) == 387, "Residual event index incomplete")
full_path = base / "related_event_navigation.json.gz"
require(not full_path.exists(), "Navigation output already exists")
dump_gzip(
    full_path,
    {
        "status": "NAVIGATION_NOT_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "candidate_text_sha256": sha(base / "source_text.json.gz"),
        "related_text_sha256": sha(base / "related_source_text.json.gz"),
        "documents": documents,
        "events": list(event_matches.values()),
        "empty_text_pages": empty_pages,
        "facts_accepted": 0,
        "limitations": "Date occurrence and amendment vocabulary are navigation hints only. A source may concern another period, proposal, price adjustment or legal citation. Preserve issuer, field, role, availability and independent financial checks; a received archive is not a complete historical archive.",
    },
)
summary = {
    "status": "RECEIVED_ARCHIVE_INDEXED_NOT_ACCEPTED",
    "completed_at": now(),
    "checker_sha256": sha(__file__),
    "navigation_sha256": sha(full_path),
    "documents": len(documents),
    "events": len(event_matches),
    "events_with_date_mentions": sum(bool(row["documents"]) for row in event_matches.values()),
    "events_without_date_mentions_by_prior_status": dict(
        Counter(row["prior_status"] for row in event_matches.values() if not row["documents"])
    ),
    "unmatched_event_hints": [
        {
            "symbol": row["symbol"],
            "ex_date": row["ex_date"],
            "documents_with_date_mentions": len(row["documents"]),
        }
        for row in event_matches.values()
        if row["prior_status"] == "UNMATCHED"
    ],
    "empty_text_pages": empty_pages,
    "facts_accepted": 0,
}
save(base / "related_navigation.json", summary, immutable=True)
print(
    "RELATED_EVENT_INDEX_COMPLETE",
    len(documents),
    len(event_matches),
    "empty text pages",
    len(empty_pages),
)
print("NO_DATE_MENTIONS", summary["events_without_date_mentions_by_prior_status"])
