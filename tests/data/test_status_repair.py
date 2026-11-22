"""Dated warning facts must change eligibility without leaking future notices."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from quantfolio.accounts import new_account
from quantfolio.market import LABEL_STATUS, board_limit, legal_label, opening_price, risk_warning
from quantfolio.planning import make_intent, resolve_intent
from quantfolio.preprocessing import build_dataset
from quantfolio.settings import Paths
from quantfolio.simulation import simulate
from quantfolio.status_repair import (
    AVAILABILITY_BASIS,
    _review_inputs,
    compare_provider,
    reviewed_meta,
)
from quantfolio.storage import dump_gzip, save, sha
from quantfolio.validation import opening_errors
from tests.factories import (
    bundle_prefix,
    market_bundle,
    opening_quotes,
    raw_history,
    trading_calendar,
)


def fact(symbol="sh600001", start="2026-01-13", end="2026-01-19", limit=0.05):
    return {
        "symbol": symbol,
        "start": start,
        "end": end,
        "limit": limit,
        "start_available_at": start + "T00:00:00+08:00",
        "end_available_at": end + "T00:00:00+08:00",
        "availability_basis": AVAILABILITY_BASIS,
        "start_scope": "ANNOUNCED_EFFECTIVE_DATE",
        "sources": [
            {"notice_date": str((pd.Timestamp(day) - pd.Timedelta(days=1)).date())}
            for day in (start, end)
        ],
    }


def test_effective_boundaries_and_unknown_removal():
    bundle, _ = market_bundle()
    original = deepcopy(bundle["meta"])
    meta = reviewed_meta(original, [fact()])["sh600001"]
    assert original == bundle["meta"] and "risk_warning_periods" not in original["sh600001"]
    assert risk_warning(meta, "2026-01-12") is None
    assert board_limit(meta, "2026-01-13") == 0.05
    assert board_limit(meta, "2026-01-19") == 0.10
    assert risk_warning(meta, "2026-01-13", as_of="2026-01-12T16:00:00+08:00") is None
    assert board_limit(meta, "2026-01-19", as_of="2026-01-16T16:00:00+08:00") == 0.05
    with pytest.raises(ValueError, match="timezone-aware"):
        risk_warning(meta, "2026-01-13", as_of="2026-01-12T16:00:00")


@pytest.mark.parametrize("growth", [False, True])
def test_warning_excludes_buys_but_uses_sourced_limit_for_sells(growth):
    bundle, _ = market_bundle()
    symbol = "sh600001"
    if growth:
        bundle["meta"][symbol]["code"] = "300001"
    bundle["meta"] = reviewed_meta(bundle["meta"], [fact(limit=0.20 if growth else 0.05)])
    dates, raw = bundle["calendar"], bundle["raws"][symbol]
    raw.at[dates[6], "open"] = 9.5
    meta = bundle["meta"][symbol]
    assert opening_price(meta, raw, dates[6], dates[5])[1] == "RISK_WARNING_BUY_EXCLUDED"
    quote, reason = opening_price(meta, raw, dates[6], dates[5], side="SELL")
    assert (quote, reason) == ((9.5, None) if growth else (None, "OPEN_AT_LOWER_LIMIT"))
    trade = {"symbol": symbol, "date": str(dates[6].date()), "side": "BUY", "price": 9.5}
    assert "BUY_DURING_RISK_WARNING" in opening_errors(trade, bundle)
    trade["side"] = "SELL"
    assert opening_errors(trade, bundle) == ([] if growth else ["DIRECTIONAL_LIMIT_FILL"])


def test_label_cannot_enter_on_warning_start_and_held_exit_waits_for_legal_opening():
    bundle, _ = market_bundle()
    bundle["meta"] = reviewed_meta(bundle["meta"], [fact()])
    dates = bundle["calendar"]
    raw, meta = bundle["raws"]["sh600001"], bundle["meta"]["sh600001"]
    raw.at[dates[6], "open"] = 9.5
    raw.at[dates[7], "open"] = 9.51
    assert legal_label(meta, raw, dates, 5, 1, [])[2] == LABEL_STATUS["NO_ENTRY"]
    value, end, status, delay = legal_label(meta, raw, dates, 4, 1, [])
    assert np.isclose(value, -0.049) and (end, status, delay) == (7, 0, 1)


def test_preview_uses_prior_information_and_actual_opening_rechecks_new_notice():
    bundle, forecasts = market_bundle()
    account = new_account("2026-01-12", "synthetic")
    original = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", "2026-01-13", 0.001
    )
    bundle["meta"] = reviewed_meta(bundle["meta"], [fact()])
    intent = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", "2026-01-13", 0.001
    )
    assert intent["target_weights"] == original["target_weights"]
    for symbol in intent["contracts"]:
        assert (
            intent["contracts"][symbol]["assumed_upper_limit"]
            == original["contracts"][symbol]["assumed_upper_limit"]
        )
    preview = resolve_intent(intent, account, [], trading_calendar(bundle), {}, preview=True)
    actual = resolve_intent(intent, account, [], trading_calendar(bundle), opening_quotes(intent))
    assert any(t["symbol"] == "sh600001" for t in preview["trades"])
    assert all(t["symbol"] != "sh600001" for t in actual["trades"])
    assert any(r["reason"] == "RISK_WARNING_BUY_EXCLUDED" for r in actual["rejections"])
    _, simulated = simulate(bundle, 6, 6, forecasts)
    assert simulated["trades"] == actual["trades"]
    # If the same fact was already public at the signal close, remove it from the targets too.
    known = fact()
    known["sources"][0]["notice_date"] = "2026-01-11"
    known["start_available_at"] = "2026-01-12T00:00:00+08:00"
    bundle["meta"]["sh600001"].pop("risk_warning_periods")
    bundle["meta"] = reviewed_meta(bundle["meta"], [known])
    intent = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", "2026-01-13", 0.001
    )
    assert "sh600001" not in intent["target_weights"]


def test_dated_eligibility_recovers_after_removal_and_future_facts_preserve_feature_prefix():
    meta, raws = raw_history()
    dates = raws["sh510300"].index
    original = build_dataset(meta, raws, [])
    warning = fact(start=str(dates[135].date()), end=str(dates[140].date()))
    repaired_meta = reviewed_meta(meta, [warning])
    repaired = build_dataset(repaired_meta, raws, [])
    expected = original["panels"]["eligible"].copy()
    expected.loc[dates[135:140], "sh600001"] = False
    pd.testing.assert_frame_equal(repaired["panels"]["eligible"], expected)
    earlier = original["table"].date_index < 135
    columns = ["date_index", "symbol", "eligible", *original["features"]]
    pd.testing.assert_frame_equal(
        original["table"].loc[earlier, columns], repaired["table"].loc[earlier, columns]
    )
    entry = repaired["table"].query("symbol == 'sh600001' and date_index == 134").iloc[0]
    assert entry.status1 == LABEL_STATUS["NO_ENTRY"]
    repaired_meta["sh600001"]["name"] = "ST current-name snapshot"
    dated_name = build_dataset(repaired_meta, raws, [])
    pd.testing.assert_frame_equal(dated_name["panels"]["eligible"], expected)


@pytest.mark.parametrize("fault", ["overlap", "late", "naive", "basis", "inverted", "etf", "limit"])
def test_invalid_reviewed_facts_fail_before_modifying_parent(fault):
    bundle, _ = market_bundle()
    original = deepcopy(bundle["meta"])
    warning = fact()
    if fault == "late":
        warning["sources"][0]["notice_date"] = warning["start"]
        warning["start_available_at"] = "2026-01-14T00:00:00+08:00"
    elif fault == "naive":
        warning["start_available_at"] = "2026-01-13T00:00:00"
    elif fault == "basis":
        warning["availability_basis"] = "COLLECTION_TIME"
    elif fault == "inverted":
        warning["end"] = warning["start"]
    elif fault == "etf":
        warning["symbol"] = "sh510300"
    elif fault == "limit":
        warning["limit"] = float("nan")
    with pytest.raises(ValueError):
        reviewed_meta(bundle["meta"], [warning, warning] if fault == "overlap" else [warning])
    assert bundle["meta"] == original


def test_provider_crosscheck_requires_all_positive_sessions_and_securities():
    bundle, _ = market_bundle()
    dates = bundle["calendar"][6:10]
    records = [
        {
            "symbol": "sh600001",
            "st_sessions": 4,
            "st_eligible_sessions": 4,
            "st_eligible_dates": [str(d.date()) for d in dates],
            "st_periods": [{"first": "2026-01-13", "last": "2026-01-16", "sessions": 4}],
        }
    ]
    provider = {"st_securities": 1, "st_sessions": 4, "st_eligible_sessions": 4}
    assert compare_provider(bundle, [fact()], provider, records)[0]["previously_eligible"] == 4
    records[0]["st_periods"][0]["last"] = "2026-01-15"
    with pytest.raises(ValueError, match="calendar"):
        compare_provider(bundle, [fact()], provider, records)
    with pytest.raises(ValueError, match="every screened positive"):
        compare_provider(bundle, [], provider, records)


@pytest.mark.parametrize("tamper", ["none", "source", "review", "evidence", "provider", "snapshot"])
def test_registered_input_hashes_and_snapshot_binding(tmp_path, tamper):
    source = tmp_path / "bundle.joblib"
    source.write_bytes(b"not deserialized by this source-check test")
    evidence = tmp_path / "notice.pdf"
    evidence.write_bytes(b"reviewed source placeholder")
    warning = fact()
    for item in warning["sources"]:
        item.update(path="notice.pdf", sha256=sha(evidence))
    review = {
        "status": "ISSUER_NOTICES_REVIEWED",
        "data_snapshot_sha256": sha(source),
        "facts": [warning],
    }
    if tamper == "snapshot":
        review["data_snapshot_sha256"] = "wrong snapshot"
    save(tmp_path / "review.json", review)
    save(tmp_path / "provider.json", {"source_bundle_sha256": sha(source)})
    dump_gzip(tmp_path / "records.json.gz", [])
    protocol = {
        "source_bundle": "bundle.joblib",
        "source_bundle_sha256": sha(source),
        "review": "review.json",
        "review_sha256": sha(tmp_path / "review.json"),
        "provider_audit": "provider.json",
        "provider_audit_sha256": sha(tmp_path / "provider.json"),
        "provider_records": "records.json.gz",
        "provider_records_sha256": sha(tmp_path / "records.json.gz"),
    }
    save(tmp_path / "protocol.json", protocol)
    altered = {
        "source": source,
        "evidence": evidence,
        "review": tmp_path / "review.json",
        "provider": tmp_path / "provider.json",
    }
    if tamper in altered:
        altered[tamper].write_bytes(b"changed")
    if tamper == "none":
        assert _review_inputs(Paths(tmp_path), tmp_path / "protocol.json")[2] == review
    else:
        with pytest.raises(ValueError, match="changed|another snapshot"):
            _review_inputs(Paths(tmp_path), tmp_path / "protocol.json")
