"""Reconstruct changed price paths and whole-market ranks without production builders."""

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, digest, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def price_path(raw, actions):
    """Aggregate dated cash independently, preserving each binary64 operation."""
    events = defaultdict(list)
    for action in sorted(actions, key=lambda row: row["ex_date"]):
        day = pd.Timestamp(action["ex_date"])
        if raw.index[0] <= day <= raw.index[-1]:
            events[int(raw.index.searchsorted(day))].append(action)
    closes = raw.close.to_numpy()
    factors, splits, gaps = [], [], []
    cumulative, split, prior = 1.0, 1.0, None
    for position, close in enumerate(closes):
        cash, multiplier = 0.0, 1.0
        for event in events[position]:
            cash = cash + float(event["cash_per_share"]) * multiplier
            multiplier = multiplier * float(event["share_multiplier"])
        change = 0.0 if prior is None else (float(close) * multiplier + cash) / prior - 1
        cumulative = cumulative * (1 + change)
        index = cumulative * float(closes[0])
        factors.append(index / float(close))
        split = split * multiplier
        splits.append(split)
        gaps.append(
            np.nan
            if prior is None
            else (float(raw.open.iloc[position]) / prior) * (multiplier + cash / float(close)) - 1
        )
        prior = float(close)
    adjusted = raw[["open", "high", "low", "close"]].mul(np.array(factors), axis=0)
    return adjusted, pd.Series(splits, index=raw.index), pd.Series(gaps, index=raw.index)


