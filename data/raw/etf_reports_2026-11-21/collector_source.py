"""Archive every registered ETF period report and its publication notices."""

import datetime as dt
import hashlib
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from quantfolio.storage import load_gzip, now, read, save, sha

ROOT = Path.cwd()
PROTOCOL = ROOT / "configs/protocols/etf_holdings.json"
P = read(PROTOCOL)
OUT = ROOT / P["raw_output"]
PERIOD = re.compile(r"(20\d{2})年(?:第([1-4一二三四])季度|(年度|中期|半年度))报告")


def inventory():
    if sha(ROOT / P["catalogue"]) != P["catalogue_sha256"]:
        raise ValueError("Frozen catalogue changed")
    selected, excluded = [], []
    for row in load_gzip(ROOT / P["catalogue"]):
        match = PERIOD.search(row["title"])
        if match is None:
            if "报告" in row["title"]:
                raise ValueError("Unclassified report title: " + row["title"])
            excluded.append({"symbol": row["symbol"], "url": row["url"], "reason": "not a period-report title"})
            continue
        year, quarter, kind = match.groups()
        year = int(year)
        kind = "quarterly" if quarter else "annual" if kind == "年度" else "interim"
        if quarter:
            quarter = int(quarter) if quarter.isdigit() else "一二三四".index(quarter) + 1
            month = quarter * 3
        else:
            month = 12 if kind == "annual" else 6
        end = (dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)) - dt.timedelta(days=1)
        notice = "提示性公告" in row["title"]
        selected.append({**row, "report_type": kind, "report_year": year, "quarter": quarter,
                         "title_period_end": end.isoformat(), "publication_notice": notice})
    full = [r for r in selected if not r["publication_notice"]]
    if Counter(r["symbol"] for r in full) != Counter({s: 24 for s in P["symbols"]}):
        raise ValueError("Unexpected fund report coverage")
    if len(full) != 528 or len(selected) - len(full) != 24:
        raise ValueError("Report inventory changed")
    keys = [(r["symbol"], r["report_type"], r["title_period_end"]) for r in full]
    if len(set(keys)) != len(keys):
        raise ValueError("Multiple versions need explicit adjudication")
    save(OUT / "inventory.json", {"selected": selected, "excluded": excluded,
         "catalogue_records": len(selected) + len(excluded), "fund_reports": len(full),
         "publication_notices": 24, "protocol_sha256": sha(PROTOCOL)}, immutable=True)
    return sorted(selected, key=lambda r: (r["symbol"], r["catalogue_date"], r["url"]))


def download(row):
    suffix = hashlib.sha256(row["url"].encode()).hexdigest()[:12]
    path = OUT / "documents" / row["symbol"] / (row["catalogue_date"] + "_" + suffix + ".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"url": row["url"], "requested_at": now(), "status": "FAILED"}
    try:
        request = Request(row["url"], headers={"User-Agent": "Mozilla/5.0",
                          "Referer": "https://www.cninfo.com.cn/" if row["symbol"].startswith("sz") else "https://www.sse.com.cn/"})
        with urlopen(request, timeout=40) as response:
            body = response.read(30_000_001)
            receipt.update(http_status=response.status, final_url=response.url,
                           content_type=response.headers.get("Content-Type"))
        if len(body) > 30_000_000:
            raise ValueError("Response exceeds the registered archive safety limit")
        with path.open("xb") as stream:
            stream.write(body)
        receipt.update(path=path.relative_to(ROOT).as_posix(), sha256=sha(path), size_bytes=path.stat().st_size)
        if not body.startswith(b"%PDF-"):
            raise ValueError("Response is not a PDF")
        receipt["status"] = "RECEIVED"
    except Exception as error:
        receipt["error"] = f"{type(error).__name__}: {error}"
    receipt["completed_at"] = now()
    save(path.with_suffix(".pdf.receipt.json"), receipt, immutable=True)
    time.sleep(0.2)
    return {**row, "download": receipt, "status": receipt["status"]}


def main():
    if (OUT / "protocol.json").exists():
        raise FileExistsError("Preserve this attempt and use a separately registered retry")
    save(OUT / "protocol.json", {"started_at": now(), "protocol_sha256": sha(PROTOCOL),
         "source_sha256": sha(Path(__file__))}, immutable=True)
    rows = inventory()
    documents = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for index, result in enumerate(pool.map(download, rows), 1):
            documents.append(result)
            if index % 25 == 0 or result["status"] != "RECEIVED":
                print(index, "/", len(rows), result["symbol"], result["status"],
                      result["download"].get("error", ""), flush=True)
    save(OUT / "collection.json", {"status": "COMPLETE" if all(d["status"] == "RECEIVED" for d in documents) else "PARTIAL",
         "completed_at": now(), "protocol_sha256": sha(PROTOCOL), "source_sha256": sha(Path(__file__)),
         "documents": documents, "counts": dict(Counter(d["status"] for d in documents)),
         "content_identity_reviewed": False, "model_inputs_changed": False}, immutable=True)
    print("FINISHED", Counter(d["status"] for d in documents), flush=True)


if __name__ == "__main__":
    main()
