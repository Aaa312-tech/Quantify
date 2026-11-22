"""Causal cross-sectional ranks and historical rank-to-return calibration."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

BUCKETS = np.array([0.0, 0.5, 0.8, 0.9, 0.95, 1.0])
CENTERS = (BUCKETS[1:] + BUCKETS[:-1]) / 2


def rank_by_date(table, rows, values):
    """Rank values within each signal-date cross-section, preserving input row order."""
    frame = pd.DataFrame({"date": table.iloc[rows].date_index.to_numpy(), "x": np.asarray(values)})
    return frame.groupby("date", sort=False).x.rank(pct=True).to_numpy()


@pd.option_context("compute.use_numexpr", False)
def calibrate(table, rows, score, available_before, clip):
    """Map historical ranks to returns using only labels available before the boundary."""
    history = table.iloc[rows][["date_index", "y5", "end5", "status5"]].copy()
    history["score"] = score
    history = history[
        (history.status5 == 0) & (history.end5 < available_before) & np.isfinite(history.y5)
    ]
    history["target"] = history.y5.clip(*clip["gross_clip"])
    history["bucket"] = np.minimum(4, np.searchsorted(BUCKETS[1:], history.score, side="left"))
    daily_returns = history.groupby("date_index", observed=True).target.mean()
    overall_mean = float(daily_returns.mean()) if len(daily_returns) else 0.0
    means = []
    standard_errors = []
    day_counts = []
    origin = int(history.date_index.min()) if len(history) else 0
    for bucket in range(5):
        bucket_returns = (
            history[history.bucket == bucket].groupby("date_index", observed=True).target.mean()
        )
        if len(bucket_returns) < 20:
            means.append(-0.05)
            standard_errors.append(0.10)
            day_counts.append(max(1, len(bucket_returns)))
            continue
        blocks = bucket_returns.groupby((bucket_returns.index - origin) // 5).mean()
        standard_error = (
            float(blocks.std(ddof=1) / np.sqrt(len(blocks))) if len(blocks) > 1 else 0.10
        )
        means.append(0.8 * float(bucket_returns.mean()) + 0.2 * overall_mean)
        standard_errors.append(max(0.0, standard_error))
        day_counts.append(len(bucket_returns))
    calibrated_means = IsotonicRegression(increasing=True, out_of_bounds="clip").fit_transform(
        CENTERS, means, sample_weight=day_counts
    )
    return {
        "centers": CENTERS.tolist(),
        "mean": calibrated_means.tolist(),
        "standard_error": standard_errors,
        "days": day_counts,
        "gross_clip": clip["gross_clip"],
        "available_before": int(available_before),
        "source_rows": len(history),
        "last_label_end": int(history.end5.max()) if len(history) else None,
        "status": "RANK_TO_RETURN_ESTIMATE_NOT_PROBABILITY",
    }


def map_calibration(score, cal):
    """Apply a frozen rank calibration and its uncertainty margin to new ranks."""
    return np.interp(score, cal["centers"], cal["mean"]), 0.5 * np.interp(
        score, cal["centers"], cal["standard_error"]
    )
