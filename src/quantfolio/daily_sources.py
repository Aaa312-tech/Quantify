"""Daily feed parsing and comparison with archived observations."""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import numpy as np
import pandas as pd

from .market import risk_warning
from .market_data import COLUMNS

BAOSTOCK_FIELDS = (
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "adjustflag",
    "turn",
    "tradestatus",
    "pctChg",
    "peTTM",
    "pbMRQ",
    "psTTM",
    "pcfNcfTTM",
    "isST",
)
OHLCV = ("open", "close", "high", "low", "volume_lots")


def _number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Invalid daily-feed number") from error
    if not result.is_finite():
        raise ValueError("Nonfinite daily-feed number")
    return result


def _bar(values, date):
    if set(values) != set(COLUMNS):
        raise ValueError("Daily-feed fields differ from the raw bar contract")
    prices = [values[k] for k in ("open", "close", "high", "low")]
    if (
        min(prices) <= 0
        or values["high"] < max(prices)
        or values["low"] > min(prices)
        or min(values[k] for k in ("volume_lots", "amount", "turnover", "amplitude")) < 0
    ):
        raise ValueError("Invalid daily-feed prices or activity")
    frame = pd.DataFrame(
        [[float(values[k]) for k in COLUMNS]],
        columns=COLUMNS,
        index=pd.to_datetime([date]),
        dtype=float,
    )
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("Daily-feed values exceed storage precision")
    return frame


def baostock_bars(payload, meta, date):
    """Use native percent precision and round shares to the existing whole-lot contract."""
    if (
        payload.get("error_code") != "0"
        or payload.get("fields") != list(BAOSTOCK_FIELDS)
        or payload.get("kind") not in {"stocks", "funds"}
    ):
        raise ValueError("Unexpected BaoStock daily response schema or error")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Empty BaoStock daily response")
    result, seen = {}, set()
    for raw in rows:
        if not isinstance(raw, list) or len(raw) != len(BAOSTOCK_FIELDS):
            raise ValueError("Malformed BaoStock daily row")
        row = dict(zip(BAOSTOCK_FIELDS, raw, strict=True))
        if row["date"] != date or not re.fullmatch(r"(?:sh|sz)\.\d{6}", row["code"]):
            raise ValueError("BaoStock date or security identity differs")
        symbol = row["code"].replace(".", "")
        if symbol in seen:
            raise ValueError("Duplicate BaoStock security")
        seen.add(symbol)
        if symbol not in meta:
            continue
        instrument = meta[symbol]
        if instrument["symbol"] != symbol or instrument["code"] != symbol[2:]:
            raise ValueError("BaoStock security identity differs from metadata")
        expected_kind = "funds" if instrument["kind"] == "etf" else "stocks"
        if (
            payload["kind"] != expected_kind
            or row["adjustflag"] != "3"
            or row["tradestatus"] != "1"
            or row["isST"] not in {"0", "1"}
        ):
            raise ValueError("Wrong kind, adjustment or daily status in BaoStock row")
        closing = pd.Timestamp(date, tz="Asia/Shanghai") + pd.Timedelta(hours=16)
        if (
            instrument["kind"] == "stock"
            and row["isST"] == "1"
            and risk_warning(instrument, date, as_of=closing) is None
        ):
            raise ValueError("Unreviewed stock risk-warning flag in current daily feed")
        d = {
            key: _number(row[key])
            for key in (
                "open",
                "close",
                "high",
                "low",
                "preclose",
                "volume",
                "amount",
                "turn",
                "pctChg",
            )
        }
        quantum = Decimal(".000001") if instrument["kind"] == "etf" else Decimal(".0001")
        if d["preclose"] <= 0 or d["volume"] < 0 or d["volume"] % 1 or d["turn"] % quantum:
            raise ValueError("Invalid reference price, shares or turnover precision")
        pct = (d["close"] / d["preclose"] - 1) * 100
        if abs(pct - d["pctChg"]) > Decimal(".00005"):
            raise ValueError("BaoStock percent change conflicts with its reference price")
        values = {
            **{k: d[k] for k in ("open", "close", "high", "low", "amount")},
            "volume_lots": (d["volume"] / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP),
            "turnover": d["turn"],
            "pct": pct,
            "change": d["close"] - d["preclose"],
            "amplitude": (d["high"] - d["low"]) / d["preclose"] * 100,
        }
        result[symbol] = (
            _bar(values, date),
            {
                "normalized_values": {k: str(v) for k, v in values.items()},
                "source_preclose": row["preclose"],
                "source_volume_shares": row["volume"],
                "source_tradestatus": row["tradestatus"],
                "source_isST": row["isST"],
                "turnover_quantum": str(quantum),
            },
        )
    return result


