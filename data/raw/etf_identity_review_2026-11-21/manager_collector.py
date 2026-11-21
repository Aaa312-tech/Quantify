"""Read the fund manager's anonymous public announcement index as its web page does."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from quantfolio.storage import now, read, save, sha

OUT = Path(__file__).resolve().parent
ROOT = Path.cwd()
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.fsfund.com/", "netNo": "web"}
URL = "https://api.fsfund.com/v2/webzk/queryController/queryArticles"
initial = read(OUT / "manager_all_catalogue.json")
rows = initial["data"]
total = rows[0]["pageSums"]
save(OUT / "manager_pagination_protocol.json", {
    "registered_at": now(), "source_sha256": sha(Path(__file__)), "initial_sha256": sha(OUT / "manager_all_catalogue.json"),
    "expected_total": total, "observed_page_size": 10, "purpose": "Find the missing same-date product summary; public latest-law tab is not a historical catalogue.",
    "credentials": "No account credentials; anonymous web signing bootstrap as published by the manager's JavaScript. Ephemeral signature and signing key are neither printed nor archived.",
}, immutable=True)
response = requests.post("https://api.fsfund.com/v2/account/account/common/getSignKey", json={"netNo": "web"}, headers=HEADERS, timeout=25).json()
if response.get("code") != "0000":
    raise ValueError("Public website signing bootstrap failed")
signing_key = response["data"]
del response


def fetch(offset):
    params = {"fundCode": "512170", "columnId": "267", "pageSize": "10", "offset": str(offset), "netNo": "web", "timestamp": str(int(time.time() * 1000))}
    unsigned = dict(params)
    params["signature"] = hashlib.md5(("".join(k + "=" + params[k] + "&" for k in sorted(params)) + "key=" + signing_key).encode()).hexdigest()
    started = now()
    result = requests.post(URL, json=params, headers=HEADERS, timeout=25)
    del params
    path = OUT / f"manager_all_{offset:03}.json"
    with path.open("xb") as stream:
        stream.write(result.content)
    save(path.with_suffix(".receipt.json"), {"url": URL, "unsigned_parameters": unsigned,
        "status": "RECEIVED", "http_status": result.status_code, "path": path.relative_to(ROOT).as_posix(), "sha256": sha(path),
        "requested_at": started, "completed_at": now(), "authentication": "Anonymous website request; signature/key omitted"}, immutable=True)
    data = result.json()
    if data.get("code") != "0000" or not isinstance(data.get("data"), list):
        raise ValueError("Public catalogue query failed")
    selected = data["data"]
    if len(selected) != min(10, total - offset) or any(row["pageSums"] != total for row in selected):
        raise ValueError("Manager catalogue totals or pages changed")
    return selected


with ThreadPoolExecutor(max_workers=3) as pool:
    for page in pool.map(fetch, range(10, total, 10)):
        rows.extend(page)
del signing_key
if len(rows) != total or len({row["id"] for row in rows}) != total:
    raise ValueError("Manager catalogue duplicate or missing article IDs")
matches = [r for r in rows if r["publishDate1"] == "2023-12-20" and "产品资料概要" in r["title"] and "联接" not in r["title"]]
save(OUT / "manager_review.json", {"status": "CATALOGUE_COMPLETE", "completed_at": now(), "total": total, "matches": matches, "source_sha256": sha(Path(__file__))}, immutable=True)
print({"total": total, "matches": [{"title": r["title"], "date": r["publishDate1"], "url": r["articleFile"]} for r in matches]}, flush=True)
