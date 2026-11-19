"""Walk-forward windows with actual label-end purging."""

from __future__ import annotations

import numpy as np


def walk_forward_folds(calendar, train=252, validation=63, test=21, gap=6, first=125):
    """Create chronological train, validation and test windows with purging gaps."""
    folds = []
    start = first
    while True:
        train_end = start + train - 1
        valid_start = train_end + gap + 1
        valid_end = valid_start + validation - 1
        test_start = valid_end + gap + 1
        test_end = test_start + test - 1
        if test_end + 6 >= len(calendar):
            break
        folds.append(
            {
                "fold": len(folds) + 1,
                "train_start": start,
                "train_end": train_end,
                "validation_start": valid_start,
                "validation_end": valid_end,
                "test_start": test_start,
                "test_end": test_end,
            }
        )
        start += test
    return folds


def label_mask(table, horizon, start, end, available_before):
    """Calendar grouping plus actual, possibly delayed, label-end purging."""
    dates = table.date_index.to_numpy()
    ends = table[f"end{horizon}"].to_numpy()
    return (
        (dates >= start)
        & (dates <= end)
        & table.eligible.to_numpy()
        & (table[f"status{horizon}"].to_numpy() == 0)
        & (ends < available_before)
        & (ends > dates)
        & np.isfinite(table[f"y{horizon}"].to_numpy())
    )
