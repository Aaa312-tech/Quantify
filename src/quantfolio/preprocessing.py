"""Build the research dataset from unadjusted bars and dated corporate actions."""

from collections import defaultdict
from copy import deepcopy

import numpy as np
import pandas as pd

from .features import (
    base_features,
    cross_ranks,
    industry_features,
    momentum_features,
    price_features,
)
from .ledger import causal_price_index
from .market import LABEL_STATUS, legal_label, risk_warning
from .storage import log

PANEL_FIELDS = (
    "close",
    "open",
    "raw_close",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_prev",
    "ret1",
    "mom5",
    "mom20",
    "mom60",
    "vol20",
    "vol60",
    "trend20",
    "trend60",
    "distance_high20",
    "adv20",
    "volume_ratio",
    "age",
    "price_ratio",
)
CONTEXT_FEATURES = (
    "market_ret1",
    "market_mom20",
    "market_vol20",
    "breadth",
    "is_etf",
    "is_growth_board",
    "is_bse",
)


@pd.option_context("compute.use_numexpr", False)
def rank_panels(frames, meta, calendar, quarantined=()):
    """Apply the frozen eligibility thresholds and fixed cross-sectional scores."""
    panels = {
        name: pd.DataFrame({s: f[name] for s, f in frames.items()}).reindex(calendar)
        for name in PANEL_FIELDS
    }
    eligible = (
        (panels["age"] >= 126)
        & (panels["adv20"] >= 5e7)
        & (panels["raw_close"] >= 2)
        & panels["close"].notna()
    )
    for symbol, instrument in meta.items():
        if instrument["kind"] == "etf":
            eligible[symbol] = (
                (panels["age"][symbol] >= 126)
                & (panels["adv20"][symbol] >= 2e7)
                & panels["close"][symbol].notna()
            )
        if "退" in instrument["name"] or (
            "ST" in instrument["name"].upper() and not instrument.get("risk_warning_periods")
        ):
            eligible[symbol] = False
        if instrument.get("risk_warning_periods"):
            for date in calendar:
                closing = date.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=16)
                if risk_warning(instrument, date, as_of=closing) is not None:
                    eligible.at[date, symbol] = False
    for symbol in quarantined:
        if symbol not in meta:
            raise ValueError(f"Unknown quarantined security: {symbol}")
        eligible[symbol] = False

    def rank(values):
        return values.where(eligible).rank(axis=1, pct=True)

    scores = {
        "momentum": (
            0.35 * rank(panels["mom20"])
            + 0.20 * rank(panels["mom5"])
            + 0.20 * rank(panels["distance_high20"])
            + 0.10 * rank(-abs(np.log(panels["volume_ratio"].clip(lower=0.01) / 1.5)))
            + 0.15 * rank(-panels["vol20"])
        )
        * 100,
        "lowvol": (0.7 * rank(-panels["vol60"]) + 0.3 * rank(panels["mom60"])) * 100,
        "reversal": (0.6 * rank(-panels["mom5"]) + 0.4 * rank(panels["mom60"])) * 100,
    }
    panels["eligible"] = eligible
    return panels, scores


def _labels(frame, instrument, raw, calendar, actions):
    eligible = np.flatnonzero(frame.eligible.to_numpy())
    for horizon in (1, 3, 5):
        values = np.full(len(calendar), np.nan, dtype="float32")
        ends = np.full(len(calendar), -1, dtype="int16")
        status = np.full(len(calendar), LABEL_STATUS["INELIGIBLE"], dtype="uint8")
        delay = np.zeros(len(calendar), dtype="int16")
        for index in eligible:
            values[index], ends[index], status[index], delay[index] = legal_label(
                instrument, raw, calendar, int(index), horizon, actions
            )
        for name, column in zip(("y", "end", "status", "delay"), (values, ends, status, delay)):
            frame[f"{name}{horizon}"] = column


