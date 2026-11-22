"""Same-session information arriving after the open must not select opening fills."""

from copy import deepcopy

import pytest

from quantfolio.ledger import Ledger, buy_quantity
from quantfolio.market import opening_price
from quantfolio.simulation import simulate
from quantfolio.validation import execution_audit, reconcile
from tests.factories import market_bundle


@pytest.mark.parametrize(
    "fields,values",
    [
        (["high", "low"], [10.0, 10.0]),
        (["volume_lots", "amount"], [0.0, 0.0]),
        (["close", "high", "low"], [10.2, 10.2, 10.0]),
    ],
)
def test_later_bar_fields_do_not_select_opening_orders(fields, values):
    bundle, forecasts = market_bundle()
    _, expected = simulate(bundle, 6, 6, forecasts)
    changed = deepcopy(bundle)
    date = changed["calendar"][6]
    for raw in changed["raws"].values():
        raw.loc[date, fields] = values
    _, actual = simulate(changed, 6, 6, forecasts)
    assert expected["trades"], "The test must exercise actual opening purchases"
    for section in ("decisions", "trades", "rejections", "intermediate_weights"):
        assert actual[section] == expected[section], section


def test_later_sessions_do_not_change_earlier_opening_orders():
    bundle, forecasts = market_bundle()
    _, expected = simulate(bundle, 6, 6, forecasts)
    changed = deepcopy(bundle)
    for raw in changed["raws"].values():
        raw.iloc[7:] *= 2
    for panel in changed["panels"].values():
        if panel.dtypes.iloc[0].kind != "b":
            panel.iloc[7:] *= 3
    _, actual = simulate(changed, 6, 6, forecasts)
    assert actual == expected


def test_sell_fills_ignore_later_range_and_volume():
    bundle, forecasts = market_bundle()
    forecasts[5] = dict(list(forecasts[5].items())[:2])
    # At the next rebalance, no positive edge remains, so the account exits.
    for value in forecasts[10].values():
        value["mu"] = -0.1
    _, expected = simulate(bundle, 6, 11, forecasts)
    changed = deepcopy(bundle)
    date = changed["calendar"][11]
    for raw in changed["raws"].values():
        raw.loc[date, ["high", "low", "volume_lots", "amount"]] = [10, 10, 0, 0]
    _, actual = simulate(changed, 6, 11, forecasts)
    assert any(trade["side"] == "SELL" for trade in expected["trades"])
    for section in ("decisions", "trades", "rejections", "intermediate_weights"):
        assert actual[section] == expected[section]


@pytest.mark.parametrize("rate", [0.0, 0.001, 0.002, 0.003])
def test_slippage_is_charged_once_without_changing_flat_market_quote(rate):
    bundle, forecasts = market_bundle()
    forecasts[5] = dict(list(forecasts[5].items())[:2])
    for value in forecasts[10].values():
        value["mu"] = -0.1
    summary, detail = simulate(bundle, 6, 11, forecasts, slippage=rate)
    assert detail["trades"] and not detail["curve"][-1]["positions"]
    assert all(trade["price"] == 10.0 for trade in detail["trades"])
    assert summary["slippage_cost"] == pytest.approx(
        sum(round(trade["gross"] * rate, 2) for trade in detail["trades"])
    )
    assert summary["ending_cash"] == pytest.approx(
        1_000_000 - summary["fees"] - summary["slippage_cost"]
    )
    assert reconcile(detail, summary, bundle["meta"], [])["status"] == "PASS"
    assert execution_audit(detail, bundle)["status"] == "PASS"


def test_affordable_quantity_reserves_cost_and_ledger_rejects_overspend():
    bundle, _ = market_bundle()
    symbol = "sh600001"
    date = bundle["calendar"][1]
    quantity = buy_quantity(1005.31, 10, bundle["meta"][symbol], slippage=0.001)
    assert quantity == 0  # 100 shares cost 1006.01 including minimum commission.
    book = Ledger(bundle["meta"], initial=1005.31)
    assert not book.buy(date, symbol, 100, 10, slippage=0.001)
    assert book.cash == 100531 and not book.trades


@pytest.mark.parametrize(
    "status,reason",
    [
        ({"known_at": "2026-01-13T09:20:00+08:00", "suspended": True}, "SUSPENDED_AT_OPEN"),
        (
            {"known_at": "2026-01-13T15:00:00+08:00", "suspended": False},
            "STATUS_NOT_AVAILABLE_AT_OPEN",
        ),
        ({"suspended": False}, "STATUS_NOT_AVAILABLE_AT_OPEN"),
    ],
)
def test_status_requires_evidence_known_by_open(status, reason):
    bundle, _ = market_bundle()
    symbol = "sh600001"
    assert opening_price(
        bundle["meta"][symbol],
        bundle["raws"][symbol],
        bundle["calendar"][6],
        bundle["calendar"][5],
        status=status,
    ) == (None, reason)


@pytest.mark.parametrize("side,opening", [("BUY", 11.0), ("SELL", 9.0)])
def test_directional_limit_opening_is_still_rejected(side, opening):
    bundle, _ = market_bundle()
    symbol = "sh600001"
    raw = bundle["raws"][symbol]
    date, previous = bundle["calendar"][[6, 5]]
    raw.at[date, "open"] = opening
    price, reason = opening_price(bundle["meta"][symbol], raw, date, previous, side=side)
    assert price is None and "LIMIT" in reason


@pytest.mark.parametrize(
    "field,value,reason",
    [("price", 10.01, "NOT_OPENING_PROXY"), ("slippage_cost", 0.0, "SLIPPAGE_COST_MISMATCH")],
)
def test_independent_audit_rejects_corrupt_fill_or_missing_cost(field, value, reason):
    bundle, forecasts = market_bundle()
    summary, detail = simulate(bundle, 6, 6, forecasts)
    detail["trades"][0][field] = value
    assert any(error[0] == reason for error in execution_audit(detail, bundle)["errors"])
    assert reconcile(detail, summary, bundle["meta"], [])["status"] == "FAIL"
