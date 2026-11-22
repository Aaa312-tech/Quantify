"""Price, momentum and industry features."""

from typing import cast

import numpy as np
import pandas as pd


@pd.option_context("compute.use_numexpr", False)
def price_features(raw, adj, bench_returns):
    """Compute trailing price and liquidity features from causal adjusted bars."""
    features = pd.DataFrame(index=adj.index)
    close = adj.close
    returns = close.pct_change(fill_method=None)
    features["raw_close"] = raw.close
    features["close"] = close
    features["open"] = adj.open
    features["raw_open"] = raw.open
    features["raw_high"] = raw.high
    features["raw_low"] = raw.low
    features["ret1"] = returns
    for window in [5, 10, 20, 60, 120]:
        features[f"mom{window}"] = close.pct_change(window, fill_method=None)
    for window in [5, 10, 20, 60]:
        features[f"ma{window}"] = close.rolling(window).mean()
    features["trend20"] = close / features.ma20 - 1
    features["trend60"] = close / features.ma60 - 1
    features["vol20"] = returns.rolling(20).std(ddof=1) * np.sqrt(252)
    features["vol60"] = returns.rolling(60).std(ddof=1) * np.sqrt(252)
    features["downvol60"] = returns.clip(upper=0).pow(2).rolling(60).mean().pow(0.5) * np.sqrt(252)
    true_range = pd.concat(
        [adj.high - adj.low, (adj.high - close.shift()).abs(), (adj.low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    features["atr14"] = true_range.rolling(14).mean()
    features["atr_pct"] = features.atr14 / close
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    features["rsi14"] = 100 - 100 / (1 + up / down.replace(0, np.nan))
    features.loc[(down == 0) & (up > 0), "rsi14"] = 100
    features["macd_dif"] = (
        close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    )
    features["macd_dea"] = features.macd_dif.ewm(span=9, adjust=False).mean()
    features["macd_hist"] = 2 * (features.macd_dif - features.macd_dea)
    features["distance_high20"] = close / adj.high.shift(1).rolling(20).max() - 1
    features["distance_high60"] = close / adj.high.rolling(60).max() - 1
    features["dd60"] = close.rolling(60).apply(
        lambda x: np.min(x / np.maximum.accumulate(x) - 1), raw=True
    )
    features["adv20"] = raw.amount.rolling(20).mean()
    features["amount"] = raw.amount
    features["volume_ratio"] = raw.volume_lots / raw.volume_lots.shift(1).rolling(20).mean()
    features["turnover20"] = raw.turnover.rolling(20).mean() / 100
    features["beta60"] = returns.rolling(60).cov(bench_returns) / bench_returns.rolling(60).var()
    features["corr60"] = returns.rolling(60).corr(bench_returns)
    features["relative20"] = (1 + features.mom20) / (
        1 + bench_returns.add(1).rolling(20).apply(np.prod, raw=True).sub(1)
    ) - 1
    features["var1_95"] = returns.rolling(120).quantile(0.05)
    features["es1_95"] = returns.rolling(120).apply(
        lambda x: np.mean(x[x <= np.quantile(x, 0.05)]), raw=True
    )
    features["gap"] = adj.opening_gap
    features["price_ratio"] = adj.close / raw.close
    features["raw_prev"] = raw.close.shift()
    features["age"] = np.arange(1, len(features) + 1)
    features["rising_days20"] = (returns > 0).rolling(20).mean()
    return features


BASE_FEATURES = [
    "ret1",
    "mom5",
    "mom10",
    "mom20",
    "mom60",
    "mom120",
    "trend20",
    "trend60",
    "vol20",
    "vol60",
    "downvol60",
    "atr_pct",
    "rsi14",
    "distance_high20",
    "distance_high60",
    "dd60",
    "volume_ratio",
    "turnover20",
    "beta60",
    "corr60",
    "relative20",
    "var1_95",
    "es1_95",
    "gap",
    "rising_days20",
]


@pd.option_context("compute.use_numexpr", False)
def base_features(raw, prices):
    """Select model inputs and replace infinities with missing values."""
    features = prices[BASE_FEATURES].copy()
    features["log_adv20"] = np.log1p(prices.adv20)
    features["amount_ratio"] = raw.amount / prices.adv20
    features["range_pct"] = (raw.high - raw.low) / raw.close.shift()
    features["close_location"] = (raw.close - raw.low) / (raw.high - raw.low).replace(0, np.nan)
    features["macd_scaled"] = prices.macd_hist / prices.close
    return features.replace([np.inf, -np.inf], np.nan)


@pd.option_context("compute.use_numexpr", False)
def cross_ranks(table, columns):
    """Rank eligible base features within each completed signal date."""
    values = table[columns].where(table.eligible, axis=0)
    result = values.groupby(table.date_index, sort=False).rank(pct=True) - 0.5
    result.columns = ["rank_" + c for c in columns]
    return result


@pd.option_context("compute.use_numexpr", False)
def momentum_features(table):
    """Build trailing momentum interactions and their same-date cross-sectional ranks."""
    eps = 1e-8
    extra = pd.DataFrame(index=table.index)
    extra["momentum20_ex5"] = (1 + table.mom20) / (1 + table.mom5) - 1
    extra["momentum60_ex20"] = (1 + table.mom60) / (1 + table.mom20) - 1
    extra["short_acceleration"] = table.mom5 - table.mom20 / 4
    extra["vol_ratio"] = table.vol20 / (table.vol60 + eps)
    extra["momentum_atr"] = table.mom20 / (table.atr_pct + eps)
    extra["downside_share"] = table.downvol60 / (table.vol60 + eps)
    extra["rebound_risk"] = table.mom5 / (table.dd60.abs() + table.atr_pct + eps)
    extra["volume_confirmation"] = table.mom5 * np.log1p(table.amount_ratio.clip(0, 10))
    extra = extra.replace([np.inf, -np.inf], np.nan)
    ranked = (
        extra.where(table.eligible, axis=0).groupby(table.date_index, sort=False).rank(pct=True)
        - 0.5
    )
    ranked.columns = ["rank_" + cast(str, c) for c in extra]
    ranked["momentum_percentile"] = (
        table.momentum_score.where(table.eligible)
        .groupby(table.date_index, sort=False)
        .rank(pct=True)
    )
    return ranked.astype("float32")


INDUSTRY_FEATURES = [
    "ret1",
    "mom5",
    "mom20",
    "mom60",
    "trend20",
    "vol20",
    "atr_pct",
    "amount_ratio",
    "distance_high20",
    "rsi14",
    "beta60",
]


@pd.option_context("compute.use_numexpr", False)
def industry_features(table, meta, calendar=None):
    """Compute same-date industry ranks with small-group shrinkage and eligibility masking."""
    sector = table.symbol.map({s: m["industry"] for s, m in meta.items()})
    if any(
        "industry_history" in instrument or "fund_history" in instrument
        for instrument in meta.values()
    ):
        from .industries import industry_series

        if calendar is None:
            raise ValueError("Dated industry features require the signal calendar")
        if len(table) and (table.date_index.min() < 0 or table.date_index.max() >= len(calendar)):
            raise ValueError("Industry feature dates are outside the signal calendar")
        histories = {s: industry_series(m, calendar).to_numpy() for s, m in meta.items()}
        sector = pd.Series(
            [histories[s][i] for i, s in zip(table.date_index, table.symbol)],
            index=table.index,
            dtype=object,
        )
    values = table[INDUSTRY_FEATURES].where(table.eligible, axis=0)
    group = values.groupby([table.date_index, sector], sort=False)
    counts = group.transform("count")
    ranks = group.rank(pct=True)
    # Center finite-sample ranks, then shrink noisy small-industry estimates.
    ranked = (ranks - (counts + 1) / (2 * counts)) * counts / (counts + 5)
    ranked = ranked.where(counts >= 3, 0).where(table.eligible, axis=0)
    ranked.columns = ["industry_" + c for c in INDUSTRY_FEATURES]
    return ranked.astype("float32"), sector
