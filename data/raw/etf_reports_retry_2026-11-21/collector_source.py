"""Retry only the two terminal download failures, preserving their original receipts."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from quantfolio.storage import now, read, save, sha

ROOT = Path.cwd()
OUT = ROOT / "data/raw/etf_reports_retry_2026-11-21"
SOURCE = ROOT / "data/raw/etf_reports_2026-11-21/collection.json"
MAX_BYTES = 160_000_000


def download(row):
    path = OUT / "documents" / row["symbol"] / (row["catalogue_date"] + "_" + hashlib.sha256(row["url"].encode()).hexdigest()[:12] + ".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"requested_at": now(), "url": row["url"], "status": "FAILED"}
    try:
        request = Request(row["url"], headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.sse.com.cn/" if row["symbol"].startswith("sh") else "https://www.cninfo.com.cn/"})
        with urlopen(request, timeout=60) as response:
            body = response.read(MAX_BYTES + 1)
            receipt.update(http_status=response.status, final_url=response.url,
                           content_type=response.headers.get("Content-Type"))
        if len(body) > MAX_BYTES:
            raise ValueError("Response exceeds the retry size cap")
        with path.open("xb") as stream:
            stream.write(body)
        receipt.update(path=path.relative_to(ROOT).as_posix(), size_bytes=len(body), sha256=sha(path))
        if not body.startswith(b"%PDF-"):
            raise ValueError("Not a PDF")
        receipt["status"] = "RECEIVED"
    except Exception as error:
        receipt["error"] = f"{type(error).__name__}: {error}"
    receipt["completed_at"] = now()
    save(path.with_suffix(".pdf.receipt.json"), receipt, immutable=True)
    print(row["symbol"], row["catalogue_date"], receipt["status"], receipt.get("size_bytes"), flush=True)
    return {**row, "prior_download": row["download"], "download": receipt, "status": receipt["status"]}


def main():
    if (OUT / "protocol.json").exists():
        raise FileExistsError("Do not repeat this registered attempt")
    source = read(SOURCE)
    rows = [r for r in source["documents"] if r["status"] == "FAILED"]
    if len(rows) != 2 or source["counts"] != {"RECEIVED": 550, "FAILED": 2}:
        raise ValueError("Unexpected terminal source collection")
    save(OUT / "protocol.json", {"registered_at": now(), "source_collection": SOURCE.relative_to(ROOT).as_posix(),
         "source_collection_sha256": sha(SOURCE), "source_sha256": sha(Path(__file__)),
         "original_process_exit_code": 0, "original_terminal_session": 34835,
         "maximum_response_bytes": MAX_BYTES, "tls_verification": True,
         "scope": [{"symbol": r["symbol"], "url": r["url"], "failure": r["download"]["error"]} for r in rows]}, immutable=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        documents = list(pool.map(download, rows))
    save(OUT / "collection.json", {"completed_at": now(), "source_sha256": sha(Path(__file__)),
         "documents": documents, "status": "COMPLETE" if all(r["status"] == "RECEIVED" for r in documents) else "PARTIAL"}, immutable=True)


if __name__ == "__main__":
    main()