@pd.option_context("compute.use_numexpr", False)
def build_dataset(meta, raws, actions, *, quarantined=()):
    """Regenerate panels, scores, 57 model inputs and legal labels without future bars."""
    if set(meta) != set(raws) or "sh510300" not in raws:
        raise ValueError("Metadata and raw securities must match and include the benchmark")
    calendar = raws["sh510300"].index.copy()
    if calendar.empty or calendar.has_duplicates or not calendar.is_monotonic_increasing:
        raise ValueError("Benchmark sessions must be nonempty, unique and ordered")
    if len(calendar) > np.iinfo(np.int16).max:
        raise ValueError("Calendar exceeds the registered date-index storage range")
    grouped = defaultdict(list)
    for action in actions:
        if action["symbol"] not in meta:
            raise ValueError("Corporate action has no matching security")
        grouped[action["symbol"]].append(action)
    benchmark, _ = causal_price_index(raws["sh510300"], grouped["sh510300"])
    benchmark_returns = benchmark.close.pct_change(fill_method=None)
    prices = {}
    for count, (symbol, raw) in enumerate(raws.items(), 1):
        if (
            raw.empty
            or raw.index.has_duplicates
            or not raw.index.is_monotonic_increasing
            or not raw.index.isin(calendar).all()
        ):
            raise ValueError(f"Invalid raw sessions: {symbol}")
        causal, splits = causal_price_index(raw, grouped[symbol])
        normalized = raw.copy()
        normalized["volume_lots"] = raw.volume_lots / splits
        prices[symbol] = price_features(normalized, causal, benchmark_returns)
        if count % 100 == 0:
            log("Price features built", securities=count, total=len(raws))
    panels, scores = rank_panels(prices, meta, calendar, quarantined)
    breadth = ((panels["trend20"] > 0) & panels["eligible"]).sum(axis=1) / panels["eligible"].sum(
        axis=1
    ).replace(0, np.nan)
    frames = []
    for count, (symbol, instrument) in enumerate(meta.items(), 1):
        raw = raws[symbol]
        frame = base_features(raw, prices[symbol]).reindex(calendar)
        base_columns = list(frame)
        frame["market_ret1"] = benchmark_returns.reindex(calendar)
        frame["market_mom20"] = panels["mom20"]["sh510300"]
        frame["market_vol20"] = panels["vol20"]["sh510300"]
        frame["breadth"] = breadth
        for name in ("momentum", "lowvol", "reversal"):
            frame[f"{name}_score"] = scores[name][symbol] / 100
        frame["is_etf"] = float(instrument["kind"] == "etf")
        frame["is_growth_board"] = float(
            instrument["code"].startswith(("300", "301", "688", "689"))
        )
        frame["is_bse"] = float(symbol.startswith("bj"))
        # Historical ranks were computed after float32 conversion; preserve that boundary.
        frame = frame.astype("float32")
        frame.insert(0, "date_index", np.arange(len(calendar), dtype="int16"))
        frame.insert(1, "symbol", symbol)
        frame["eligible"] = panels["eligible"][symbol].fillna(False).to_numpy(dtype=bool)
        _labels(frame, instrument, raw, calendar, grouped[symbol])
        frames.append(frame)
        if count % 100 == 0:
            log("Legal labels built", securities=count, total=len(meta))
    table = pd.concat(frames, ignore_index=True)
    table["symbol"] = table.symbol.astype("category")
    table = table.sort_values(["date_index", "symbol"]).reset_index(drop=True)
    ranks = cross_ranks(table, base_columns).astype("float32")
    table = pd.concat([table, ranks], axis=1)
    momentum = momentum_features(table)
    table = pd.concat([table, momentum], axis=1)
    industry, sector = industry_features(table, meta)
    table["industry"] = sector
    table = pd.concat([table, industry], axis=1)
    features = [*ranks, *CONTEXT_FEATURES, *momentum, *industry]
    if np.isinf(table[features].to_numpy()).any():
        raise ValueError("Nonfinite model inputs")
    return {
        "meta": deepcopy(meta),
        "raws": {s: r.copy() for s, r in raws.items()},
        "actions": deepcopy(actions),
        "panels": panels,
        "scores": scores,
        "calendar": calendar,
        "table": table,
        "features": features,
        "cutoff": str(calendar[-1].date()),
    }


def compare_history(before, after, *, labels=True):
    """Require exact historical values, missingness, order and dtypes, including ranks."""
    calendar = before["calendar"]
    pd.testing.assert_index_equal(calendar, after["calendar"][: len(calendar)], exact=True)
    if before["meta"] != after["meta"] or before["features"] != after["features"]:
        raise ValueError("Historical universe or feature contract changed")
    for symbol, raw in before["raws"].items():
        pd.testing.assert_frame_equal(raw, after["raws"][symbol].loc[raw.index], check_exact=True)
    for section in ("panels", "scores"):
        if before[section].keys() != after[section].keys():
            raise ValueError(f"Historical {section} fields changed")
        for name, frame in before[section].items():
            pd.testing.assert_frame_equal(
                frame, after[section][name].loc[calendar], check_exact=True
            )
    columns = list(before["table"])
    if not labels:
        excluded = {f"{field}{h}" for h in (1, 3, 5) for field in ("y", "end", "status", "delay")}
        columns = [c for c in columns if c not in excluded]
    prefix = after["table"].loc[after["table"].date_index < len(calendar), columns]
    pd.testing.assert_frame_equal(before["table"][columns], prefix, check_exact=True)
    return {
        "status": "PASS",
        "securities": len(before["meta"]),
        "sessions": len(calendar),
        "rows": len(prefix),
        "columns": len(columns),
        "model_features": len(before["features"]),
        "panels": len(before["panels"]),
        "scores": len(before["scores"]),
        "labels_compared": labels,
        "equality": "exact values, missingness, ordering and stored dtypes",
    }
