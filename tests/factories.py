"""Deterministic synthetic inputs shared by independent behavioral tests."""

import numpy as np
import pandas as pd

STOCK_META = {"code": "600001", "symbol": "sh600001", "kind": "stock", "name": "TEST"}


def market_bundle():
    dates = pd.bdate_range("2026-01-05", periods=30)
    symbols = [f"sh60000{i}" for i in range(1, 7)] + ["sh510300"]
    meta = {
        s: {
            "symbol": s,
            "code": s[2:],
            "kind": "etf" if s == "sh510300" else "stock",
            "industry": s,
            "name": s,
        }
        for s in symbols
    }
    raws = {
        s: pd.DataFrame(
            {
                "open": 10.0,
                "close": 10.0,
                "high": 10.5,
                "low": 9.0,
                "volume_lots": 1000000.0,
                "amount": 1e9,
            },
            index=dates,
        )
        for s in symbols
    }

    def panel(x):
        return pd.DataFrame(x, index=dates, columns=symbols)

    panels = {
        "eligible": panel(True),
        "trend20": panel(0.03),
        "adv20": panel(1e9),
        "ret1": panel(0.0),
        "raw_close": panel(10.0),
        "close": panel(np.repeat((10 + np.arange(30) * 0.01)[:, None], 7, axis=1)),
    }
    panels["ret1"].iloc[:, :] = np.random.default_rng(42).normal(0, 0.01, (30, 7))
    table = pd.DataFrame(
        [
            {"date_index": i, "symbol": s, "atr_pct": 0.03}
            for i in range(len(dates))
            for s in symbols
        ]
    )
    b = {
        "meta": meta,
        "raws": raws,
        "calendar": dates,
        "panels": panels,
        "table": table,
        "actions": [],
    }
    fc = {
        i: {s: {"rank": 1 - j * 0.1, "mu": 0.05, "margin": 0.0} for j, s in enumerate(symbols[:-1])}
        for i in range(len(dates))
    }
    return b, fc


def bundle_prefix(bundle, index):
    return {**bundle, "calendar": bundle["calendar"][: index + 1]}


def trading_calendar(bundle):
    return {
        "timezone": "Asia/Shanghai",
        "coverage_start": "2026-01-01",
        "coverage_end": "2026-02-28",
        "sessions": [str(d.date()) for d in bundle["calendar"]],
    }


def opening_quotes(intent, price=10):
    return {
        s: {
            "open": price,
            "market_at": intent["execution_date"] + "T09:25:00+08:00",
            "suspended": False,
        }
        for s in intent["contracts"]
    }


def raw_history():
    calendar = pd.bdate_range("2025-01-02", periods=145)
    meta, raws = {}, {}
    for number, symbol in enumerate(("sh510300", "sh600001", "sz300001", "sh600002")):
        meta[symbol] = {
            "symbol": symbol,
            "code": symbol[2:],
            "name": "Example",
            "kind": "etf" if number == 0 else "stock",
            "industry": "Example",
        }
        close = 10 + np.arange(len(calendar)) * (number + 1) * 0.01
        raws[symbol] = pd.DataFrame(
            {
                "open": close,
                "close": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "volume_lots": 10000.0,
                "amount": 1e8,
                "turnover": 1.0,
            },
            index=calendar,
        )
    return meta, raws


def fund_profile(*, known=None, unmatched="0", undisclosed="0", residual="0"):
    return {
        "mode": "reported",
        "reported_nav_cny": "100",
        "matched_divisions": {key: {"amount_cny": value} for key, value in (known or {}).items()},
        "unmatched_named": {"amount_cny": unmatched},
        "undisclosed_stocks": {"amount_cny": undisclosed},
        "signed_equity_sector_residual": {"amount_cny": residual},
        "reported_index_contracts": [],
        "period_end": "2025-12-31",
        "source_report": "synthetic/source.pdf",
        "source_sha256": "0" * 64,
        "complete_economic_exposure_certified": False,
        "economic_exposure_upper_bound": None,
    }


def fund_history_entry(day, value):
    return {
        "available_at": str(day.date()) + "T00:00:00+08:00",
        "industry": "benchmark:csi_300" if value is not None else None,
        "profile": value,
    }


def flat_bars(n=12):
    dates = pd.bdate_range("2025-01-02", periods=n)
    raw = pd.DataFrame(
        {
            "open": 10.0,
            "close": 10.0,
            "high": 10.1,
            "low": 9.9,
            "volume_lots": 10000.0,
            "amount": 10000000.0,
            "turnover": 1.0,
        },
        index=dates,
    )
    return dates, raw
