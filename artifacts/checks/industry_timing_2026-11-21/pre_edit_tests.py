"""Published classifications must follow the signal and execution information clocks."""

from copy import deepcopy

import pytest
from conftest import fixture
from test_planning import calendar_for, opening_quotes, prefix
from test_preprocessing import inputs

from quantfolio.accounts import new_account
from quantfolio.planning import make_intent, resolve_intent
from quantfolio.preprocessing import build_dataset
from quantfolio.simulation import simulate


def entry(day, industry):
    return {"available_at": str(day.date()) + "T00:00:00+08:00", "industry": industry}


def changing_groups():
    bundle, forecasts = fixture()
    dates = bundle["calendar"]
    for symbol, instrument in bundle["meta"].items():
        if instrument["kind"] == "stock":
            instrument["industry_history"] = [
                entry(dates[0], symbol),
                entry(dates[6], "capco:C39"),
            ]
    return bundle, forecasts


def test_unknown_industry_is_excluded_before_ranks_and_labels():
    meta, raws = inputs()
    dates = raws["sh510300"].index
    meta["sh600001"]["industry_history"] = [entry(dates[140], "capco:C39")]
    bundle = build_dataset(meta, raws, [])
    assert not bundle["panels"]["eligible"].loc[dates[125:140], "sh600001"].any()
    assert bundle["scores"]["momentum"].loc[dates[125:140], "sh600001"].isna().all()
    rows = bundle["table"].query("symbol == 'sh600001' and 125 <= date_index < 140")
    assert rows.y5.isna().all() and not rows.eligible.any()
    assert bundle["panels"]["eligible"].at[dates[140], "sh600001"]


def test_dataset_uses_the_category_available_on_each_signal_day():
    meta, raws = inputs()
    dates = raws["sh510300"].index
    for instrument in meta.values():
        if instrument["kind"] == "stock":
            instrument["industry_history"] = [entry(dates[0], "capco:C39")]
    meta["sh600001"]["industry_history"].append(entry(dates[135], "capco:I65"))
    bundle = build_dataset(meta, raws, [])
    rows = bundle["table"].query("symbol == 'sh600001'").set_index("date_index")
    assert rows.loc[130, "industry"] == "capco:C39"
    assert rows.loc[136, "industry"] == "capco:I65"
    assert bundle["meta"] == meta


def test_opening_reclassification_limits_buys_without_rewriting_close_targets():
    bundle, forecasts = changing_groups()
    _, ledger = simulate(bundle, 6, 6, forecasts)
    assert len(ledger["decisions"][0]["target_weights"]) == 6
    closing = ledger["curve"][0]
    stock_value = sum(value["value"] for value in closing["positions"].values())
    assert stock_value <= 0.30 * closing["nav"] + 1e-8
    assert all(mark["max_industry_weight"] <= 0.30 + 1e-8 for mark in ledger["intermediate_weights"])


def test_paper_preview_and_actual_opening_use_their_own_information_time():
    bundle, forecasts = changing_groups()
    day = str(bundle["calendar"][6].date())
    account = new_account(str(bundle["calendar"][5].date()), "industry_boundary")
    intent = make_intent(prefix(bundle, 5), account, forecasts[5], "baseline", day, 0.001)
    frozen = deepcopy(intent)
    preview = resolve_intent(intent, account, [], calendar_for(bundle), {}, preview=True)
    actual = resolve_intent(intent, account, [], calendar_for(bundle), opening_quotes(intent))
    preview_gross = sum(t["gross"] for t in preview["trades"])
    actual_gross = sum(t["gross"] for t in actual["trades"])
    assert preview_gross > 0.30 * preview["opening_nav_after_costs"]
    assert actual_gross <= 0.30 * actual["opening_nav_after_costs"] + 1e-8
    assert actual["audit"]["status"] == "PASS"
    assert intent == frozen


def test_missing_classification_for_a_holding_stops_the_account():
    bundle, forecasts = fixture()
    dates = bundle["calendar"]
    bundle["meta"]["sh600001"]["industry_history"] = [
        entry(dates[0], "capco:C39"),
        entry(dates[7], None),
    ]
    with pytest.raises(ValueError, match="industry"):
        simulate(bundle, 6, 7, forecasts)
