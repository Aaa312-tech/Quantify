"""Convert stored rank arrays to signal-date prediction maps."""

from __future__ import annotations


def prediction_map(table, a, name):
    """Translate stored outer prediction arrays into per-date instrument forecasts."""
    out = {}
    sub = table.iloc[a["row_indices"]]
    for d, s, r, m, e in zip(
        sub.date_index, sub.symbol, a[name + "_rank"], a["common_mu"], a["common_margin"]
    ):
        out.setdefault(int(d), {})[str(s)] = {"rank": float(r), "mu": float(m), "margin": float(e)}
    return out
