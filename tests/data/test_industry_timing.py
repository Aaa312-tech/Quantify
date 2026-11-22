"""Published classifications must follow the signal and execution information clocks."""

from copy import deepcopy

import pandas as pd
import pytest

from quantfolio.accounts import new_account
from quantfolio.industries import compile_histories, industry_at, metadata_at
from quantfolio.planning import make_intent, resolve_intent
from quantfolio.preprocessing import build_dataset
from quantfolio.simulation import simulate
from quantfolio.validation import checked_industries, execution_audit, reconcile
from tests.factories import (
    bundle_prefix,
    market_bundle,
    opening_quotes,
    raw_history,
    trading_calendar,
)


def entry(day, industry):
    return {"available_at": str(day.date()) + "T00:00:00+08:00", "industry": industry}


def changing_groups():
    bundle, forecasts = market_bundle()
    dates = bundle["calendar"]
    for symbol, instrument in bundle["meta"].items():
        if instrument["kind"] == "stock":
            instrument["industry_history"] = [
                entry(dates[0], symbol),
                entry(dates[6], "capco:C39"),
            ]
    return bundle, forecasts


def test_unknown_industry_is_excluded_before_ranks_and_labels():
    meta, raws = raw_history()
    dates = raws["sh510300"].index
    meta["sh600001"]["industry_history"] = [entry(dates[140], "capco:C39")]
    bundle = build_dataset(meta, raws, [])
    assert not bundle["panels"]["eligible"].loc[dates[125:140], "sh600001"].any()
    assert bundle["scores"]["momentum"].loc[dates[125:140], "sh600001"].isna().all()
    rows = bundle["table"].query("symbol == 'sh600001' and 125 <= date_index < 140")
    assert rows.y5.isna().all() and not rows.eligible.any()
    assert bundle["panels"]["eligible"].at[dates[140], "sh600001"]


def test_dataset_uses_the_category_available_on_each_signal_day():
    meta, raws = raw_history()
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
    assert all(
        mark["max_industry_weight"] <= 0.30 + 1e-8 for mark in ledger["intermediate_weights"]
    )


def test_paper_preview_and_actual_opening_use_their_own_information_time():
    bundle, forecasts = changing_groups()
    day = str(bundle["calendar"][6].date())
    account = new_account(str(bundle["calendar"][5].date()), "industry_boundary")
    intent = make_intent(bundle_prefix(bundle, 5), account, forecasts[5], "baseline", day, 0.001)
    frozen = deepcopy(intent)
    preview = resolve_intent(intent, account, [], trading_calendar(bundle), {}, preview=True)
    actual = resolve_intent(intent, account, [], trading_calendar(bundle), opening_quotes(intent))
    preview_gross = sum(t["gross"] for t in preview["trades"])
    actual_gross = sum(t["gross"] for t in actual["trades"])
    assert preview_gross > 0.30 * preview["opening_nav_after_costs"]
    assert actual_gross <= 0.30 * actual["opening_nav_after_costs"] + 1e-8
    assert actual["audit"]["status"] == "PASS"
    assert intent == frozen


def test_missing_classification_for_a_holding_stops_the_account():
    bundle, forecasts = market_bundle()
    dates = bundle["calendar"]
    bundle["meta"]["sh600001"]["industry_history"] = [
        entry(dates[0], "capco:C39"),
        entry(dates[7], None),
    ]
    with pytest.raises(ValueError, match="industry"):
        simulate(bundle, 6, 7, forecasts)


def source_fixture():
    calendar = pd.to_datetime(["2026-09-30", "2026-10-08", "2026-10-09"])
    meta = {
        "bj920001": {"kind": "stock", "industry": "Original", "code": "920001"},
        "sh510300": {"kind": "etf", "industry": "Broad index", "code": "510300"},
    }
    releases = []
    for date, section, division in [("2026-09-30", "C", "39"), ("2026-10-08", "I", "65")]:
        releases.append(
            {
                "catalogue_date": date,
                "source": date + ".pdf",
                "source_sha256": "a" * 64,
                "records": [
                    {
                        "code": "830001",
                        "section": section,
                        "division": division,
                        "page": 1,
                        "row": 2,
                    }
                ],
            }
        )
    return meta, calendar, releases, {"bj830001": "bj920001"}


