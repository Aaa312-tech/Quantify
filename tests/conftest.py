import numpy as np
import pandas as pd


def fixture():
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