def trailing_features(raw, actions, benchmark):
    """Independent formula assembly; pandas supplies rolling and EWM arithmetic."""
    adjusted, splits, gap = price_path(raw, actions)
    close = adjusted.close
    returns = close / close.shift() - 1
    frame = pd.DataFrame(index=raw.index)
    for name in ("open", "close"):
        frame[name] = adjusted[name]
    for name in ("open", "close", "high", "low"):
        frame["raw_" + name] = raw[name]
    frame["raw_prev"] = raw.close.shift()
    frame["ret1"] = returns
    for width in (5, 10, 20, 60, 120):
        frame[f"mom{width}"] = close / close.shift(width) - 1
    for width in (20, 60):
        frame[f"trend{width}"] = close / close.rolling(width).mean() - 1
        frame[f"vol{width}"] = returns.rolling(width).std(ddof=1) * np.sqrt(252)
    frame["downvol60"] = returns.clip(upper=0).pow(2).rolling(60).mean().pow(0.5) * np.sqrt(252)
    ranges = pd.concat(
        [
            adjusted.high - adjusted.low,
            (adjusted.high - close.shift()).abs(),
            (adjusted.low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_pct"] = ranges.rolling(14).mean() / close
    delta = close - close.shift()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    frame["rsi14"] = 100 - 100 / (1 + up / down.replace(0, np.nan))
    frame.loc[(down == 0) & (up > 0), "rsi14"] = 100
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    frame["macd_scaled"] = (2 * (macd - macd.ewm(span=9, adjust=False).mean())) / close
    frame["distance_high20"] = close / adjusted.high.shift().rolling(20).max() - 1
    frame["distance_high60"] = close / adjusted.high.rolling(60).max() - 1
    frame["dd60"] = close.rolling(60).apply(
        lambda values: np.min(values / np.maximum.accumulate(values) - 1), raw=True
    )
    volume = raw.volume_lots / splits
    frame["volume_ratio"] = volume / volume.shift().rolling(20).mean()
    frame["turnover20"] = raw.turnover.rolling(20).mean() / 100
    frame["adv20"] = raw.amount.rolling(20).mean()
    frame["beta60"] = returns.rolling(60).cov(benchmark) / benchmark.rolling(60).var()
    frame["corr60"] = returns.rolling(60).corr(benchmark)
    frame["relative20"] = (1 + frame.mom20) / (
        1 + benchmark.add(1).rolling(20).apply(np.prod, raw=True).sub(1)
    ) - 1
    frame["var1_95"] = returns.rolling(120).quantile(0.05)
    frame["es1_95"] = returns.rolling(120).apply(
        lambda values: np.mean(values[values <= np.quantile(values, 0.05)]), raw=True
    )
    frame["gap"] = gap
    frame["price_ratio"] = close / raw.close
    frame["age"] = np.arange(1, len(raw) + 1)
    frame["rising_days20"] = (returns > 0).rolling(20).mean()
    frame["log_adv20"] = np.log1p(frame.adv20)
    frame["amount_ratio"] = raw.amount / frame.adv20
    frame["range_pct"] = (raw.high - raw.low) / raw.close.shift()
    frame["close_location"] = (raw.close - raw.low) / (raw.high - raw.low).replace(0, np.nan)
    return frame


def percentiles(values):
    """Average tie positions from sorted observations, without pandas rank."""
    result = np.full(values.shape, np.nan, dtype="float64")
    usable = ~np.isnan(values)
    ordered = np.sort(values[usable])
    if len(ordered):
        low = np.searchsorted(ordered, values[usable], side="left")
        high = np.searchsorted(ordered, values[usable], side="right")
        result[usable] = (low + 1 + high) / 2 / len(ordered)
    return result


def table_rank(series, table, centered=True):
    values = series.where(table.eligible).to_numpy()
    result = np.full(len(table), np.nan)
    for positions in table.groupby("date_index", sort=False).indices.values():
        result[positions] = percentiles(values[positions]) - (0.5 if centered else 0)
    return result.astype("float32")


def rebuild(parent, actions, symbols):
    grouped = defaultdict(list)
    for event in actions:
        grouped[event["symbol"]].append(event)
    table = parent["table"].copy(deep=True)
    panels = {name: frame.copy(deep=True) for name, frame in parent["panels"].items()}
    base_names = [name[5:] for name in table if name.startswith("rank_") and name[5:] in table]
    require(len(base_names) == 30, "Base feature inventory differs")
    benchmark = parent["panels"]["close"]["sh510300"].pct_change(fill_method=None)
    for symbol in symbols:
        frame = trailing_features(parent["raws"][symbol], grouped[symbol], benchmark).reindex(
            parent["calendar"]
        )
        positions = np.flatnonzero(table.symbol.eq(symbol))
        table.loc[positions, base_names] = (
            frame[base_names].replace([np.inf, -np.inf], np.nan).astype("float32").to_numpy()
        )
        for name in panels.keys() - {"eligible"}:
            panels[name][symbol] = frame[name]
    eligible = panels["eligible"]

    def panel_rank(frame):
        data = frame.where(eligible).to_numpy()
        return pd.DataFrame(
            np.array([percentiles(row) for row in data]), index=frame.index, columns=frame.columns
        )

    scores = {
        "momentum": (
            0.35 * panel_rank(panels["mom20"])
            + 0.20 * panel_rank(panels["mom5"])
            + 0.20 * panel_rank(panels["distance_high20"])
            + 0.10 * panel_rank(-abs(np.log(panels["volume_ratio"].clip(lower=0.01) / 1.5)))
            + 0.15 * panel_rank(-panels["vol20"])
        )
        * 100,
        "lowvol": (0.7 * panel_rank(-panels["vol60"]) + 0.3 * panel_rank(panels["mom60"])) * 100,
        "reversal": (0.6 * panel_rank(-panels["mom5"]) + 0.4 * panel_rank(panels["mom60"])) * 100,
    }
    ordered_symbols = list(table.symbol.cat.categories)
    indices = table.symbol.cat.codes.to_numpy()
    dates = table.date_index.to_numpy()
    for name, score in scores.items():
        table[name + "_score"] = (score[ordered_symbols].to_numpy()[dates, indices] / 100).astype(
            "float32"
        )
    breadth = ((panels["trend20"] > 0) & eligible).sum(axis=1) / eligible.sum(axis=1).replace(
        0, np.nan
    )
    table["breadth"] = breadth.to_numpy()[dates].astype("float32")
    for name in base_names:
        table["rank_" + name] = table_rank(table[name], table)
    epsilon = 1e-8
    interactions = {
        "momentum20_ex5": (1 + table.mom20) / (1 + table.mom5) - 1,
        "momentum60_ex20": (1 + table.mom60) / (1 + table.mom20) - 1,
        "short_acceleration": table.mom5 - table.mom20 / 4,
        "vol_ratio": table.vol20 / (table.vol60 + epsilon),
        "momentum_atr": table.mom20 / (table.atr_pct + epsilon),
        "downside_share": table.downvol60 / (table.vol60 + epsilon),
        "rebound_risk": table.mom5 / (table.dd60.abs() + table.atr_pct + epsilon),
        "volume_confirmation": table.mom5 * np.log1p(table.amount_ratio.clip(0, 10)),
    }
    for name, values in interactions.items():
        table["rank_" + name] = table_rank(values.replace([np.inf, -np.inf], np.nan), table)
    table["momentum_percentile"] = table_rank(table.momentum_score, table, centered=False)
    industry_columns = [name for name in table if name.startswith("industry_")]
    require(len(industry_columns) == 11, "Industry feature inventory differs")
    industry_groups = table.groupby([table.date_index, table.industry], sort=False).indices
    for name in industry_columns:
        values = table[name[9:]].where(table.eligible).to_numpy()
        result = np.full(len(table), np.nan)
        for positions in industry_groups.values():
            subset = values[positions]
            count = np.count_nonzero(~np.isnan(subset))
            ranks = percentiles(subset)
            result[positions] = (
                0 if count < 3 else (ranks - (count + 1) / (2 * count)) * count / (count + 5)
            )
        result[~table.eligible.to_numpy()] = np.nan
        table[name] = result.astype("float32")
    return table, panels, scores


def fingerprint(frame):
    result = {}
    for name in frame:
        series = frame[name]
        if not pd.api.types.is_numeric_dtype(series):
            continue
        values = series.to_numpy(copy=True)
        if values.dtype.kind == "f":
            values[np.isnan(values)] = np.nan
            values[values == 0] = 0
        result[str(name)] = {
            "sha256": array_sha(values),
            "dtype": str(values.dtype),
            "missing": int(series.isna().sum()),
        }
    return result


pd.set_option("compute.use_numexpr", False)
root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
checks = root / p["checks_output"]
require(not (root / p["training_output"]).exists(), "Training started before feature acceptance")
parent = load_bundle(paths)
require(sha(paths.bundle) == p["source_bundle_sha256"], "Parent snapshot changed")
actions = read(checks / "expected_actions.json")
changes = read(checks / "expected_action_changes.json")
symbols = sorted({row["symbol"] for row in changes if "cash_per_share" in row["fields"]})
require(symbols == ["sh600989", "sz300750"], "Unexpected cash-price dependency scope")
old_table, old_panels, old_scores = rebuild(parent, parent["actions"], symbols)
pd.testing.assert_frame_equal(old_table, parent["table"], check_exact=True)
for section, actual in (("panels", old_panels), ("scores", old_scores)):
    for name, frame in actual.items():
        pd.testing.assert_frame_equal(frame, parent[section][name], check_exact=True)
print("INDEPENDENT_PARENT_FEATURES_EXACT", len(old_table), flush=True)
table, panels, scores = rebuild(parent, actions, symbols)
labels = {f"{name}{horizon}" for name in ("y", "end", "status", "delay") for horizon in (1, 3, 5)}
columns = [name for name in table if name not in labels]
counts = {
    name: int(
        (
            ~(
                table[name].eq(parent["table"][name])
                | (table[name].isna() & parent["table"][name].isna())
            )
        ).sum()
    )
    for name in columns
}
boundary = int(
    parent["calendar"].searchsorted(
        min(row["ex_date"] for row in changes if "cash_per_share" in row["fields"])
    )
)
pd.testing.assert_frame_equal(
    parent["table"].loc[parent["table"].date_index < boundary, columns],
    table.loc[table.date_index < boundary, columns],
    check_exact=True,
)
save(
    checks / "feature_expectations.json",
    {
        "status": "EXPECTED_FEATURES_FROZEN",
        "created_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "parent_bundle_sha256": sha(paths.bundle),
        "actions_sha256": digest(actions),
        "price_path_symbols": symbols,
        "parent_full_non_label_rebuild_exact": True,
        "non_label_prefix_boundary": boundary,
        "expected_changed_cells": {name: count for name, count in counts.items() if count},
        "table": fingerprint(table[columns]),
        "panels": {name: fingerprint(frame) for name, frame in panels.items()},
        "scores": {name: fingerprint(frame) for name, frame in scores.items()},
        "numeric_identity": "Exact dtype and numerical values with NaNs and signed zeros canonicalized; no numerical tolerance.",
        "method": "Independent raw-bar entitlement accumulation and feature assembly, standard pandas rolling/EWM kernels, and sorted-order average ties for every market/industry rank. No production feature, rank, price-index or dataset builder is called. Expected old labels are retained only as placeholders and are excluded from feature fingerprints.",
        "training_started": False,
        "new_snapshot_reconciled": False,
    },
    immutable=True,
)
print(
    "INDEPENDENT_FEATURE_EXPECTATIONS_FROZEN",
    {name: count for name, count in counts.items() if count},
    flush=True,
)
