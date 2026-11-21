"""Recover failed SSE pagination without changing or deduplicating the failed archive."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
original = root / "data/raw/etf_disclosures_2026-11-21"
source = original / "collector_source.py"
spec = importlib.util.spec_from_file_location("etf_collector", source)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
previous = read(original / "collection.json")
out = root / "data/raw/etf_disclosures_followup_2026-11-21"
out.mkdir()
c.OUT = out
(out / "collector_source.py").write_bytes(Path(__file__).read_bytes())
failed = [q for q in previous["queries"] if q["status"] != "COMPLETE"]
save(out / "protocol.json", {
    "registered_at": now(), "parent_collection_sha256": sha(original / "collection.json"),
    "collector_sha256": sha(out / "collector_source.py"), "original_collector_sha256": sha(source),
    "reason": "SSE page-size 25 returned repeated documents across different NUM positions; counts alone did not cover unique URLs. Preserve failures and obtain complete single-page yearly results.",
    "queries": [{"symbol": q["symbol"], "year": q["year"], "original_error": q.get("error")} for q in failed],
    "page_size": 1000, "requirements": ["one complete page with stable original declared total", "unique URLs and contiguous NUM", "all original unique records must be present with identical non-NUM fields", "no source duplicate is silently discarded"],
}, immutable=True)


def query(old):
    symbol, year = old["symbol"], old["year"]
    if old["provider"] != "SSE":
        raise ValueError("Only the documented SSE pagination condition is covered")
    params = {"isPagination": "true", "pageHelp.pageSize": 1000, "pageHelp.pageNo": 1,
              "pageHelp.beginPage": 1, "pageHelp.cacheSize": 1, "pageHelp.endPage": 1,
              "type": "inParams", "sqlId": "COMMON_PL_JJXX_JJGG_NEW_L", "TITLE": "",
              "SECURITY_CODE": symbol[2:], "BULLETIN_TYPE": "", "OTHER_TYPE": "",
              "START_DATE": f"{year}-01-01", "END_DATE": min(f"{year}-12-31", c.P["period"]["through"])}
    target = out / "catalogues" / symbol / str(year)
    result = {"symbol": symbol, "year": year, "provider": "SSE", "status": "FAILED"}
    try:
        receipt = c.capture(target / "page_001.json", c.SSE + "?" + urlencode(params))
        d = read(root / receipt["path"])
        h, rows = d["pageHelp"], d["result"]
        original_total = read(root / old["pages"][0]["path"])["pageHelp"]["total"]
        if not (h["pageSize"] == 1000 and h["pageCount"] == h["pageNo"] == 1 and h["total"] == original_total == len(rows) == len({r["URL"] for r in rows})):
            raise ValueError("Single-page counts/unique documents differ")
        if rows != h["data"] or [int(r["NUM"]) for r in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("Response copies or sequence differ")
        records = []
        for row in rows:
            if row["SECURITY_CODE"] != symbol[2:] or not params["START_DATE"] <= row["SSEDATE"] <= params["END_DATE"]:
                raise ValueError("Unexpected source code or date")
            if not row["URL"].startswith("/disclosure/fund/announcement/") or not row["URL"].lower().endswith(".pdf"):
                raise ValueError("Unexpected PDF source")
            records.append({"symbol": symbol, "title": row["TITLE"], "catalogue_date": row["SSEDATE"],
                            "url": "https://www.sse.com.cn" + row["URL"], "raw": row})
        lookup = {row["url"]: row for row in records}
        for row in old["records"]:
            before = {k: v for k, v in row["raw"].items() if k != "NUM"}
            after = {k: v for k, v in lookup[row["url"]]["raw"].items() if k != "NUM"}
            if before != after:
                raise ValueError("Original non-NUM catalogue fields changed")
        result.update(status="COMPLETE", total=len(records), records=records, pages=[receipt],
                      original_unique=len({row["url"] for row in old["records"]}),
                      recovered_documents=len(records) - len({row["url"] for row in old["records"]}))
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result["completed_at"] = now()
    save(target / "query.json", result, immutable=True)
    return result


queries = []
with ThreadPoolExecutor(max_workers=3) as pool:
    for future in as_completed([pool.submit(query, item) for item in failed]):
        row = future.result(); queries.append(row)
        print("followup", row["symbol"], row["year"], row["status"], row.get("recovered_documents"), row.get("error", ""), flush=True)
documents = []
selected = [r for q in queries if q["status"] == "COMPLETE" for r in q["records"] if "产品资料概要" in r["title"] and "联接" not in r["title"]]
with ThreadPoolExecutor(max_workers=3) as pool:
    for i, row in enumerate(pool.map(c.download, selected), 1):
        documents.append(row)
        if i % 10 == 0 or row["status"] != "RECEIVED": print("documents", i, len(selected), row["status"], flush=True)
save(out / "collection.json", {"status": "COMPLETE" if all(q["status"] == "COMPLETE" for q in queries) and all(d["status"] == "RECEIVED" for d in documents) else "PARTIAL", "completed_at": now(), "queries": sorted(queries, key=lambda q: (q["symbol"], q["year"])), "documents": documents}, immutable=True)
print("FINISHED", len(queries), len(documents), flush=True)
