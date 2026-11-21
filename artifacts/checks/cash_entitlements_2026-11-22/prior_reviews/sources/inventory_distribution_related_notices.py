"""Inventory all archived notices for the three diagnostic issuers."""

from pathlib import Path
import re

from pypdf import PdfReader

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
collection_file = root / "data/raw/disclosures_retry_2026-11-21/collection.json"
collection = read(collection_file)
symbols = {"sz300750", "sh688025", "sh688627"}
records = []
for entry in collection["documents"]:
    receipt_file = root / entry["receipt"]
    receipt = read(receipt_file)
    matching = sorted(symbols.intersection(receipt["requested_symbols"]))
    if not matching:
        continue
    pdf = root / receipt["path"]
    assert receipt["status"] == "RECEIVED" and sha(pdf) == receipt["sha256"]
    pages = [page.extract_text() or "" for page in PdfReader(pdf).pages]
    hits = []
    for number, page in enumerate(pages, 1):
        text = re.sub(r"\s+", "", page)
        for hit in re.finditer(r"更正|修订|补充|调整.{0,25}(?:分红|利润分配)|(?:分红|利润分配).{0,25}调整", text):
            hits.append({"page": number, "text": text[max(0, hit.start()-55):min(len(text), hit.end()+100)]})
    records.append({
        "id": receipt["id"], "symbols": matching, "title": receipt["title"],
        "catalog_date": receipt["catalog_date"], "kind": receipt["kind"],
        "receipt": {"path": entry["receipt"], "sha256": sha(receipt_file)},
        "source": {"path": receipt["path"], "sha256": sha(pdf), "url": receipt["url"]},
        "pages": pages, "candidate_mentions": hits,
        "title_adjustment_or_revision": bool(re.search(r"更正|修订|补充|调整", receipt["title"])),
    })
queries = []
for query in collection["queries"]:
    if query["symbol"] in symbols:
        queries.append({**query, "sha256": sha(root / query["path"])})
save(base / "related_notice_inventory.json", {
    "status": "ARCHIVE_INVENTORIED_NOT_CLOSED", "created_at": now(),
    "collection": {"path": collection_file.relative_to(root).as_posix(), "sha256": sha(collection_file)},
    "script_sha256": sha(Path(__file__)), "symbols": sorted(symbols),
    "queries": queries, "records": records, "facts_accepted": 0,
    "scope": "Every received document for these requested symbols in the bound archive was inventoried, including full extracted text and broad revision/adjustment navigation hits. This does not prove the archive complete or certify any revision relation; related candidates require semantic and visual checks.",
}, immutable=True)
print("ARCHIVED DOCUMENTS", len(records), "QUERIES", len(queries), flush=True)
for row in records:
    print(row["id"], row["symbols"], row["catalog_date"], row["title"], "hits", len(row["candidate_mentions"]), flush=True)
