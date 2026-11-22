"""Reproduce missing fund availability and cross-holding concentration checks."""

from copy import deepcopy

import pytest

from quantfolio.accounts import new_account
from quantfolio.industries import industry_at, metadata_at
from quantfolio.planning import make_intent, resolve_intent
from quantfolio.simulation import post_buy_limits, simulate
from tests.factories import (
    bundle_prefix,
    fund_history_entry,
    fund_profile,
    market_bundle,
    opening_quotes,
    trading_calendar,
)


def opening_change():
    bundle, forecasts = market_bundle()
    dates = bundle["calendar"]
    for symbol, instrument in bundle["meta"].items():
        if instrument["kind"] == "stock":
            instrument["industry"] = (
                "capco:C39" if symbol[-1] in "12" else "capco:C" + symbol[-1] * 2
            )
    bundle["meta"]["sh510300"]["fund_history"] = [
        fund_history_entry(dates[0], fund_profile(known={"capco:C10": "100"})),
        fund_history_entry(dates[6], fund_profile(undisclosed="100")),
    ]
    for values in forecasts.values():
        del values["sh600006"]
        values["sh510300"] = {"rank": 0.5, "mu": 0.05, "margin": 0.0}
    return bundle, forecasts


def test_fund_identity_is_unavailable_before_its_source():
    bundle, _ = market_bundle()
    fund = bundle["meta"]["sh510300"]
    fund["fund_history"] = [fund_history_entry(bundle["calendar"][6], fund_profile())]
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
    intent = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", str(dates[6].date()), 0.001
    )
    original = deepcopy(intent)
    preview = resolve_intent(intent, account, [], trading_calendar(bundle), {}, preview=True)
    actual = resolve_intent(intent, account, [], trading_calendar(bundle), opening_quotes(intent))
    preview_value = sum(
        t["gross"] for t in preview["trades"] if t["symbol"] in ("sh600001", "sh600002", "sh510300")
    )
    actual_value = sum(
        t["gross"] for t in actual["trades"] if t["symbol"] in ("sh600001", "sh600002", "sh510300")
    )
    assert preview_value > 0.30 * preview["opening_nav_after_costs"]
    assert actual_value <= 0.30 * actual["opening_nav_after_costs"] + 1e-8
    assert actual["audit"]["status"] == "PASS"
    assert intent == original


def test_missing_new_fund_profile_stops_existing_holdings():
    bundle, forecasts = opening_change()
    bundle["meta"]["sh510300"]["fund_history"].append(
        fund_history_entry(bundle["calendar"][7], None)
    )
    with pytest.raises(ValueError, match="industry"):
        simulate(bundle, 6, 7, forecasts)


def test_unknown_and_signed_residuals_are_not_normalized_or_offset():
    from fractions import Fraction

    from quantfolio.funds import reported_components

    value = fund_profile(known={"capco:C39": "20"}, unmatched="5", undisclosed="70", residual="-3")
    value["reported_index_contracts"] = [
        {
            "kind": "stock_index_futures",
            "reported_market_value_cny": "10",
            "reported_fair_value_change_cny": "9",
        },
        {
            "kind": "stock_index_futures",
            "reported_market_value_cny": "-7",
            "reported_fair_value_change_cny": "-9",
        },
    ]
    known, parts = reported_components(value)
    assert known == {"capco:C39": Fraction(1, 5)}
    assert parts["reported_index_contracts"] == Fraction(17, 100)
    assert sum(parts.values()) == Fraction(95, 100)
    assert value["signed_equity_sector_residual"]["amount_cny"] == "-3"
    assert value["economic_exposure_upper_bound"] is None


def test_unknown_amount_is_limited_even_without_a_named_division():
    from quantfolio.funds import concentration_rows, purchase_capacity

    bundle, _ = opening_change()
    meta = metadata_at(bundle["meta"], "2026-01-13T09:25:00+08:00")
    assert concentration_rows(meta, ["sh510300"])["reported:unnamed_division"] == {"sh510300": 1.0}
    assert (
        purchase_capacity(meta, {"sh600001": 140000, "sh600002": 140000}, "sh510300", 1000000)
        == 20000
    )


def test_non_equity_source_is_unknown_economics_with_zero_reported_stocks():
    from quantfolio.funds import exposure_report

    bundle, _ = market_bundle()
    bundle["meta"]["sh510300"]["fund_history"] = [
        fund_history_entry(bundle["calendar"][0], fund_profile())
    ]
    meta = metadata_at(bundle["meta"], "2026-01-13T09:25:00+08:00")
    report = exposure_report(meta, {"sh510300": 100000}, 1000000, "2026-01-13T09:25:00+08:00")
    assert report["max_reported_equity_weight"] == 0
    assert report["uncertified_fund_weight"] == 0.1
    assert report["economic_exposure_upper_bound"] is None
    assert not report["complete_economic_exposure_certified"]


def test_future_profile_cannot_change_earlier_views():
    bundle, _ = opening_change()
    before = metadata_at(bundle["meta"], "2026-01-12T16:00:00+08:00")
    modified = deepcopy(bundle["meta"])
    modified["sh510300"]["fund_history"][-1]["profile"]["undisclosed_stocks"]["amount_cny"] = (
        "999999"
    )
    after = metadata_at(modified, "2026-01-12T16:00:00+08:00")
    assert before["sh510300"]["fund_profile"] == after["sh510300"]["fund_profile"]


