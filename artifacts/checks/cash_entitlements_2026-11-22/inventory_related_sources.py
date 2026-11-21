"""Inventory received issuer notices without certifying archive completeness."""

import re
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
protocol_path = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_path)
scope_path = root / protocol["scope"]["path"]
assert sha(scope_path) == protocol["scope"]["sha256"]
symbols = {row["symbol"] for row in read(scope_path)["records"]}
collection_source = protocol["source_inputs"]["disclosures"]
collection_path = root / collection_source["path"]
assert sha(collection_path) == collection_source["sha256"]
collection = read(collection_path)
records = []
pattern = re.compile(r"更正|修订|补充|调整.{0,25}(?:分红|利润分配)|(?:分红|利润分配).{0,25}调整")
for entry in collection["documents"]:
    receipt_path = root / entry["receipt"]
    receipt = read(receipt_path)
    matching = sorted(symbols.intersection(receipt["requested_symbols"]))
    if not matching:
        continue
    pdf = root / receipt["path"]
    assert receipt["status"] == "RECEIVED" and sha(pdf) == receipt["sha256"]
    pages = [page.extract_text() or "" for page in PdfReader(pdf).pages]
    with fitz.open(pdf) as document:
        independent_pages = [page.get_text(sort=True) for page in document]
    assert len(pages) == len(independent_pages)
    hits = []
    for page_number, page in enumerate(pages, 1):
        compact = re.sub(r"\s+", "", page)
        for match in pattern.finditer(compact):
            hits.append({
                "page": page_number,
                "context": compact[max(0, match.start() - 65):match.end() + 125],
            })
    records.append({
        "id": receipt["id"], "symbols": matching, "title": receipt["title"],
        "catalog_date": receipt["catalog_date"], "kind": receipt["kind"],
        "receipt": {"path": entry["receipt"], "sha256": sha(receipt_path)},
        "source": {"path": receipt["path"], "sha256": sha(pdf), "url": receipt["url"]},
        "pages": pages, "independent_pages": independent_pages,
        "candidate_mentions": hits,
        "title_adjustment_or_revision": bool(re.search(r"更正|修订|补充|调整", receipt["title"])),
    })
queries = [
    {**query, "sha256": sha(root / query["path"])}
    for query in collection["queries"] if query["symbol"] in symbols
]
save(root / protocol["checks_output"] / "related_source_inventory.json", {
    "status": "ARCHIVE_INVENTORIED_NOT_CLOSED", "created_at": now(),
    "protocol_sha256": sha(protocol_path), "scope_sha256": sha(scope_path),
    "collection": collection_source, "script_sha256": sha(Path(__file__)),
    "symbols": sorted(symbols), "queries": queries, "records": records,
    "reader_version": fitz.VersionBind, "facts_accepted": 0,
    "scope": "All received documents requested for scoped issuers in the bound collection, with both readers and broad navigation hits. This does not certify historical publication time, archive completeness or the absence of an amendment. Related candidates still require contextual decisions and relevant page inspection.",
}, immutable=True)
print("INVENTORIED", len(records), "documents", len(queries), "queries", len(symbols), "issuers")
for row in records:
    print(row["id"], "/".join(row["symbols"]), row["catalog_date"], row["title"],
          "pages", len(row["pages"]), "hits", len(row["candidate_mentions"]))
