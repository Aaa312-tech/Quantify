"""Cross-check every registered ETF summary against two independent PDF text readers."""

import bisect
import collections
import datetime as dt
import re
from pathlib import Path

import joblib
import pymupdf
from pypdf import PdfReader

from quantfolio.storage import dump_gzip, now, read, save, sha

ROOT = Path.cwd()
P = read(ROOT / "configs/protocols/etf_sources.json")
OUT = ROOT / P["review_output"]
PROCESSED = ROOT / P["processed_output"]
RAW = [ROOT / "data/raw" / name for name in (
    "etf_disclosures_2026-11-21", "etf_disclosures_followup_2026-11-21", "etf_disclosures_retry_2026-11-21",
)]


def compact(text):
    return "".join(text.split())


def field(pages, start, end):
    normalized = list(map(compact, pages))
    full = "".join(normalized)
    begin = full.find(start)
    if begin < 0:
        return None
    finish = full.find(end, begin + len(start))
    if finish < 0 or finish - begin > 6000:
        return None
    offsets, running = [], 0
    for page in normalized:
        running += len(page)
        offsets.append(running)
    first, last = bisect.bisect_right(offsets, begin) + 1, bisect.bisect_right(offsets, finish - 1) + 1
    return {"value": full[begin + len(start):finish], "pages": list(range(first, last + 1)), "start_marker": start, "end_marker": end}


def date_value(first_page, marker):
    value = compact(first_page)
    match = re.search(re.escape(marker) + r"[:：]?(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?", value)
    if not match:
        return None
    return dt.date(*map(int, match.groups())).isoformat()


def observations(row):
    path = ROOT / row["download"]["path"]
    if sha(path) != row["download"]["sha256"]:
        raise ValueError("Archived PDF changed")
    with pymupdf.open(path) as document:
        first = [page.get_text() for page in document]
    second = [page.extract_text() or "" for page in PdfReader(path).pages]
    identity = "基金代码" + row["symbol"][2:]
    if len(first) != len(second) or not all(identity in compact(pages[0]) for pages in (first, second)):
        raise ValueError("PDF identity or page-count disagreement: " + row["symbol"] + "/" + row["catalogue_date"])
    parsed = []
    for pages in (first, second):
        parsed.append({
            "fund_type": field(pages, "基金类型", "交易币种"),
            "performance_benchmark": field(pages, "业绩比较基准", "风险收益特征"),
            "investment_scope": field(pages, "投资范围", "主要投资策略") or field(pages, "投资范围", "投资策略"),
            "prepared_on": date_value(pages[0], "编制日期"),
            "inception_on": date_value(pages[0], "基金合同生效日"),
        })
    agreed, unresolved = {}, {}
    for key in parsed[0]:
        left, right = parsed[0][key], parsed[1][key]
        if left is not None and left == right:
            agreed[key] = left
        else:
            unresolved[key] = {"pymupdf": left, "pypdf": right}
    result = {
        "symbol": row["symbol"], "title": row["title"], "catalogue_date": row["catalogue_date"],
        "source_url": row["url"], "source_path": row["download"]["path"], "source_sha256": sha(path),
        "page_count": len(first), "identity_verified_by_both_parsers": True,
        "agreed_fields": agreed, "unresolved_extraction": unresolved,
        "status": "FIELDS_AGREE" if not unresolved else "PARTIAL_FIELD_EXTRACTION",
        "historical_publication_time_certified": False, "holdings_sector_weights_certified": False,
    }
    return result, {"source_path": row["download"]["path"], "pymupdf": first, "pypdf": second}


