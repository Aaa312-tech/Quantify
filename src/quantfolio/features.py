"""Same-date industry features; no future-row aggregation."""

from __future__ import annotations

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


def features(table, meta):
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
