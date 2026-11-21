"""Reproduce missing fund availability and cross-holding concentration checks."""

from copy import deepcopy

import pytest
from conftest import fixture
from test_planning import calendar_for, opening_quotes, prefix

from quantfolio.accounts import new_account
from quantfolio.industries import industry_at, metadata_at
from quantfolio.planning import make_intent, resolve_intent
from quantfolio.simulation import post_buy_limits, simulate


def profile(*, known=None, unmatched="0", undisclosed="0", residual="0"):
    return {
        "mode": "reported",
        "reported_nav_cny": "100",
        "matched_divisions": {key: {"amount_cny": value} for key, value in (known or {}).items()},
        "unmatched_named": {"amount_cny": unmatched},
        "undisclosed_stocks": {"amount_cny": undisclosed},
        "signed_equity_sector_residual": {"amount_cny": residual},
        "reported_index_contracts": [],
        "period_end": "2025-12-31",
        "source_report": "synthetic/source.pdf",
        "source_sha256": "0" * 64,
        "complete_economic_exposure_certified": False,
        "economic_exposure_upper_bound": None,
    }


def entry(day, value):
    return {
        "available_at": str(day.date()) + "T00:00:00+08:00",
        "industry": "benchmark:csi_300" if value is not None else None,
        "profile": value,
    }


def opening_change():
    bundle, forecasts = fixture()
    dates = bundle["calendar"]
    for symbol, instrument in bundle["meta"].items():
        if instrument["kind"] == "stock":
            instrument["industry"] = "capco:C39" if symbol[-1] in "12" else "capco:C" + symbol[-1] * 2
    bundle["meta"]["sh510300"]["fund_history"] = [
        entry(dates[0], profile(known={"capco:C10": "100"})),
        entry(dates[6], profile(undisclosed="100")),
    ]
    for values in forecasts.values():
        del values["sh600006"]
        values["sh510300"] = {"rank": 0.5, "mu": 0.05, "margin": 0.0}
    return bundle, forecasts


def test_fund_identity_is_unavailable_before_its_source():
    bundle, _ = fixture()
    fund = bundle["meta"]["sh510300"]
    fund["fund_history"] = [entry(bundle["calendar"][6], profile())]
    assert industry_at(fund, "2026-01-05T16:00:00+08:00") is None


def test_partial_stock_schedule_cannot_hide_concentration():
    bundle, _ = opening_change()
    meta = metadata_at(bundle["meta"], "2026-01-13T09:25:00+08:00")
    assert not post_buy_limits(
        {"sh600001": 140000, "sh600002": 140000}, meta, "sh510300", 140000, 1000000, 0.9
    )


def test_new_opening_profile_resizes_the_frozen_close_intention():
    bundle, forecasts = opening_change()
    dates = bundle["calendar"]
    account = new_account(str(dates[5].date()), "fund_boundary")
    intent = make_intent(prefix(bundle, 5), account, forecasts[5], "baseline", str(dates[6].date()), 0.001)
    original = deepcopy(intent)
    preview = resolve_intent(intent, account, [], calendar_for(bundle), {}, preview=True)
    actual = resolve_intent(intent, account, [], calendar_for(bundle), opening_quotes(intent))
    preview_value = sum(t["gross"] for t in preview["trades"] if t["symbol"] in ("sh600001", "sh600002", "sh510300"))
    actual_value = sum(t["gross"] for t in actual["trades"] if t["symbol"] in ("sh600001", "sh600002", "sh510300"))
    assert preview_value > 0.30 * preview["opening_nav_after_costs"]
    assert actual_value <= 0.30 * actual["opening_nav_after_costs"] + 1e-8
    assert actual["audit"]["status"] == "PASS"
    assert intent == original


def test_missing_new_fund_profile_stops_existing_holdings():
    bundle, forecasts = opening_change()
    bundle["meta"]["sh510300"]["fund_history"].append(entry(bundle["calendar"][7], None))
    with pytest.raises(ValueError, match="industry"):
        simulate(bundle, 6, 7, forecasts)