def test_catalogue_day_is_excluded_and_holidays_delay_the_boundary():
    meta, calendar, releases, aliases = source_fixture()
    original = deepcopy(meta)
    result = compile_histories(meta, calendar, releases, aliases, "dated")
    stock = result["bj920001"]
    assert industry_at(stock, "2026-09-30T16:00:00+08:00") is None
    assert industry_at(stock, "2026-10-08T09:25:00+08:00") == "capco:C39"
    assert industry_at(stock, "2026-10-08T16:00:00+08:00") == "capco:C39"
    assert industry_at(stock, "2026-10-09T09:25:00+08:00") == "capco:I65"
    assert stock["industry_history"][0]["coverage_source"]["source_code"] == "830001"
    assert result["sh510300"] == original["sh510300"]
    assert meta == original


def test_controls_share_coverage_but_expose_their_different_grouping():
    meta, calendar, releases, aliases = source_fixture()
    views = {
        name: compile_histories(meta, calendar, releases, aliases, name)
        for name in ("coverage", "static", "dated")
    }
    for view in views.values():
        assert industry_at(view["bj920001"], "2026-09-30T16:00:00+08:00") is None
    observed = {
        name: industry_at(view["bj920001"], "2026-10-08T16:00:00+08:00")
        for name, view in views.items()
    }
    assert observed == {"coverage": "Original", "static": "capco:I65", "dated": "capco:C39"}
    assert (
        views["static"]["bj920001"]["industry_history"][0]["classification_source"]["path"]
        == "2026-10-08.pdf"
    )


def test_omitted_stock_does_not_inherit_its_previous_group():
    meta, calendar, releases, aliases = source_fixture()
    releases[-1]["records"] = []
    dated = compile_histories(meta, calendar, releases, aliases, "dated")
    assert industry_at(dated["bj920001"], "2026-10-09T09:25:00+08:00") is None
    with pytest.raises(ValueError, match="future-dependent mask"):
        compile_histories(meta, calendar, releases, aliases, "static")


@pytest.mark.parametrize(
    "fault",
    ["duplicate_code", "ambiguous_alias", "parent_conflict", "unordered_dates", "alias_collision"],
)
def test_ambiguous_source_identity_or_classification_is_rejected(fault):
    meta, calendar, releases, aliases = source_fixture()
    if fault == "duplicate_code":
        releases[0]["records"].append(deepcopy(releases[0]["records"][0]))
    elif fault == "ambiguous_alias":
        releases[0]["records"].append({**releases[0]["records"][0], "code": "920001"})
    elif fault == "parent_conflict":
        releases[0]["records"][0]["taxonomy_findings"] = [{"problem": "parent_mismatch"}]
    elif fault == "unordered_dates":
        releases.reverse()
    else:
        aliases["bj830002"] = "bj920001"
    with pytest.raises(ValueError):
        compile_histories(meta, calendar, releases, aliases, "dated")


def test_last_catalogue_release_during_a_closure_wins_at_the_next_session():
    meta, calendar, releases, aliases = source_fixture()
    releases[1]["catalogue_date"] = "2026-10-01"
    result = compile_histories(meta, calendar, releases, aliases, "dated")
    assert industry_at(result["bj920001"], "2026-10-08T09:25:00+08:00") == "capco:I65"
    assert len(result["bj920001"]["industry_history"]) == 1


@pytest.mark.parametrize("fault", ["naive", "duplicate", "out_of_order", "empty", "etf"])
def test_malformed_history_is_rejected_by_lookup_and_independent_audit(fault):
    instrument = {
        "kind": "stock",
        "industry": "Static",
        "industry_history": [
            {"available_at": "2026-01-01T00:00:00+08:00", "industry": "A"},
            {"available_at": "2026-02-01T00:00:00+08:00", "industry": "B"},
        ],
    }
    if fault == "naive":
        instrument["industry_history"][0]["available_at"] = "2026-01-01"
    elif fault == "duplicate":
        instrument["industry_history"][1] = instrument["industry_history"][0]
    elif fault == "out_of_order":
        instrument["industry_history"].reverse()
    elif fault == "empty":
        instrument["industry_history"] = []
    else:
        instrument["kind"] = "etf"
    with pytest.raises(ValueError):
        industry_at(instrument, "2026-03-01T16:00:00+08:00")
    with pytest.raises(ValueError):
        checked_industries({"example": instrument}, "2026-03-01T16:00:00+08:00")


