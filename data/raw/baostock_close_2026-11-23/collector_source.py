import contextlib
import importlib.metadata
import io
import json
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quantfolio.daily_sources import quote_entries
from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import dump_gzip, now, save, sha

root = Path.cwd()
paths = Paths(root)
bundle = load_bundle(paths)
date, start = "2026-11-23", "2026-11-16"
mode = sys.argv[1]
names = {"baostock": "baostock_close", "quotes": "tencent_close", "daily": "tencent_beijing_daily"}
providers = {"baostock": "baostock", "quotes": "tencent_closing_quote", "daily": "tencent_daily_bars"}
folder = root / "data/raw" / (names[mode] + "_" + date)
assert not folder.exists(), folder
folder.mkdir(parents=True)
(folder / "collector_source.py").write_bytes(Path(__file__).read_bytes())
symbols = sorted(s for s in bundle["meta"] if mode != "daily" or s.startswith("bj"))
save(folder / "protocol.json", {
    "registered_at": now(), "provider": providers[mode], "target_date": date,
    "start": start, "symbols": symbols, "source_bundle_sha256": sha(paths.bundle),
    "collector_sha256": sha(folder / "collector_source.py"),
    "purpose": "Complete the fixed 594-instrument universe for the 2026-11-24 decision, using existing unchanged provider parsers. Raw retrieval alone does not activate data.",
    "adjustment": "unadjusted", "request_spacing_seconds": 0.5,
    "timeout_seconds": 20, "activated": False,
}, immutable=True)
records, failure = [], None
messages = io.StringIO()

if mode == "baostock":
    import baostock as provider
    assert importlib.metadata.version("baostock") == "0.9.4"
    socket.setdefaulttimeout(20)
    logged_in = False
    try:
        with contextlib.redirect_stdout(messages):
            login = provider.login()
        if login.error_code != "0":
            raise ValueError(f"Login failed: {login.error_code} {login.error_msg}")
        logged_in = True
        for kind, method in (("stocks", provider.query_daily_history_k_AStock), ("funds", provider.query_daily_history_k_ETF)):
            requested = now()
            with contextlib.redirect_stdout(messages):
                response = method(date=date)
                rows = []
                while response.error_code == "0" and response.next():
                    rows.append(response.get_row_data())
            payload = {"requested_at": requested, "retrieved_at": now(), "kind": kind,
                       "fields": response.fields, "rows": rows,
                       "error_code": response.error_code, "error_msg": response.error_msg}
            file = folder / f"{kind}.json.gz"
            dump_gzip(file, payload)
            records.append({"kind": kind, "requested_at": requested, "retrieved_at": payload["retrieved_at"],
                            "rows": len(rows), "fields": response.fields, "error_code": response.error_code,
                            "response_sha256": sha(file), "response": file.relative_to(root).as_posix()})
            print(kind, len(rows), response.error_code, flush=True)
            if response.error_code != "0":
                raise ValueError(f"Provider error: {response.error_code}: {response.error_msg}")
            time.sleep(0.5)
    except (OSError, ValueError, TypeError, KeyError) as error:
        failure = f"{type(error).__name__}: {error}"
    finally:
        if logged_in:
            with contextlib.redirect_stdout(messages):
                provider.logout()
    complete = len(records) == 2 and failure is None
else:
    batches = [symbols[i:i+50] for i in range(0, len(symbols), 50)] if mode == "quotes" else [[s] for s in symbols]
    for index, batch in enumerate(batches, 1):
        if mode == "quotes":
            url = "https://qt.gtimg.cn/q=" + ",".join(batch)
            file = folder / f"batch_{index:02d}.txt"
            record = {"batch": index, "symbols": batch}
        else:
            symbol = batch[0]
            url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?" + urlencode({
                "_var": "kline_day2026", "param": f"{symbol},day,{start},{date},100,", "r": "0.8205512681390605"})
            file = folder / f"{symbol}.txt"
            record = {"symbol": symbol}
        record.update(requested_at=now(), url=url)
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
                payload = response.read()
            file.write_bytes(payload)
            record["response_sha256"] = sha(file)
            if mode == "quotes":
                parsed = quote_entries(payload)
                if list(parsed) != batch:
                    raise ValueError("Quote symbols differ from request")
                record.update(file=file.relative_to(root).as_posix(), sha256=sha(file), returned_symbols=list(parsed), bytes=len(payload))
            else:
                data = json.loads(payload.decode("utf-8").split("=", 1)[1].rstrip(";\n "))
                if data.get("code") != 0:
                    raise ValueError(f"Provider error: {data.get('code')}")
                rows = data["data"][batch[0]]["day"]
                if not rows or not any(row[0] == date for row in rows):
                    raise ValueError("Missing target daily bar")
                record.update(rows=len(rows), target_present=True)
            record.update(status="RECEIVED", retrieved_at=now())
        except (OSError, ValueError, TypeError, KeyError) as error:
            failure = f"{type(error).__name__}: {error}"
            record.update(status="FAILED", error=failure)
        records.append(record)
        print(mode, index, record["status"], failure or "", flush=True)
        if failure:
            break
        time.sleep(0.5)
    complete = len(records) == len(batches) and failure is None

save(folder / "collection.json", {"status": "COMPLETE" if complete else "PARTIAL",
    "completed_at": now(), "failure": failure, "records": records,
    "provider_messages": messages.getvalue(), "activated": False}, immutable=True)
print("Collection", mode, "COMPLETE" if complete else "PARTIAL", failure, flush=True)
