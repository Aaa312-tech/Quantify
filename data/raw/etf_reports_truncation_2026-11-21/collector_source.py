"""Refetch the one original PDF that failed the independent structural reader."""

from pathlib import Path
from urllib.request import Request, urlopen

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
out = root / "data/raw/etf_reports_truncation_2026-11-21"
protocol = read(out / "protocol.json")
row = protocol["source"]
target = out / "documents" / row["symbol"] / Path(row["download"]["path"]).name
target.parent.mkdir(parents=True, exist_ok=True)
receipt = {"requested_at": now(), "url": row["url"], "source_sha256": sha(Path(__file__)), "status": "FAILED"}
try:
    request = Request(row["url"], headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=60) as response:
        body = response.read(160_000_001)
        receipt.update(http_status=response.status, final_url=response.url, content_length=response.headers.get("Content-Length"), content_type=response.headers.get("Content-Type"))
    if len(body) > 160_000_000:
        raise ValueError("Response exceeds the archive size cap")
    with target.open("xb") as stream:
        stream.write(body)
    receipt.update(path=target.relative_to(root).as_posix(), sha256=sha(target), size_bytes=len(body), eof_marker_in_tail=b"%%EOF" in body[-4096:])
    if not body.startswith(b"%PDF-") or not receipt["eof_marker_in_tail"]:
        raise ValueError("Refetched PDF remains structurally incomplete")
    if receipt["content_length"] and int(receipt["content_length"]) != len(body):
        raise ValueError("Response Content-Length differs from received bytes")
    receipt["status"] = "RECEIVED"
except Exception as error:
    receipt["error"] = f"{type(error).__name__}: {error}"
receipt["completed_at"] = now()
save(target.with_suffix(".pdf.receipt.json"), receipt, immutable=True)
save(out / "collection.json", {"completed_at": now(), "source_sha256": sha(Path(__file__)), "documents": [{**row, "prior_download": row["download"], "download": receipt, "status": receipt["status"]}], "status": receipt["status"]}, immutable=True)
print(receipt, flush=True)