def test_future_classification_changes_do_not_change_any_earlier_table_value():
    meta, raws = raw_history()
    dates = raws["sh510300"].index
    for instrument in meta.values():
        if instrument["kind"] == "stock":
            instrument["industry_history"] = [entry(dates[0], "capco:C39")]
    before = build_dataset(meta, raws, [])
    changed = deepcopy(meta)
    changed["sh600001"]["industry_history"].append(entry(dates[140], None))
    after = build_dataset(changed, raws, [])
    rows = before["table"].date_index < 140
    pd.testing.assert_frame_equal(
        before["table"].loc[rows], after["table"].loc[rows], check_exact=True
    )
    assert (
        metadata_at(meta, dates[130].tz_localize("Asia/Shanghai"))["sh600001"]["industry"]
        == "capco:C39"
    )


def test_independent_audit_catches_forged_industry_marks():
    bundle, forecasts = changing_groups()
    _, ledger = simulate(bundle, 6, 10, forecasts)
    assert execution_audit(ledger, bundle)["status"] == "PASS"
    ledger["intermediate_weights"][0]["max_industry_weight"] = 0.001
    result = execution_audit(ledger, bundle)
    assert result["status"] == "FAIL"
    assert any(error[0] == "DATED_INDUSTRY_WEIGHT_MISMATCH" for error in result["errors"])


def test_dated_audit_reconstructs_shares_and_receivables_across_actions():
    bundle, forecasts = market_bundle()
    dates = bundle["calendar"]
    symbol = "sh600001"
    bundle["meta"][symbol]["industry_history"] = [entry(dates[0], symbol)]
    bundle["actions"] = [
        {
            "id": "industry_action",
            "symbol": symbol,
            "record_date": str(dates[7].date()),
            "ex_date": str(dates[8].date()),
            "cash_per_share": 0.1,
            "share_multiplier": 1.5,
            "cash_pay_date": str(dates[10].date()),
            "cash_available_date": str(dates[11].date()),
            "share_list_date": str(dates[11].date()),
        }
    ]
    bundle["raws"][symbol].loc[dates[8] :, ["open", "close", "high", "low"]] = 6.6
    summary, ledger = simulate(bundle, 6, 16, forecasts)
    assert any(event["type"] == "CORPORATE_ACTION" for event in ledger["events"])
    assert reconcile(ledger, summary, bundle["meta"], bundle["actions"])["status"] == "PASS"
    assert execution_audit(ledger, bundle)["status"] == "PASS"


def test_industry_audit_retains_the_mark_for_shares_received_after_a_sale():
    bundle, forecasts = market_bundle()
    dates, symbol = bundle["calendar"], "sh600001"
    bundle["meta"][symbol]["industry_history"] = [entry(dates[0], symbol)]
    forecasts[5] = {symbol: forecasts[5][symbol]}
    bundle["actions"] = [
        {
            "id": "late_bonus",
            "symbol": symbol,
            "record_date": str(dates[7].date()),
            "ex_date": str(dates[16].date()),
            "cash_per_share": 0.0,
            "share_multiplier": 1.5,
            "share_list_date": str(dates[17].date()),
        }
    ]
    for value in forecasts[10].values():
        value["mu"] = -0.1
    bundle["raws"][symbol].loc[dates[16], ["open", "close"]] = float("nan")
    bundle["raws"][symbol].loc[dates[17] :, ["open", "close", "high", "low"]] = 6.67
    summary, ledger = simulate(bundle, 6, 18, forecasts)
    assert symbol not in ledger["curve"][11 - 6]["positions"]
    assert symbol in ledger["curve"][16 - 6]["positions"]
    assert any(row["problem"] == "STALE_MARK" for row in ledger["quality"])
    assert reconcile(ledger, summary, bundle["meta"], bundle["actions"])["status"] == "PASS"
    assert execution_audit(ledger, bundle)["status"] == "PASS"
