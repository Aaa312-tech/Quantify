"""Inventory remaining extraction contexts without accepting facts or changing runtime."""

import re
import unicodedata
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from quantfolio.distributions import _current_payout, _current_scheme
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
review_file = root / "artifacts/checks/distribution_wording_2026-11-21/source_review.json"
review = read(review_file)
runtime = root / "src/quantfolio/distributions.py"
runtime_sha = sha(runtime)
cache, records, counts = {}, [], Counter()
for row in review["records"]:
    if row["status"] != "PRIMARY_GROSS_AMOUNT_UNSUPPORTED_OR_CONFLICTING":
        continue
    source = row["source"]
    file = root / source["path"]
    assert sha(file) == source["sha256"]
    if source["path"] not in cache:
        pages = []
        for page in PdfReader(file).pages:
            text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", page.extract_text() or ""))
            pages.append(re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", text))
        cache[source["path"]] = pages
    pages = cache[source["path"]]
    contexts = []
    for item in row.get("evidence", {}).get("unscoped_cash_per_share", []):
        page_index = item["page"] - 1
        page = pages[page_index]
        matches = list(re.finditer(re.escape(item["text"]), page))
        for match in matches:
            prefix = " ".join(pages[:page_index]) + " " + page[:match.start()]
            headings = list(re.finditer(r"[一二三四五六七八九十]+、", prefix))
            heading = prefix[headings[-1].end():].strip()[:160] if headings else None
            recognized = _current_scheme(prefix)
            supported = _current_payout(prefix, item["text"].startswith("向全体股东"))
            category = "section_unrecognized" if not recognized else "clause_excluded" if not supported else "ambiguous_excerpt_location"
            counts[category] += 1
            contexts.append({"page": item["page"], "excerpt": item["text"],
                             "page_match_count": len(matches), "heading_prefix": heading,
                             "clause_prefix": re.split(r"[。；;]", prefix)[-1][-1000:],
                             "section_recognized": recognized, "payout_recognized": supported,
                             "classification": category})
        if not matches:
            counts["excerpt_not_relocated"] += 1
            contexts.append({"page": item["page"], "excerpt": item["text"],
                             "classification": "excerpt_not_relocated"})
    cash_contexts = []
    if not contexts:
        counts["records_without_unscoped_excerpt"] += 1
        for number, page in enumerate(pages, 1):
            for match in re.finditer(r"每\s*(?:10|十)?\s*股", page):
                cash_contexts.append({"page": number, "text": page[max(0, match.start()-90):match.end()+180]})
    records.append({"symbol": row["symbol"], "ex_date": row["ex_date"],
                    "source": source, "existing_evidence": row.get("evidence", {}),
                    "unscoped_contexts": contexts, "other_per_share_contexts": cash_contexts})
assert len(records) == 182
assert runtime_sha == sha(runtime)
save(root / ".git/payout_scope_inventory.json", {
    "status": "READ_ONLY_DIAGNOSTIC_NOT_ACCEPTANCE", "completed_at": now(),
    "reader_sha256": sha(Path(__file__)), "source_review_sha256": sha(review_file),
    "runtime_sha256": runtime_sha, "events": len(records), "documents": len(cache),
    "context_counts": dict(counts), "records": records, "facts_added": 0,
    "scope": "Contexts relocated in complete frozen PDFs using the existing normalizer and scope functions. Counts describe text occurrences, not newly verified financial events. The frozen review omits character offsets, so identical excerpts retain every location. Source facts, active inputs, runtime, research candidates and returns remain unchanged; any parser change requires separate registration and independent financial/visual acceptance.",
}, immutable=True)
print("PAYOUT CONTEXT INVENTORY", len(records), len(cache), dict(counts), flush=True)
