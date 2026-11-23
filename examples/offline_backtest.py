"""Run the public execution API on invented prices and a fictional weekday calendar.

This tutorial does not load project configuration, market data or fitted models.
The simple momentum scores illustrate the Forecast contract, not a research candidate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.simulation import simulate
from quantfolio.storage import dump_gzip, save
from quantfolio.validation import execution_audit, reconcile


def synthetic_market():
    dates = pd.bdate_range("2020-01-06", periods=80)
    symbols = [f"sh60000{i}" for i in range(1, 7)] + ["sh510300"]
    meta, raws = {}, {}
    day = np.arange(len(dates))
    for number, symbol in enumerate(symbols):
        meta[symbol] = {
            "symbol": symbol,
            "code": symbol[2:],
            "name": f"Synthetic instrument {number + 1}",
            "kind": "etf" if symbol == "sh510300" else "stock",
            "industry": f"Synthetic sector {number % 4}",
        }
        close = np.round(10 + day * (0.012 + number * 0.003), 2)
        opening = np.r_[close[0], close[:-1]]
        raws[symbol] = pd.DataFrame(
            {
                "open": opening,
                "high": np.round(close * 1.01, 2),
                "low": np.round(opening * 0.99, 2),
                "close": close,
                "volume_lots": 1_000_000.0,
                "amount": 1_000_000_000.0,
            },
            index=dates,
        )
    close = pd.DataFrame({symbol: raw.close for symbol, raw in raws.items()})
    panels = {
        "close": close,
        "raw_close": close,
        "ret1": close.pct_change().fillna(0),
        "trend20": close.pct_change(20).fillna(0),
        "adv20": pd.DataFrame(1e9, index=dates, columns=symbols),
        "eligible": pd.DataFrame(
            np.repeat((day >= 20)[:, None], len(symbols), axis=1), index=dates, columns=symbols
        ),
    }
    table = pd.DataFrame(
        [
            {"date_index": index, "symbol": symbol, "atr_pct": 0.03}
            for index in range(len(dates))
            for symbol in symbols
        ]
    )
    return {
        "meta": meta,
        "raws": raws,
        "panels": panels,
        "calendar": dates,
        "table": table,
        "actions": [],
    }


def momentum_signals(bundle):
    stocks = [symbol for symbol, meta in bundle["meta"].items() if meta["kind"] == "stock"]
    scores = bundle["panels"]["close"][stocks].pct_change(20)
    ranks = scores.rank(axis=1, pct=True, method="first")
    return {
        index: {
            symbol: {
                "rank": float(ranks.iloc[index][symbol]),
                "mu": float(scores.iloc[index][symbol]) * 5 / 20,
                "margin": 0.0,
            }
            for symbol in stocks
        }
        for index in range(20, len(bundle["calendar"]) - 1)
    }


def run(output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose an unused example output directory")
    bundle = synthetic_market()
    signals = momentum_signals(bundle)
    summary, ledger = simulate(
        bundle,
        21,
        len(bundle["calendar"]) - 1,
        signals,
        mode="latched",
        rules="baseline",
        turnover="legacy",
    )
    checks = {
        "accounting": reconcile(ledger, summary, bundle["meta"], bundle["actions"]),
        "execution": execution_audit(ledger, bundle),
    }
    if any(check["status"] != "PASS" for check in checks.values()):
        raise ValueError("Synthetic example failed independent account checks")
    output.mkdir(parents=True)
    bars = pd.concat(bundle["raws"], names=["symbol", "date"]).reset_index()
    bars.to_csv(output / "bars.csv", index=False, lineterminator="\n")
    save(
        output / "inputs.json",
        {
            "kind": "SYNTHETIC",
            "calendar": "Fictional weekdays, not an exchange calendar",
            "instruments": bundle["meta"],
            "model_fits": 0,
        },
        immutable=True,
    )
    save(
        output / "signals.json",
        [
            {
                "signal_date": str(bundle["calendar"][index].date()),
                "execution_date": str(bundle["calendar"][index + 1].date()),
                "forecasts": forecasts,
            }
            for index, forecasts in signals.items()
        ],
        immutable=True,
    )
    save(output / "summary.json", summary, immutable=True)
    save(output / "checks.json", checks, immutable=True)
    dump_gzip(output / "ledger.json.gz", ledger)
    return {
        "status": "PASS",
        "kind": "SYNTHETIC",
        "sessions": len(ledger["curve"]),
        "trades": len(ledger["trades"]),
        "output": str(output),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output)))
