"""A sourced suspension is distinct from an unexplained missing price."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from quantfolio.ledger import Ledger
from quantfolio.market import legal_label, opening_price
from quantfolio.preprocessing import build_dataset, compare_history
from quantfolio.simulation import _Execution, buy_ceiling
from quantfolio.snapshots import rebuild_labels
from quantfolio.validation import execution_audit, opening_errors
from tests.factories import market_bundle, raw_history


def suspended_bundle():
    bundle, _ = market_bundle()
    symbol = "sh600001"
    dates = bundle["calendar"]
    raw = bundle["raws"][symbol].drop(dates[7:12])
    raw.loc[dates[12], ["open", "close"]] = 11.0
    raw.loc[dates[13], ["open", "close"]] = 12.1
    bundle["raws"][symbol] = raw
    history = {}
    for day in dates[7:12]:
        history[str(day.date())] = {
            symbol: {"known_at": str(dates[6].date()) + "T22:00:00+08:00", "suspended": True}
        }
    history[str(dates[12].date())] = {
        symbol: {
            "known_at": str(dates[11].date()) + "T22:00:00+08:00",
            "suspended": False,
            "reference_date": str(dates[6].date()),
            "suspension_evidence": [
                {"date": day, "known_at": row[symbol]["known_at"]} for day, row in history.items()
            ],
        }
    }
    bundle["opening_status"] = history
    return bundle, symbol


def quote(bundle, symbol, side="SELL"):
    dates = bundle["calendar"]
    return opening_price(
        bundle["meta"][symbol],
        bundle["raws"][symbol],
        dates[12],
        dates[11],
        bundle["actions"],
        side,
        status=bundle["opening_status"][str(dates[12].date())][symbol],
        calendar=dates,
    )


def test_reviewed_resumption_ends_label_at_first_legal_sell():
    bundle, symbol = suspended_bundle()
    dates = bundle["calendar"]
    assert quote(bundle, symbol) == (11.0, None)
    value, end, status, delay = legal_label(
        bundle["meta"][symbol],
        bundle["raws"][symbol],
        dates,
        5,
        1,
        [],
        opening_status=bundle["opening_status"],
    )
    assert (end, status, delay) == (12, 0, 5)
    assert value == pytest.approx(0.1)
    trade = {"symbol": symbol, "date": str(dates[12].date()), "side": "SELL", "price": 11.0}
    assert opening_errors(trade, bundle) == []
    assert quote(bundle, symbol, "BUY") == (None, "NO_QUOTE")
    assert buy_ceiling(bundle["raws"][symbol], dates[11], dates[12], [], 0.03) is None


@pytest.mark.parametrize(
    "defect",
    ["unknown", "incomplete", "late_halt", "late_resume", "stale_anchor", "intervening_action"],
)
def test_incomplete_or_unavailable_resumption_evidence_stays_blocked(defect):
    bundle, symbol = suspended_bundle()
    dates = bundle["calendar"]
    status = bundle["opening_status"][str(dates[12].date())][symbol]
    if defect == "unknown":
        status.pop("reference_date")
    elif defect == "incomplete":
        status["suspension_evidence"].pop(0)
    elif defect == "late_halt":
        status["suspension_evidence"][0]["known_at"] = str(dates[7].date()) + "T15:00:00+08:00"
    elif defect == "late_resume":
        status["known_at"] = str(dates[12].date()) + "T15:00:00+08:00"
    elif defect == "stale_anchor":
        status["reference_date"] = str(dates[5].date())
    else:
        bundle["actions"] = [{"symbol": symbol, "ex_date": str(dates[8].date())}]
    assert quote(bundle, symbol)[0] is None
    trade = {"symbol": symbol, "date": str(dates[12].date()), "side": "SELL", "price": 11.0}
    assert opening_errors(trade, bundle)


def test_known_resumption_does_not_invent_liquidity_or_ignore_lower_limit():
    bundle, symbol = suspended_bundle()
    dates = bundle["calendar"]
    bundle["panels"]["adv20"].at[dates[11], symbol] = np.nan
    book = Ledger(bundle["meta"])
    book.buy(dates[6], symbol, 100, 10.0)
    executor = _Execution(book, bundle, 12, {symbol: 10.0}, {symbol: []}, 0.001, 0.9, [])
    executor.sell({symbol: 0.0})
    assert book.quantity(symbol) == 100
    assert book.rejections[-1]["reason"] == "MISSING_LIQUIDITY"
    changed = deepcopy(bundle)
    changed["raws"][symbol].at[dates[12], "open"] = 9.0
    assert quote(changed, symbol) == (None, "OPEN_AT_LOWER_LIMIT")


def test_rebuilding_or_extending_data_preserves_status_aware_labels():
    meta, raws = raw_history()
    symbol = "sh600001"
    dates = raws[symbol].index
    raws[symbol].loc[:, ["open", "close", "high", "low"]] = 10.0
    raws[symbol] = raws[symbol].drop(dates[129:134])
    raws[symbol].loc[dates[134], ["open", "close"]] = 11.0
    raws[symbol].loc[dates[135] :, ["open", "close"]] = 12.1
    original = build_dataset(meta, raws, [])
    status = {
        str(day.date()): {
            symbol: {"suspended": True, "known_at": str(dates[128].date()) + "T22:00:00+08:00"}
        }
        for day in dates[129:134]
    }
    status[str(dates[134].date())] = {
        symbol: {
            "suspended": False,
            "known_at": str(dates[133].date()) + "T22:00:00+08:00",
            "reference_date": str(dates[128].date()),
            "suspension_evidence": [
                {"date": day, "known_at": item[symbol]["known_at"]} for day, item in status.items()
            ],
        }
    }
    result = build_dataset(meta, raws, [], opening_status=status)
    assert compare_history(original, result, labels=False)["status"] == "PASS"
    row = result["table"].query("symbol == 'sh600001' and date_index == 127").iloc[0]
    assert row.end1 == 134 and row.y1 == pytest.approx(0.1)
    assert result["opening_status"] == status
    pd.testing.assert_frame_equal(
        result["table"], rebuild_labels(result, {symbol}), check_exact=True
    )


@pytest.mark.parametrize("adv", [np.nan, np.inf, 0.0])
def test_independent_audit_rejects_fills_without_finite_prior_capacity(adv):
    from quantfolio.simulation import simulate

    bundle, forecasts = market_bundle()
    _, account = simulate(bundle, 6, 6, forecasts)
    assert account["trades"]
    bundle["panels"]["adv20"].iloc[5] = adv
    assert any(error[0] == "ADV_LIMIT" for error in execution_audit(account, bundle)["errors"])