def main():
    if (OUT / "review.json").exists() or PROCESSED.exists():
        raise FileExistsError("Preserve completed reviews")
    baseline = ROOT / P["baseline_bundle"]
    if sha(baseline) != P["baseline_bundle_sha256"]:
        raise ValueError("Source review baseline changed")
    bundle = joblib.load(baseline)
    receipts = []
    collections_in = []
    for folder in RAW:
        batch = read(folder / "collection.json")
        collections_in.append(batch)
        for file in sorted(folder.rglob("*.receipt.json")):
            receipt = read(file)
            if receipt["status"] == "RECEIVED" and sha(ROOT / receipt["path"]) != receipt["sha256"]:
                raise ValueError("Source response bytes changed: " + str(file))
            receipts.append({"receipt": file.relative_to(ROOT).as_posix(), "status": receipt["status"]})
    queries = {}
    documents = {}
    failed = []
    for batch in collections_in:
        for query in batch.get("queries", []):
            key = (query["symbol"], query["year"])
            if query["status"] != "COMPLETE":
                failed.append({"symbol": key[0], "year": key[1], "error": query.get("error")})
                continue
            if key in queries:
                raise ValueError("Duplicate accepted yearly catalogue")
            queries[key] = query
        for doc in batch["documents"]:
            if doc["status"] == "RECEIVED":
                if doc["url"] in documents:
                    raise ValueError("Duplicate accepted PDF")
                documents[doc["url"]] = doc
    expected_queries = {(s, y) for s in P["symbols"] for y in range(2023, 2027)}
    if set(queries) != expected_queries:
        raise ValueError("A registered fund/year catalogue is missing")
    catalogue = []
    for key, query in sorted(queries.items()):
        if query["total"] != len(query["records"]) or len({r["url"] for r in query["records"]}) != query["total"]:
            raise ValueError("Accepted catalogue has missing or duplicate URLs")
        original = []
        for request in query["pages"]:
            payload = read(ROOT / request["path"])
            original.extend(payload["result"] if query["provider"] == "SSE" else payload["announcements"] or [])
        if original != [record["raw"] for record in query["records"]]:
            raise ValueError("Parsed catalogue differs from every archived raw row")
        for row in query["records"]:
            if row["symbol"] != key[0] or not f"{key[1]}-01-01" <= row["catalogue_date"] <= min(f"{key[1]}-12-31", P["period"]["through"]):
                raise ValueError("Accepted catalogue has a different fund or period")
        catalogue.extend(query["records"])
    expected = {row["url"] for row in catalogue if "产品资料概要" in row["title"] and "联接" not in row["title"]}
    if set(documents) != expected:
        raise ValueError("Product-summary PDFs do not cover the complete accepted catalogues")
    rows, texts = [], []
    for index, row in enumerate(sorted(documents.values(), key=lambda r: (r["symbol"], r["catalogue_date"], r["url"])), 1):
        observation, text = observations(row)
        rows.append(observation); texts.append(text)
        if index % 25 == 0:
            print("reviewed", index, "/", len(documents), flush=True)
    summaries = []
    for symbol in P["symbols"]:
        selected = [r for r in rows if r["symbol"] == symbol]
        before = [r for r in selected if r["catalogue_date"] < P["period"]["first_model_session"]]
        variants = {}
        for name in ("fund_type", "performance_benchmark", "inception_on"):
            variants[name] = sorted({str(r["agreed_fields"][name]["value"] if isinstance(r["agreed_fields"][name], dict) else r["agreed_fields"][name]) for r in selected if name in r["agreed_fields"]})
        summaries.append({"symbol": symbol, "current_name": bundle["meta"][symbol]["name"], "legacy_label": bundle["meta"][symbol]["industry"],
                          "documents": len(selected), "earliest_catalogue_date": selected[0]["catalogue_date"], "latest_catalogue_date": selected[-1]["catalogue_date"],
                          "pre_model_documents": len(before), "observed_variants": variants,
                          "unresolved_documents": sum(bool(r["unresolved_extraction"]) for r in selected),
                          "historical_sector_exposure_verified": False})
    amendments = [row for row in catalogue if any(term in row["title"] for term in ("标的指数", "投资范围", "投资策略", "基金合同", "更名", "变更", "修订", "拆分", "折算"))]
    PROCESSED.mkdir()
    dump_gzip(PROCESSED / "catalogue.json.gz", catalogue)
    dump_gzip(PROCESSED / "extracted_pages.json.gz", texts)
    save(PROCESSED / "observations.json", {"protocol_sha256": sha(ROOT / 'configs/protocols/etf_sources.json'), "observations": rows}, immutable=True)
    save(PROCESSED / "funds.json", summaries, immutable=True)
    save(OUT / "amendment_candidates.json", {"records": amendments, "status": "TITLE_SCREEN_ONLY_REQUIRES_DOCUMENT_REVIEW", "no_hit_is_proof_of_absence": False}, immutable=True)
    save(OUT / "review.json", {
        "status": "SOURCE_FIELDS_EXTRACTED_NOT_ACTIVATED", "completed_at": now(), "source_sha256": sha(Path(__file__)),
        "accepted_queries": len(queries), "catalogue_records": len(catalogue), "summary_pdfs": len(rows),
        "pages": sum(r["page_count"] for r in rows), "receipts": len(receipts), "preserved_failed_queries": failed,
        "funds": len(summaries), "funds_with_pre_model_document": sum(x["pre_model_documents"] > 0 for x in summaries),
        "field_coverage": {key: sum(key in r["agreed_fields"] for r in rows) for key in ("fund_type", "performance_benchmark", "investment_scope", "prepared_on", "inception_on")},
        "unresolved_documents": sum(bool(r["unresolved_extraction"]) for r in rows), "amendment_title_hits": len(amendments),
        "collections": [{"path": (f / 'collection.json').relative_to(ROOT).as_posix(), "sha256": sha(f / 'collection.json')} for f in RAW],
        "model_inputs_changed": False, "forward_observations_added": 0,
        "limitations": ["Matching PDF text is field-scoped; ambiguous extractions require explicit follow-up.", "Catalogue dates, present file bytes and prepared dates do not establish an immutable historical publication timestamp.", "An ETF benchmark is not a look-through CAPCO sector weight, and current identity does not remove historical universe selection bias.", "Amendment titles are a review queue, not an assertion that amendments changed the target index or that no other changes exist."],
    }, immutable=True)
    print("FINISHED", len(rows), "PDFs", len(summaries), "funds", flush=True)


if __name__ == "__main__":
    main()
