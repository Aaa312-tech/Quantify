"""Date evidence can restore named censored outcomes, never arbitrary table cells."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from quantfolio.snapshots import restored_label_table


def example():
    table = pd.DataFrame(
        {
            "date_index": np.arange(10, dtype="int16"),
            "symbol": ["sh688001"] * 10,
            "eligible": [True] * 10,
            "score": np.ones(10, dtype="float32"),
        }
    )
    for horizon in (1, 3, 5):
        table[f"y{horizon}"] = np.full(10, np.nan, dtype="float32")
        table[f"end{horizon}"] = np.full(10, -1, dtype="int16")
        table[f"status{horizon}"] = np.full(10, 4, dtype="uint8")
        table[f"delay{horizon}"] = np.zeros(10, dtype="int16")
    record = {
        "row": 0,
        "symbol": "sh688001",
        "date_index": 0,
        "horizon": 3,
        "before": [None, -1, 4, 0],
        "after": [0.125, 4, 0, 0],
        "independent_before": [None, -1, 4, 0],
        "independent_after": [0.125, 4, 0, 0],
    }
    return table, record


def test_restores_only_the_reviewed_label_and_preserves_storage():
    table, record = example()
    original = table.copy(deep=True)
    result = restored_label_table(table, [record])
    expected = original.copy(deep=True)
    expected.loc[0, ["y3", "end3", "status3", "delay3"]] = [0.125, 4, 0, 0]
    pd.testing.assert_frame_equal(result, expected, check_exact=True)
    pd.testing.assert_frame_equal(table, original, check_exact=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"row": -1},
        {"row": 10},
        {"row": True},
        {"horizon": 2},
        {"horizon": True},
        {"symbol": "sh688002"},
        {"date_index": 1},
        {"before": [None, -1, 0, 0]},
        {"independent_after": [0.5, 4, 0, 0]},
    ],
)
def test_rejects_invalid_or_different_reviewed_identity(changes):
    table, record = example()
    record.update(changes)
    with pytest.raises(ValueError):
        restored_label_table(table, [record])


@pytest.mark.parametrize(
    "after",
    [
        [float("nan"), 4, 0, 0],
        [-1.1, 4, 0, 0],
        [0.125, 3, 0, 0],
        [0.125, 10, 0, 6],
        [0.125, 4, 4, 0],
        [0.125, 5, 0, 0],
    ],
)
def test_rejects_invalid_exit_or_value(after):
    table, record = example()
    record["after"] = record["independent_after"] = after
    with pytest.raises(ValueError):
        restored_label_table(table, [record])


def test_rejects_repeated_or_already_known_labels():
    table, record = example()
    with pytest.raises(ValueError):
        restored_label_table(table, [record, deepcopy(record)])
    table.loc[0, ["y3", "end3", "status3"]] = [0.125, 4, 0]
    with pytest.raises(ValueError):
        restored_label_table(table, [record])
