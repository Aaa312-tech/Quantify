"""Causal price features and cross-sectional transformations used by the dataset contract."""

import numpy as np
import pandas as pd


def price_features(raw, adj, bench_returns):
    """Compute trailing price and liquidity features from causal adjusted bars."""
    f = pd.DataFrame(index=adj.index)
    c = adj.close
    ret = c.pct_change(fill_method=None)
    f["raw_close"] = raw.close
    f["close"] = c
    f["open"] = adj.open
    f["raw_open"] = raw.open
    f["raw_high"] = raw.high
    f["raw_low"] = raw.low
    f["ret1"] = ret
    for n in [5, 10, 20, 60, 120]:
        f[f"mom{n}"] = c.pct_change(n, fill_method=None)
    for n in [5, 10, 20, 60]:
        f[f"ma{n}"] = c.rolling(n).mean()
    f["trend20"] = c / f.ma20 - 1
    f["trend60"] = c / f.ma60 - 1
    f["vol20"] = ret.rolling(20).std(ddof=1) * np.sqrt(252)
    f["vol60"] = ret.rolling(60).std(ddof=1) * np.sqrt(252)
    f["downvol60"] = ret.clip(upper=0).pow(2).rolling(60).mean().pow(0.5) * np.sqrt(252)
    tr = pd.concat(
        [adj.high - adj.low, (adj.high - c.shift()).abs(), (adj.low - c.shift()).abs()], axis=1
    ).max(axis=1)
    f["atr14"] = tr.rolling(14).mean()
    f["atr_pct"] = f.atr14 / c
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    f["rsi14"] = 100 - 100 / (1 + up / down.replace(0, np.nan))
    f.loc[(down == 0) & (up > 0), "rsi14"] = 100
    f["macd_dif"] = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f["macd_dea"] = f.macd_dif.ewm(span=9, adjust=False).mean()
    f["macd_hist"] = 2 * (f.macd_dif - f.macd_dea)
    f["distance_high20"] = c / adj.high.shift(1).rolling(20).max() - 1
    f["distance_high60"] = c / adj.high.rolling(60).max() - 1
    f["dd60"] = c.rolling(60).apply(lambda x: np.min(x / np.maximum.accumulate(x) - 1), raw=True)
    f["adv20"] = raw.amount.rolling(20).mean()
    f["amount"] = raw.amount
    f["volume_ratio"] = raw.volume_lots / raw.volume_lots.shift(1).rolling(20).mean()
    f["turnover20"] = raw.turnover.rolling(20).mean() / 100
    f["beta60"] = ret.rolling(60).cov(bench_returns) / bench_returns.rolling(60).var()
    f["corr60"] = ret.rolling(60).corr(bench_returns)
    f["relative20"] = (1 + f.mom20) / (
        1 + bench_returns.add(1).rolling(20).apply(np.prod, raw=True).sub(1)
    ) - 1
    f["var1_95"] = ret.rolling(120).quantile(0.05)
    f["es1_95"] = ret.rolling(120).apply(lambda x: np.mean(x[x <= np.quantile(x, 0.05)]), raw=True)
    f["gap"] = adj.open / c.shift() - 1
    f["price_ratio"] = adj.close / raw.close
    f["raw_prev"] = raw.close.shift()
    f["age"] = np.arange(1, len(f) + 1)
    f["rising_days20"] = (ret > 0).rolling(20).mean()
    return f


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


def feature_frame(raw, causal, splits, benchmark):
    """Build the base feature frame with split-normalized volume and finite values."""
    normalized = raw.copy()
    normalized["volume_lots"] = raw.volume_lots / splits
    f = price_features(normalized, causal, benchmark)
    x = f[BASE_FEATURES].copy()
    x["log_adv20"] = np.log1p(f.adv20)
    x["amount_ratio"] = raw.amount / f.adv20
    x["range_pct"] = (raw.high - raw.low) / raw.close.shift()
    x["close_location"] = (raw.close - raw.low) / (raw.high - raw.low).replace(0, np.nan)
    x["macd_scaled"] = f.macd_hist / f.close
    return x.replace([np.inf, -np.inf], np.nan)


def cross_ranks(table, columns):
    """Rank eligible base features within each completed signal date."""
    values = table[columns].where(table.eligible, axis=0)
    result = values.groupby(table.date_index, sort=False).rank(pct=True) - 0.5
    result.columns = ["rank_" + c for c in columns]
    return result


def momentum_features(table):
    """Build trailing momentum interactions and their same-date cross-sectional ranks."""
    t = table
    eps = 1e-8
    extra = pd.DataFrame(index=t.index)
    extra["momentum20_ex5"] = (1 + t.mom20) / (1 + t.mom5) - 1
    extra["momentum60_ex20"] = (1 + t.mom60) / (1 + t.mom20) - 1
    extra["short_acceleration"] = t.mom5 - t.mom20 / 4
    extra["vol_ratio"] = t.vol20 / (t.vol60 + eps)
    extra["momentum_atr"] = t.mom20 / (t.atr_pct + eps)
    extra["downside_share"] = t.downvol60 / (t.vol60 + eps)
    extra["rebound_risk"] = t.mom5 / (t.dd60.abs() + t.atr_pct + eps)
    extra["volume_confirmation"] = t.mom5 * np.log1p(t.amount_ratio.clip(0, 10))
    extra = extra.replace([np.inf, -np.inf], np.nan)
    ranked = extra.where(t.eligible, axis=0).groupby(t.date_index, sort=False).rank(pct=True) - 0.5
    ranked.columns = ["v4_rank_" + c for c in extra]
    ranked["momentum_percentile"] = (
        t.momentum_score.where(t.eligible).groupby(t.date_index, sort=False).rank(pct=True)
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


def industry_features(table, meta):
    """Compute same-date industry ranks with small-group shrinkage and eligibility masking."""
    sector = table.symbol.map({s: m["industry"] for s, m in meta.items()})
    values = table[INDUSTRY_FEATURES].where(table.eligible, axis=0)
    group = values.groupby([table.date_index, sector], sort=False)
    counts = group.transform("count")
    ranks = group.rank(pct=True)
    # Center finite-sample ranks, then shrink noisy small-industry estimates.
    ranked = (ranks - (counts + 1) / (2 * counts)) * counts / (counts + 5)
    ranked = ranked.where(counts >= 3, 0).where(table.eligible, axis=0)
    ranked.columns = ["industry_" + c for c in INDUSTRY_FEATURES]
    return ranked.astype("float32"), sector
