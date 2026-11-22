"""The raw-data builder must preserve historical signals when future bars arrive."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from quantfolio.preprocessing import build_dataset, compare_history
from tests.factories import raw_history


def test_future_bars_and_actions_preserve_all_historical_signals():
    meta, raws = raw_history()
    original = deepcopy(raws)
    prefix = {s: r.iloc[:-1].copy() for s, r in raws.items()}
    before = build_dataset(meta, prefix, [])
    dates = raws["sh510300"].index
    action = {
        "symbol": "sh600001",
        "record_date": str(dates[-2].date()),
        "ex_date": str(dates[-1].date()),
        "cash_per_share": 0.2,
        "share_multiplier": 1.0,
    }
    after = build_dataset(meta, raws, [action])
    assert compare_history(before, after, labels=False)["status"] == "PASS"
    assert len(after["features"]) == 57
    for symbol in raws:
        pd.testing.assert_frame_equal(raws[symbol], original[symbol], check_exact=True)
    # The prior signal's outcome becomes observable only after another session arrives.
    old = before["table"].query("symbol == 'sh600001' and date_index == 142").iloc[0]
    new = after["table"].query("symbol == 'sh600001' and date_index == 142").iloc[0]
    assert old.status1 != 0 and new.status1 == 0
    assert new.end1 == 144
    assert after["table"].date_index.dtype == np.dtype("int16")
    assert after["table"].status1.dtype == np.dtype("uint8")


def test_eligibility_requires_history_liquidity_and_clear_security_status():
    meta, raws = raw_history()
    meta["sh600001"]["name"] = "*ST Example"
    raws["sz300001"].loc[:, "amount"] = np.nan
    result = build_dataset(meta, raws, [], quarantined=["sh600002"])
    eligible = result["panels"]["eligible"]
    assert not eligible.iloc[:125].any().any()
    assert eligible["sh510300"].iloc[125:].all()
    assert not eligible.drop(columns="sh510300").any().any()
    assert result["scores"]["momentum"].drop(columns="sh510300").isna().all().all()
    assert result["scores"]["momentum"]["sh510300"].iloc[-1] == 100
    for symbol, raw in raws.items():
        pd.testing.assert_frame_equal(raw, result["raws"][symbol])


@pytest.mark.parametrize("fault", ["duplicate", "outside_calendar", "missing_benchmark"])
def test_invalid_raw_calendar_is_rejected(fault):
    meta, raws = raw_history()
    if fault == "duplicate":
        raws["sh600001"] = pd.concat([raws["sh600001"], raws["sh600001"].iloc[-1:]])
    elif fault == "outside_calendar":
        raws["sh600001"].index = raws["sh600001"].index + pd.Timedelta(days=1)
    else:
        del raws["sh510300"]
    with pytest.raises(ValueError):
        build_dataset(meta, raws, [])
