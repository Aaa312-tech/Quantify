"""Archive the registered ETF catalogues and product summaries; never update model inputs."""

import datetime as dt
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from quantfolio.storage import now, read, save, sha

ROOT = Path.cwd()
PROTOCOL = ROOT / "configs/protocols/etf_sources.json"
P = read(PROTOCOL)
OUT = ROOT / P["raw_output"]
CNINFO = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
SSE = "https://query.sse.com.cn/commonQuery.do"
SZ_NAMES = {
    "159915": "易方达创业板交易型开放式指数证券投资基金",
    "159928": "中证主要消费交易型开放式指数证券投资基金",
    "159995": "华夏国证半导体芯片交易型开放式指数证券投资基金",
}


def capture(target, url, form=None, referer="https://www.sse.com.cn/"):
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"url": url, "form": form, "requested_at": now()}
    try:
        request = Request(
            url, data=urlencode(form).encode() if form else None,
            headers={"User-Agent": "Mozilla/5.0", "Referer": referer},
        )
        with urlopen(request, timeout=25) as response:
            data = response.read(25_000_001)
            receipt.update(http_status=response.status, final_url=response.url)
        if len(data) > 25_000_000:
            raise ValueError("Response exceeds 25 MB")
        with target.open("xb") as stream:
            stream.write(data)
        receipt.update(status="RECEIVED", path=target.relative_to(ROOT).as_posix(), sha256=sha(target))
    except Exception as error:
        receipt.update(status="FAILED", error=f"{type(error).__name__}: {error}")
    receipt["completed_at"] = now()
    save(target.with_suffix(target.suffix + ".receipt.json"), receipt, immutable=True)
    time.sleep(0.15)
    if receipt["status"] != "RECEIVED":
        raise ValueError(receipt["error"])
    return receipt


def sse_page(payload, code, year, page):
    info, rows = payload["pageHelp"], payload["result"]
    total, count = info["total"], info["pageCount"]
    if type(total) is not int or total < 0 or count != math.ceil(total / 25):
        raise ValueError("Invalid SSE catalogue totals")
    if info["pageNo"] != page or info["pageSize"] != 25 or not isinstance(rows, list):
        raise ValueError("Invalid SSE pagination")
    expected = min(25, max(total - (page - 1) * 25, 0))
    if len(rows) != expected or rows != info["data"]:
        raise ValueError("SSE count or duplicated response data differs")
    result = []
    for row in rows:
        date, relative = row["SSEDATE"], row["URL"]
        dt.date.fromisoformat(date)
        if row["SECURITY_CODE"] != code or not f"{year}-01-01" <= date <= min(f"{year}-12-31", P["period"]["through"]):
            raise ValueError("Wrong SSE code or date")
        if not relative.startswith("/disclosure/fund/announcement/") or not relative.lower().endswith(".pdf"):
            raise ValueError("Unexpected SSE document location")
        result.append({"symbol": "sh" + code, "title": row["TITLE"], "catalogue_date": date,
                       "url": urljoin("https://www.sse.com.cn", relative), "raw": row})
    return result, total, count


def sse_query(symbol, year):
    destination = OUT / "catalogues" / symbol / str(year)
    records, pages, expected = [], [], None
    result = {"symbol": symbol, "year": year, "provider": "SSE", "status": "FAILED"}
    try:
        for page in range(1, 101):
            params = {"isPagination": "true", "pageHelp.pageSize": 25, "pageHelp.pageNo": page,
                      "pageHelp.beginPage": page, "pageHelp.cacheSize": 1, "pageHelp.endPage": page,
                      "type": "inParams", "sqlId": "COMMON_PL_JJXX_JJGG_NEW_L", "TITLE": "",
                      "SECURITY_CODE": symbol[2:], "BULLETIN_TYPE": "", "OTHER_TYPE": "",
                      "START_DATE": f"{year}-01-01", "END_DATE": min(f"{year}-12-31", P["period"]["through"])}
            received = capture(destination / f"page_{page:03d}.json", SSE + "?" + urlencode(params))
            pages.append(received)
            rows, total, count = sse_page(read(ROOT / received["path"]), symbol[2:], year, page)
            if expected is not None and total != expected:
                raise ValueError("SSE total changed during collection")
            expected = total
            records.extend(rows)
            if page >= count:
                break
        if len(records) != expected or len({r["url"] for r in records}) != expected:
            raise ValueError("SSE catalogue missing or duplicated documents")
        result.update(status="COMPLETE", total=expected)
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result.update(records=records, pages=pages, completed_at=now())
    save(destination / "query.json", result, immutable=True)
    return result


