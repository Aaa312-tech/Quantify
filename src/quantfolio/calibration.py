"""Causal cross-sectional ranks and historical rank-to-return calibration."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

BUCKETS = np.array([0.0, 0.5, 0.8, 0.9, 0.95, 1.0])


CENTERS = (BUCKETS[1:] + BUCKETS[:-1]) / 2


def rank_by_date(table, rows, values):
    """Rank values within each signal-date cross-section, preserving input row order."""
    sub = pd.DataFrame({"date": table.iloc[rows].date_index.to_numpy(), "x": np.asarray(values)})
    return sub.groupby("date", sort=False).x.rank(pct=True).to_numpy()


def calibrate(table, rows, score, available_before, clip):
    """Map historical ranks to returns using only labels available before the boundary."""
    sub = table.iloc[rows][["date_index", "y5", "end5", "status5"]].copy()
    sub["score"] = score
    sub = sub[(sub.status5 == 0) & (sub.end5 < available_before) & np.isfinite(sub.y5)]
    sub["target"] = sub.y5.clip(*clip["gross_clip"])
    sub["bucket"] = np.minimum(4, np.searchsorted(BUCKETS[1:], sub.score, side="left"))
    daily = sub.groupby("date_index", observed=True).target.mean()
    grand = float(daily.mean()) if len(daily) else 0.0
    means = []
    errors = []
    counts = []
    origin = int(sub.date_index.min()) if len(sub) else 0
    for bucket in range(5):
        d = sub[sub.bucket == bucket].groupby("date_index", observed=True).target.mean()
        if len(d) < 20:
            means.append(-0.05)
            errors.append(0.10)
            counts.append(max(1, len(d)))
            continue
        blocks = d.groupby((d.index - origin) // 5).mean()
        se = float(blocks.std(ddof=1) / np.sqrt(len(blocks))) if len(blocks) > 1 else 0.10
        means.append(0.8 * float(d.mean()) + 0.2 * grand)
        errors.append(max(0.0, se))
        counts.append(len(d))
    iso = IsotonicRegression(increasing=True, out_of_bounds="clip").fit_transform(
        CENTERS, means, sample_weight=counts
    )
    return {
        "centers": CENTERS.tolist(),
        "mean": iso.tolist(),
        "standard_error": errors,
        "days": counts,
        "gross_clip": clip["gross_clip"],
        "available_before": int(available_before),
        "source_rows": len(sub),
        "last_label_end": int(sub.end5.max()) if len(sub) else None,
        "status": "RANK_TO_RETURN_ESTIMATE_NOT_PROBABILITY",
    }


def map_calibration(score, cal):
    """Apply a frozen rank calibration and its uncertainty margin to new ranks."""
    return np.interp(score, cal["centers"], cal["mean"]), 0.5 * np.interp(
        score, cal["centers"], cal["standard_error"]
    )