def test_independent_audit_detects_tampered_exposure_mark():
    from quantfolio.validation import execution_audit

    bundle, forecasts = opening_change()
    bundle["fund_mode"] = "reported"
    _, ledger = simulate(bundle, 6, 6, forecasts)
    assert execution_audit(ledger, bundle)["status"] == "PASS"
    ledger["intermediate_weights"][0]["fund_exposure"]["uncertified_fund_weight"] += 0.01
    assert execution_audit(ledger, bundle)["status"] == "FAIL"


def test_reported_limits_apply_to_target_weights_and_paper_replay():
    from quantfolio.funds import concentration_values
    from quantfolio.verification import _paper_account

    bundle, forecasts = opening_change()
    bundle["fund_mode"] = "reported"
    summary, ledger = simulate(bundle, 7, 10, forecasts)
    meta = metadata_at(bundle["meta"], "2026-01-13T16:00:00+08:00")
    decision = ledger["decisions"][0]
    assert max(concentration_values(meta, decision["target_weights"]).values()) <= 0.28 + 1e-8
    reference = {"summary": summary, "ledger": ledger}
    actual, _ = _paper_account(bundle, forecasts, "baseline", reference, 0.001)
    assert actual == reference


def test_missing_fund_source_masks_features_before_cross_ranks():
    from quantfolio.preprocessing import build_dataset
    from tests.factories import raw_history

    meta, raws = raw_history()
    dates = raws["sh510300"].index
    meta["sh510300"]["fund_history"] = [fund_history_entry(dates[140], fund_profile())]
    bundle = build_dataset(meta, raws, [])
    rows = bundle["table"].query("symbol == 'sh510300' and 125 <= date_index < 140")
    assert not rows.eligible.any() and rows.y5.isna().all()
    assert rows.momentum_score.isna().all() and rows.industry.isna().all()
    assert bundle["panels"]["eligible"].at[dates[140], "sh510300"]


def test_fund_identity_reaches_residual_targets_and_neutral_scores():
    import numpy as np
    import pandas as pd

    from quantfolio.features import INDUSTRY_FEATURES, industry_features
    from quantfolio.models import industry_residuals, neutral_scores

    bundle, _ = market_bundle()
    dates = bundle["calendar"]
    symbols = ["sh510300", "sh510301", "sh510302"]
    meta = {
        s: {
            "kind": "etf",
            "industry": s,
            "fund_history": [fund_history_entry(dates[0], fund_profile())],
        }
        for s in symbols
    }
    table = pd.DataFrame(
        {"date_index": [0, 0, 0], "symbol": symbols, "eligible": [True] * 3, "y5": [0.1, 0.2, 0.3]}
    )
    for name in INDUSTRY_FEATURES:
        table[name] = [0.1, 0.2, 0.3]
    ranks, table["industry"] = industry_features(table, meta, dates)
    relative, supported = industry_residuals(table)
    np.testing.assert_allclose(relative, [-0.15, 0, 0.15], atol=1e-15)
    assert supported.all() and ranks.industry_ret1.tolist() == [-0.125, 0, 0.125]
    neutral, supported = neutral_scores(table, np.arange(3), np.array([1.0, 2.0, 3.0]))
    np.testing.assert_allclose(neutral, [-0.375, 0, 0.375])
    assert supported.all()


@pytest.mark.parametrize("fault", ["naive", "duplicate", "company", "missing_profile"])
def test_invalid_fund_history_is_rejected_by_runtime_and_independent_audit(fault):
    from quantfolio.validation import checked_industries

    bundle, _ = opening_change()
    fund = bundle["meta"]["sh510300"]
    if fault == "naive":
        fund["fund_history"][0]["available_at"] = "2026-01-05"
    elif fault == "duplicate":
        fund["fund_history"][1] = deepcopy(fund["fund_history"][0])
    elif fault == "company":
        fund["kind"] = "stock"
    else:
        fund["fund_history"][0]["profile"] = None
    with pytest.raises(ValueError):
        industry_at(fund, "2026-01-20T16:00:00+08:00")
    with pytest.raises(ValueError):
        checked_industries({"fund": fund}, "2026-01-20T16:00:00+08:00")


def test_fund_builder_rejects_escaping_protocol_and_output(tmp_path):
    from quantfolio.funds import build_fund_snapshot
    from quantfolio.settings import Paths
    from quantfolio.storage import save

    paths = Paths(tmp_path)
    with pytest.raises(ValueError, match="inside"):
        build_fund_snapshot(paths, "../outside.json", "coverage")
    save(
        tmp_path / "protocol.json",
        {
            "name": "etf_integration",
            "variants": {"coverage": {"snapshot_output": "../outside"}},
        },
    )
    with pytest.raises(ValueError, match="inside"):
        build_fund_snapshot(paths, "protocol.json", "coverage")
    assert list(tmp_path.iterdir()) == [tmp_path / "protocol.json"]