def cninfo_query(symbol, year, org_id=None):
    lookup = org_id is None
    target = OUT / ("lookups" if lookup else "catalogues") / symbol / str(year)
    form = {"pageSize": "30", "column": "", "tabName": "fulltext", "plate": "", "category": "",
            "stock": "" if lookup else symbol[2:] + "," + org_id,
            "searchkey": SZ_NAMES[symbol[2:]] + "基金产品资料概要" if lookup else "",
            "seDate": f"{year}-01-01~" + min(f"{year}-12-31", P["period"]["through"]),
            "sortName": "time", "sortType": "asc", "isHLtitle": "false"}
    result = {"symbol": symbol, "year": year, "provider": "CNINFO", "status": "FAILED", "lookup": lookup}
    records, pages, expected, seen = [], [], None, set()
    try:
        for page in range(1, 101):
            received = capture(target / f"page_{page:03d}.json", CNINFO, {**form, "pageNum": str(page)}, "https://www.cninfo.com.cn/")
            pages.append(received)
            payload = read(ROOT / received["path"])
            total, more = payload["totalAnnouncement"], payload["hasMore"]
            rows = payload["announcements"] or []
            if type(total) is not int or type(more) is not bool or len(rows) != min(30, max(total - (page - 1) * 30, 0)):
                raise ValueError("Invalid CNINFO pagination")
            if expected is not None and total != expected:
                raise ValueError("CNINFO total changed during collection")
            expected = total
            for row in rows:
                date = dt.datetime.fromtimestamp(row["announcementTime"] / 1000, dt.timezone(dt.timedelta(hours=8))).date().isoformat()
                relative = row["adjunctUrl"]
                identity = (row["announcementId"], row["secCode"])
                if identity in seen or not f"{year}-01-01" <= date <= min(f"{year}-12-31", P["period"]["through"]):
                    raise ValueError("CNINFO duplicate or outside date range")
                if not re.fullmatch(r"finalpage/\d{4}-\d{2}-\d{2}/[\w-]+\.[Pp][Dd][Ff]", relative):
                    raise ValueError("Unexpected CNINFO document URL")
                if not lookup and (row["secCode"] != symbol[2:] or row["orgId"] != org_id):
                    raise ValueError("CNINFO returned another fund")
                seen.add(identity)
                records.append({"symbol": "sz" + row["secCode"], "title": row["announcementTitle"], "catalogue_date": date,
                                "url": "https://static.cninfo.com.cn/" + relative, "raw": row})
            if more != (len(seen) < total):
                raise ValueError("CNINFO pagination flag disagrees with count")
            if not more:
                break
        if len(records) != expected:
            raise ValueError("Incomplete CNINFO catalogue")
        result.update(status="COMPLETE", total=expected)
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result.update(records=records, pages=pages, completed_at=now())
    save(target / "query.json", result, immutable=True)
    return result


def download(row):
    symbol = row["symbol"]
    suffix = __import__("hashlib").sha256(row["url"].encode()).hexdigest()[:12]
    target = OUT / "documents" / symbol / (row["catalogue_date"] + "_" + suffix + ".pdf")
    try:
        received = capture(target, row["url"], referer="https://www.cninfo.com.cn/" if symbol.startswith("sz") else "https://www.sse.com.cn/")
        if not target.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Response is not a PDF")
        return {**row, "download": received, "status": "RECEIVED"}
    except Exception as error:
        return {**row, "status": "FAILED", "error": f"{type(error).__name__}: {error}"}


def main():
    if (OUT / "collection.json").exists() or (OUT / "catalogues").exists():
        raise FileExistsError("Use a fresh collection; preserve every previous attempt")
    save(OUT / "protocol.json", {"registered_protocol_sha256": sha(PROTOCOL), "collector_sha256": sha(Path(__file__)), "started_at": now()}, immutable=True)
    capture(OUT / "sse_catalogue_interface.js", "https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_fundInformation_2021.js")
    identities, lookups = {}, []
    for code in SZ_NAMES:
        lookup = cninfo_query("sz" + code, 2023)
        lookups.append(lookup)
        matching = [r for r in lookup["records"] if r["symbol"] == "sz" + code]
        orgs = {r["raw"]["orgId"] for r in matching}
        if lookup["status"] == "COMPLETE" and len(orgs) == 1:
            identities["sz" + code] = next(iter(orgs))
        print("lookup", code, lookup["status"], "matching", len(matching), flush=True)
    queries = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = []
        for symbol in P["symbols"]:
            for year in range(2023, 2027):
                if symbol.startswith("sh"):
                    futures.append(pool.submit(sse_query, symbol, year))
                elif symbol in identities:
                    futures.append(pool.submit(cninfo_query, symbol, year, identities[symbol]))
        for future in as_completed(futures):
            row = future.result()
            queries.append(row)
            print("catalogue", row["symbol"], row["year"], row["status"], row.get("total"), row.get("error", ""), flush=True)
    catalogue = [r for q in queries if q["status"] == "COMPLETE" for r in q["records"]]
    summaries = [r for r in catalogue if "产品资料概要" in r["title"] and "联接" not in r["title"]]
    documents = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for index, row in enumerate(pool.map(download, sorted(summaries, key=lambda r: (r["symbol"], r["catalogue_date"], r["url"]))), 1):
            documents.append(row)
            if index % 20 == 0 or row["status"] != "RECEIVED":
                print("documents", index, "/", len(summaries), row["status"], flush=True)
    save(OUT / "collection.json", {
        "status": "COMPLETE" if len(queries) == 88 and all(q["status"] == "COMPLETE" for q in queries) and all(d["status"] == "RECEIVED" for d in documents) else "PARTIAL",
        "completed_at": now(), "protocol_sha256": sha(PROTOCOL), "lookups": lookups,
        "queries": sorted(queries, key=lambda r: (r["symbol"], r["year"])), "documents": documents,
        "catalogue_records": len(catalogue), "product_summary_records": len(summaries),
        "limitations": ["Catalog completeness is relative to the returned indexes, not proof of original publication or permanent availability.", "No document extraction or model adoption is implied by a successful download."],
    }, immutable=True)
    print("FINISHED", len(queries), len(catalogue), len(documents), flush=True)


if __name__ == "__main__":
    main()
