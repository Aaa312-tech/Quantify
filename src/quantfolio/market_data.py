"""Collect unadjusted daily bars into a separate, auditable raw-data directory."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

from .dataset import load_bundle
from .storage import log, now, save, sha

ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
PROVIDER_REFERENCE = (
    "https://github.com/akfamily/akshare/blob/main/akshare/stock/quote/stock_hist_em.py"
)
PROVIDER_BLOB = "ba34079c48201ba3311ebcad72c1561472aa9126"
COLUMNS = (
    "open",
    "close",
    "high",
    "low",
    "volume_lots",
    "amount",
    "amplitude",
    "pct",
    "change",
    "turnover",
)


def inspect_bars(payload, meta, start, end, existing):
    """Reject malformed bars; flag gaps and historical revisions without imputing them."""
    data = json.loads(payload).get("data")
    if not data or not data.get("klines"):
        raise ValueError("Provider returned no bars")
    market = 1 if meta["symbol"].startswith("sh") else 0
    if data.get("code") != meta["code"] or data.get("market") != market:
        raise ValueError("Provider security identity differs from request")
    rows = [row.split(",") for row in data["klines"]]
    if any(len(row) != len(COLUMNS) + 1 for row in rows):
        raise ValueError("Provider daily-bar schema changed")
    frame = pd.DataFrame(rows, columns=["date", *COLUMNS]).set_index("date")
    frame.index = pd.to_datetime(frame.index, format="%Y-%m-%d", errors="raise")
    frame = frame.astype(float)
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("Duplicate or unordered provider dates")
    if frame.index[0] < pd.Timestamp(start) or frame.index[-1] > pd.Timestamp(end):
        raise ValueError("Provider returned bars outside the requested completed period")
    if (
        not np.isfinite(frame.to_numpy()).all()
        or (frame[["open", "close", "high", "low"]] <= 0).any().any()
    ):
        raise ValueError("Nonfinite data or invalid price")
    if (
        (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ValueError("Daily OHLC bounds are inconsistent")
    if (frame[["volume_lots", "amount", "turnover"]] < 0).any().any():
        raise ValueError("Negative trading activity")
    overlap = frame.index.intersection(existing.index)
    revisions = []
    for date in overlap:
        for field in COLUMNS:
            old, new = existing.at[date, field], frame.at[date, field]
            tolerance = 0.011 if field == "amount" else 1e-7
            if np.isfinite(old) and not np.isclose(old, new, rtol=0, atol=tolerance):
                revisions.append(
                    {
                        "date": str(date.date()),
                        "field": field,
                        "before": float(old),
                        "after": float(new),
                    }
                )
    target_present = pd.Timestamp(end) in frame.index
    return {
        "rows": len(frame),
        "first_date": str(frame.index[0].date()),
        "last_date": str(frame.index[-1].date()),
        "target_present": target_present,
        "overlap_dates": len(overlap),
        "historical_revisions": revisions,
        "missing_baseline_fields": int(existing.reindex(overlap)[list(COLUMNS)].isna().sum().sum()),
        "status": "CONSISTENT" if target_present and not revisions else "REVIEW",
    }


def fetch_bars(paths, date, output):
    """Fetch sequentially; preserve responses and never activate an incomplete snapshot."""
    target_date = dt.date.fromisoformat(date)
    current = dt.datetime.fromisoformat(now())
    if target_date > current.date() or (target_date == current.date() and current.hour < 16):
        raise ValueError("Request only completed sessions; same-day collection opens at 16:00")
    target = (paths.root / output).resolve()
    parent = (paths.root / "data/raw").resolve()
    if (
        not parent.is_relative_to(paths.root)
        or not target.is_relative_to(parent)
        or target == parent
    ):
        raise ValueError("Raw collection must be a named directory under data/raw")
    if target.exists():
        raise FileExistsError(f"Raw collection already exists: {target}")
    bundle = load_bundle(paths)
    calendar = bundle["calendar"]
    if target_date <= calendar[-1].date():
        raise ValueError("Collection must extend the active snapshot")
    start = str(calendar[max(0, len(calendar) - 5)].date())
    specification = {
        "registered_at": now(),
        "requested_completed_date": date,
        "source_bundle_sha256": sha(paths.bundle),
        "provider_reference": PROVIDER_REFERENCE,
        "provider_blob": PROVIDER_BLOB,
        "adjustment": "unadjusted",
        "volume_unit": "100 shares",
        "amount_unit": "CNY",
        "historical_overlap_start": start,
        "activated": False,
        "publication_gate": "A raw download is not a verified processed snapshot. Revisions, missing sessions, corporate actions and security status need separate review.",
    }
    save(target / "protocol.json", specification, immutable=True)
    records = []
    failures = 0
    for symbol in sorted(bundle["meta"]):
        meta = bundle["meta"][symbol]
        market = 1 if symbol.startswith("sh") else 0
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "101",
            "fqt": "0",
            "secid": f"{market}.{meta['code']}",
            "beg": start.replace("-", ""),
            "end": date.replace("-", ""),
        }
        url = ENDPOINT + "?" + urllib.parse.urlencode(params)
        record = {"symbol": symbol, "requested_at": now(), "url": url}
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read()
            (target / f"{symbol}.json").write_bytes(payload)
            record["response_sha256"] = hashlib.sha256(payload).hexdigest()
            record.update(inspect_bars(payload, meta, start, date, bundle["raws"][symbol]))
            failures = 0
        except (OSError, ValueError, KeyError, TypeError) as error:
            failures += 1
            record.update(status="FAILED", error=f"{type(error).__name__}: {error}")
        records.append(record)
        if len(records) % 50 == 0 or failures:
            log(
                "Raw bars collected",
                requested=len(records),
                expected=len(bundle["meta"]),
                consecutive_failures=failures,
            )
        if failures >= 3 or record.get("error", "").startswith(
            ("HTTPError: HTTP Error 429", "HTTPError: HTTP Error 403")
        ):
            break
        time.sleep(0.8)
    result = {
        "status": "COMPLETE" if len(records) == len(bundle["meta"]) else "PARTIAL",
        "completed_at": now(),
        "requested_completed_date": date,
        "instruments_expected": len(bundle["meta"]),
        "instruments_requested": len(records),
        "consistent": sum(r["status"] == "CONSISTENT" for r in records),
        "review": sum(r["status"] == "REVIEW" for r in records),
        "failed": sum(r["status"] == "FAILED" for r in records),
        "activated": False,
        "records": records,
    }
    save(target / "collection.json", result, immutable=True)
    return {key: value for key, value in result.items() if key != "records"}