def quote_entries(payload):
    """Decode only the quote envelope; instrument-specific validation follows separately."""
    entries = re.findall(r'v_([A-Za-z0-9_]+)="(.*?)";', payload.decode("gb18030"), re.S)
    if not entries or len({symbol for symbol, _ in entries}) != len(entries):
        raise ValueError("Empty or duplicate closing-quote envelope")
    return {symbol: text.split("~") for symbol, text in entries}


def tencent_beijing_bar(payload, quote, instrument, start, date, existing):
    """Validate a dated Beijing close against unadjusted OHLCV."""
    symbol = instrument["symbol"]
    if not symbol.startswith("bj") or len(quote) <= 57 or quote[2] != instrument["code"]:
        raise ValueError("Tencent adapter accepts matching Beijing securities only")
    timestamp = dt.datetime.strptime(quote[30], "%Y%m%d%H%M%S")
    if timestamp.date().isoformat() != date or timestamp.hour < 15:
        raise ValueError("Tencent quote is stale or not a completed close")
    volume, amount = _number(quote[6]), _number(quote[57]) * 10000
    compound = [_number(value) for value in quote[35].split("/")]
    if (
        len(compound) != 3
        or compound[0] != _number(quote[3])
        or compound[1] != volume
        or _number(quote[36]) != volume
        or abs(compound[2] - amount) >= 1
    ):
        raise ValueError("Tencent repeated volume or amount fields disagree")
    values = {
        key: _number(quote[index])
        for key, index in {
            "open": 5,
            "close": 3,
            "high": 33,
            "low": 34,
            "turnover": 38,
            "amplitude": 43,
            "pct": 32,
            "change": 31,
        }.items()
    }
    values.update(volume_lots=volume, amount=compound[2])
    reference_close = _number(quote[4])
    if reference_close <= 0 or abs(
        values["change"] - (values["close"] - reference_close)
    ) > Decimal(".0000001"):
        raise ValueError("Tencent change conflicts with its reference price")
    expected_pct = (values["close"] / reference_close - 1) * 100
    expected_amplitude = (values["high"] - values["low"]) / reference_close * 100
    if abs(values["pct"] - expected_pct) > Decimal(".005") or abs(
        values["amplitude"] - expected_amplitude
    ) > Decimal(".005"):
        raise ValueError("Tencent percent fields conflict with its reference price")
    frame = _bar(values, date)
    text = payload.decode("utf-8")
    if "=" not in text:
        raise ValueError("Missing Tencent daily envelope")
    data = json.loads(text.split("=", 1)[1].rstrip(";\n "))
    if data.get("code") != 0 or symbol not in data.get("data", {}):
        raise ValueError("Tencent daily response has another security or an error")
    rows = data["data"][symbol].get("day")
    if not rows or any(not isinstance(row, list) or len(row) < 6 for row in rows):
        raise ValueError("Missing unadjusted Tencent daily rows")
    dates = [str(pd.Timestamp(row[0]).date()) for row in rows]
    if dates != sorted(set(dates)) or dates[-1] > date:
        raise ValueError("Duplicate, unordered or future Tencent daily dates")
    selected = [row for row in rows if start <= row[0] <= date]
    expected = [str(day.date()) for day in existing.index if start <= str(day.date()) < date]
    if [row[0] for row in selected] != [*expected, date]:
        raise ValueError("Tencent daily overlap differs from observed history")
    checks = 0
    for row in selected:
        reference = values if row[0] == date else existing.loc[pd.Timestamp(row[0])]
        for key, value in zip(OHLCV, row[1:6], strict=True):
            if abs(_number(reference[key]) - _number(value)) > Decimal(".0000001"):
                raise ValueError(f"Tencent daily OHLCV conflicts: {symbol}/{row[0]}/{key}")
            checks += 1
    return frame, {
        "normalized_values": {k: str(v) for k, v in values.items()},
        "source_preclose": str(reference_close),
        "quote_timestamp": quote[30],
        "daily_ohlcv_checks": checks,
        "provider_independent_corroboration": False,
    }


def compare_target(primary, details, provider):
    """Compare provider values at their declared precision."""
    mismatches = []
    for key in COLUMNS:
        left, right = _number(primary[key]), _number(details["normalized_values"][key])
        difference = abs(left - right)
        tolerance = Decimal(".0000001")
        if provider == "baostock_daily":
            if key in {"pct", "amplitude"}:
                tolerance = Decimal(".005")
            elif key == "turnover":
                tolerance = Decimal(".005") + _number(details["turnover_quantum"]) / 2
        if key == "volume_lots":
            tolerance = 0
        passed = difference < 1 if key == "amount" else difference <= tolerance
        if not passed:
            mismatches.append(
                {
                    "field": key,
                    "primary": str(left),
                    "source": str(right),
                    "absolute_delta": str(difference),
                }
            )
    return mismatches
