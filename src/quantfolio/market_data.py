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
from .storage import log, now, read, save, sha

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


def parse_bars(payload, meta, start, end):
    """Parse and validate a dated, unadjusted provider response without filling gaps."""
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
    return frame


def inspect_bars(payload, meta, start, end, existing):
    """Reject malformed bars; flag gaps and historical revisions without imputing them."""
    frame = parse_bars(payload, meta, start, end)
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


def append_bars(existing, collected, cutoff):
    """Append completed bars without revising old values, missingness or storage types."""
    cutoff = pd.Timestamp(cutoff)
    added = collected.loc[
        (collected.index > existing.index[-1]) & (collected.index <= cutoff)
    ].copy()
    if cutoff not in added.index:
        raise ValueError("Completed target bar is missing")
    if list(existing) != list(added):
        raise ValueError("Collected fields differ from the historical raw schema")
    converted = added.astype(existing.dtypes)
    if not np.array_equal(added.to_numpy(), converted.to_numpy(), equal_nan=True):
        raise ValueError("New values cannot be stored without precision loss")
    converted.index.name = existing.index.name
    result = pd.concat([existing, converted])
    pd.testing.assert_frame_equal(existing, result.loc[existing.index], check_exact=True)
    return result


def fetch_bars(paths, date, output, retry=None):
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
    symbols = sorted(bundle["meta"])
    retry_source = None
    if retry is not None:
        previous = (paths.root / retry).resolve()
        if not previous.is_relative_to(parent) or previous == parent:
            raise ValueError("Retry source must be a named directory under data/raw")
        registration = read(previous / "protocol.json")
        collection = read(previous / "collection.json")
        if (
            registration["source_bundle_sha256"] != sha(paths.bundle)
            or registration["requested_completed_date"] != date
            or collection["requested_completed_date"] != date
        ):
            raise ValueError("Retry source belongs to another dataset or requested date")
        completed = {r["symbol"] for r in collection["records"] if r["status"] != "FAILED"}
        requested = set(registration.get("symbols", symbols))
        if not requested <= set(symbols) or not completed <= requested:
            raise ValueError("Retry source contains unknown instruments")
        symbols = sorted(requested - completed)
        if not symbols:
            raise ValueError("No missing requests to retry")
        retry_source = {
            "path": previous.relative_to(paths.root).as_posix(),
            "protocol_sha256": sha(previous / "protocol.json"),
            "collection_sha256": sha(previous / "collection.json"),
        }
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
        "symbols": symbols,
        "retry_source": retry_source,
        "publication_gate": "A raw download is not a verified processed snapshot. Revisions, missing sessions, corporate actions and security status need separate review.",
    }
    save(target / "protocol.json", specification, immutable=True)
    records = []
    failures = 0
    for symbol in symbols:
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
                expected=len(symbols),
                consecutive_failures=failures,
            )
        if failures >= 3 or record.get("error", "").startswith(
            ("HTTPError: HTTP Error 429", "HTTPError: HTTP Error 403")
        ):
            break
        time.sleep(0.8)
    result = {
        "status": "COMPLETE" if len(records) == len(symbols) else "PARTIAL",
        "completed_at": now(),
        "requested_completed_date": date,
        "instruments_expected": len(symbols),
        "instruments_requested": len(records),
        "consistent": sum(r["status"] == "CONSISTENT" for r in records),
        "review": sum(r["status"] == "REVIEW" for r in records),
        "failed": sum(r["status"] == "FAILED" for r in records),
        "activated": False,
        "records": records,
    }
    save(target / "collection.json", result, immutable=True)
    return {key: value for key, value in result.items() if key != "records"}
