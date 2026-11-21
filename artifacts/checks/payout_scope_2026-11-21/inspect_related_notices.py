"""Inventory complete archived notices and revision language for the added issuers."""

import re
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = Path(__file__).parent
fields = read(base / "independent_closure.json")
assert fields["status"] == "PASS"
symbols = {row["symbol"] for row in fields["records"]}
source_review = read(base / "source_review.json")
collection_file = root / "data/raw/disclosures_retry_2026-11-21/collection.json"
collection = read(collection_file)
manifest = read(root / "configs/integrity_manifest.json")["files"]
assert sha(collection_file) == manifest[collection_file.relative_to(root).as_posix()]
pattern = re.compile("更正|修订|补充|取消|终止|撤销|延期|差错|变更")
inventory = []
for item in collection["documents"]:
    receipt_file = root / item["receipt"]
    receipt = read(receipt_file)
    if not symbols.intersection(receipt["requested_symbols"]):
        continue
    assert sha(receipt_file) == manifest[item["receipt"]]
    pdf = root / receipt["path"]
    assert receipt["status"] == "RECEIVED" and sha(pdf) == receipt["sha256"]
    with fitz.open(pdf) as document:
        pages = [page.get_text(sort=True) for page in document]
    hits = []
    for number, page in enumerate(pages, 1):
        text = re.sub(r"\s+", "", page)
        for match in pattern.finditer(text):
            hits.append(
                {
                    "page": number,
                    "word": match[0],
                    "context": text[max(0, match.start() - 100) : match.end() + 140],
                }
            )
    inventory.append(
        {
            "receipt": item["receipt"],
            "receipt_sha256": sha(receipt_file),
            "id": receipt["id"],
            "title": receipt["title"],
            "kind": receipt["kind"],
            "symbols": receipt["requested_symbols"],
            "source": receipt["path"],
            "source_sha256": receipt["sha256"],
            "catalog_date": receipt["catalog_date"],
            "pages": len(pages),
            "title_revision_candidate": receipt["kind"] == "amendment"
            or pattern.search(receipt["title"]) is not None,
            "text_hits": hits,
            "original_events": [
                {"symbol": row["symbol"], "ex_date": row["ex_date"], "status": row["status"]}
                for row in source_review["records"]
                if row.get("source", {}).get("path") == receipt["path"]
            ],
            "new_events": [
                {"symbol": row["symbol"], "ex_date": row["ex_date"]}
                for row in fields["records"]
                if row["source"]["path"] == receipt["path"]
            ],
        }
    )
save(
    base / "related_notices.json",
    {
        "status": "INVENTORIED_PENDING_REVIEW",
        "checked_at": now(),
        "reader_sha256": sha(Path(__file__)),
        "collection_sha256": sha(collection_file),
        "independent_closure_sha256": sha(base / "independent_closure.json"),
        "issuers": sorted(symbols),
        "documents": len(inventory),
        "title_revision_candidates": sum(row["title_revision_candidate"] for row in inventory),
        "text_hits": sum(len(row["text_hits"]) for row in inventory),
        "records": inventory,
        "scope": "Every received document for the added issuers in the frozen collection was read. This is not certification of a complete historical disclosure archive.",
    },
    immutable=True,
)
print(
    "ISSUERS",
    len(symbols),
    "DOCUMENTS",
    len(inventory),
    "TITLE CANDIDATES",
    sum(row["title_revision_candidate"] for row in inventory),
    "TEXT HITS",
    sum(len(row["text_hits"]) for row in inventory),
    flush=True,
)
for row in inventory:
    if row["title_revision_candidate"] or row["text_hits"]:
        print(row["id"], row["title"], row["text_hits"], flush=True)
