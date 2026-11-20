"""Data repairs must change legal timing without changing entitlements or old inputs."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from test_market import META, bars

from quantfolio.dataset import label_mask
from quantfolio.ledger import Ledger
from quantfolio.market import gross_entitlement, opening_price
from quantfolio.simulation import buy_ceiling
from quantfolio.snapshots import next_opening, rebuild_labels, repair_actions
from quantfolio.validation import opening_errors


def test_shift_declared_payments_once_and_preserve_unknowns():
    calendar = pd.to_datetime(["2026-06-05", "2026-06-08", "2026-06-09"])
    actions = [
        {"cash_pay_date": "2026-06-08", "declared_date": "2026-06-05"},
        {"cash_pay_date": "2026-06-05"},
        {"cash_pay_date": None},
        {"cash_pay_date": "2026-06-09"},
    ]
    original = deepcopy(actions)
    updated, count = repair_actions(actions, calendar, [], "declared_date")
    assert count == 1
    assert updated[0]["cash_pay_date"] == "2026-06-05"
    assert [a["cash_available_date"] for a in updated] == ["2026-06-08", "2026-06-08", None, None]
    assert next_opening(calendar, "2026-06-06") == "2026-06-08"
    assert actions == original
    repeated, count = repair_actions(updated, calendar, [], "declared_date")
    assert count == 0
    assert repeated == updated


@pytest.mark.parametrize("available", ["2026-01-07", None])
def test_declared_payment_is_not_spendable_at_that_open(available):
    dates = pd.bdate_range("2026-01-05", periods=3)
    symbol = META["symbol"]
    action = {
        "id": "dividend",
        "symbol": symbol,
        "record_date": "2026-01-05",
        "ex_date": "2026-01-06",
        "cash_pay_date": "2026-01-06",
        "cash_available_date": available,
        "cash_per_share": 0.1,
        "share_multiplier": 1.0,
    }
    book = Ledger({symbol: META}, initial=1005.01)
    assert book.buy(dates[0], symbol, 100, 10.0)
    assert book.cash == 0
    book.snapshot_record_dates(dates[0], [action])
    book.apply_actions(dates[1], [action])
    assert book.cash == 0
    assert sum(r["cents"] for r in book.receivables) == 800
    assert not book.buy(dates[1], symbol, 100, 0.02)
    before = book.nav({symbol: 9.9})
    book.apply_actions(dates[2], [])
    assert book.nav({symbol: 9.9}) == before
    assert book.cash == (800 if available else 0)
    assert book.buy(dates[2], symbol, 100, 0.02) == bool(available)


def test_reference_multiplier_does_not_replace_share_entitlements():
    dates, raw = bars()
    meta = dict(META, code="688120", symbol="sh688120")
    raw.loc[dates[0], "close"] = 140.4
    raw.loc[dates[1], "open"] = 120.0
    action = {
        "symbol": meta["symbol"],
        "record_date": str(dates[0].date()),
        "ex_date": str(dates[1].date()),
        "share_list_date": str(dates[1].date()),
        "cash_per_share": 0.4,
        "share_multiplier": 1.4,
        "ex_reference_cash_per_share": 0.4,
        "ex_reference_share_multiplier": 1.39892,
    }
    assert opening_price(meta, raw, dates[1], dates[0], [action]) == (120.0, None)
    assert buy_ceiling(raw, dates[0], dates[1], [action], 0.03) == pytest.approx(
        140 / 1.39892 * 1.03
    )
    wealth, _, _ = gross_entitlement(dates[0], dates[1], 120.0, [action])
    assert wealth == pytest.approx(1.4 * 120 + 0.4)
    bundle = {
        "calendar": dates,
        "meta": {meta["symbol"]: meta},
        "raws": {meta["symbol"]: raw},
        "actions": [action],
    }
    trade = {"symbol": meta["symbol"], "date": str(dates[1].date()), "side": "BUY", "price": 120.0}
    assert opening_errors(trade, bundle) == []
    raw.loc[dates[1], "open"] = 120.09
    trade["price"] = 120.09
    assert opening_price(meta, raw, dates[1], dates[0], [action])[1] == "OPEN_AT_UPPER_LIMIT"
    assert "DIRECTIONAL_LIMIT_FILL" in opening_errors(trade, bundle)


def test_repaired_share_listing_regenerates_purged_labels():
    dates, raw = bars()
    action = {
        "symbol": META["symbol"],
        "record_date": str(dates[1].date()),
        "ex_date": str(dates[2].date()),
        "cash_per_share": 0.0,
        "share_multiplier": 2.0,
        "share_list_date": None,
    }
    raw.loc[dates[2] :, ["open", "close", "high", "low"]] = 5.0
    table = pd.DataFrame(
        {
            "date_index": np.arange(len(dates)),
            "symbol": META["symbol"],
            "eligible": True,
            "factor": np.arange(len(dates), dtype="float32"),
        }
    )
    table.loc[1, "eligible"] = False
    for horizon in (1, 3, 5):
        for field, value, dtype in (
            ("y", np.nan, "float32"),
            ("end", -1, "int16"),
            ("status", 1, "int8"),
            ("delay", 0, "int16"),
        ):
            table[f"{field}{horizon}"] = np.full(len(dates), value, dtype=dtype)
    bundle = {
        "table": table,
        "meta": {META["symbol"]: META},
        "raws": {META["symbol"]: raw},
        "calendar": dates,
        "actions": [action],
    }
    old = rebuild_labels(bundle)
    assert old.at[0, "status1"] == 4
    action["share_list_date"] = str(dates[4].date())
    updated = rebuild_labels(bundle)
    assert (updated.at[0, "status1"], updated.at[0, "end1"], updated.at[0, "delay1"]) == (0, 4, 2)
    assert not label_mask(updated, 1, 0, 0, 4)[0]
    assert label_mask(updated, 1, 0, 0, 5)[0]
    assert updated.at[1, "status1"] == 1
    pd.testing.assert_series_equal(table.factor, updated.factor)
    assert updated.dtypes.equals(table.dtypes)
    assert table.at[0, "status1"] == 1
