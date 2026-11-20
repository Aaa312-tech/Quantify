"""Source units, reference prices and fallback scope are data contracts, not alpha choices."""

import json
from copy import deepcopy

import pandas as pd
import pytest

from quantfolio.daily_sources import (
    BAOSTOCK_FIELDS,
    baostock_bars,
    compare_target,
    quote_entries,
    tencent_beijing_bar,
)
from quantfolio.market_data import COLUMNS
from quantfolio.refresh import (
    _closing_quotes,
    _collection_metadata,
    _daily_collection,
    _reviewed_raws,
)
from quantfolio.settings import Paths
from quantfolio.storage import digest, save, sha


def bao_sample(etf=False):
    symbol = "sh510300" if etf else "sh600001"
    meta = {
        symbol: {
            "symbol": symbol,
            "code": symbol[2:],
            "name": "Example",
            "kind": "etf" if etf else "stock",
        }
    }
    row = dict(
        zip(
            BAOSTOCK_FIELDS,
            [
                "2026-11-20",
                symbol[:2] + "." + symbol[2:],
                "10",
                "10.2",
                "9.9",
                "10.1",
                "10",
                "100050",
                "123456.5",
                "3",
                "8.811889" if etf else ".825000",
                "1",
                "1.000000",
                "",
                "",
                "",
                "",
                "1" if etf else "0",
            ],
            strict=True,
        )
    )
    payload = {
        "error_code": "0",
        "kind": "funds" if etf else "stocks",
        "fields": list(BAOSTOCK_FIELDS),
        "rows": [list(row.values())],
    }
    return payload, meta, symbol


def test_native_precision_and_whole_lot_conversion_do_not_double_round():
    payload, meta, symbol = bao_sample()
    before = deepcopy(payload)
    frame, detail = baostock_bars(payload, meta, "2026-11-20")[symbol]
    assert frame.iloc[0].volume_lots == 1001
    assert frame.iloc[0].amount == 123456.5
    assert frame.iloc[0].turnover == 0.825
    assert frame.iloc[0].pct == 1
    primary = frame.iloc[0].to_dict()
    primary["turnover"] = 0.82
    assert compare_target(primary, detail, "baostock_daily") == []
    assert compare_target(primary, detail, "tencent_beijing")[0]["field"] == "turnover"
    assert payload == before


def test_etf_vendor_warning_field_does_not_change_stock_eligibility_metadata():
    payload, meta, symbol = bao_sample(etf=True)
    before = deepcopy(meta)
    frame, detail = baostock_bars(payload, meta, "2026-11-20")[symbol]
    assert frame.iloc[0].turnover == 8.811889 and detail["source_isST"] == "1"
    assert meta == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("date", "2026-11-23"),
        ("adjustflag", "2"),
        ("tradestatus", "0"),
        ("isST", "1"),
        ("open", ""),
        ("close", "NaN"),
        ("amount", "Infinity"),
        ("high", "9.8"),
        ("preclose", "0"),
        ("volume", "-1"),
        ("volume", "100050.5"),
        ("turn", ".825001"),
        ("pctChg", "2.000000"),
    ],
)
def test_ambiguous_or_invalid_baostock_fields_are_rejected(field, value):
    payload, meta, _ = bao_sample()
    payload["rows"][0][BAOSTOCK_FIELDS.index(field)] = value
    with pytest.raises(ValueError):
        baostock_bars(payload, meta, "2026-11-20")


@pytest.mark.parametrize("fault", ["schema", "duplicate", "error", "kind", "metadata"])
def test_baostock_identity_and_schema_are_not_inferred(fault):
    payload, meta, symbol = bao_sample()
    if fault == "schema":
        payload["fields"] = list(reversed(payload["fields"]))
    elif fault == "duplicate":
        payload["rows"] *= 2
    elif fault == "error":
        payload["error_code"] = "1"
    elif fault == "kind":
        payload["kind"] = "funds"
    else:
        meta[symbol]["code"] = "600002"
    with pytest.raises(ValueError):
        baostock_bars(payload, meta, "2026-11-20")


def tencent_sample():
    meta = {"symbol": "bj920001", "code": "920001", "kind": "stock"}
    quote = [""] * 60
    for i, value in {
        2: "920001",
        3: "10.1",
        4: "10",
        5: "10",
        6: "1000",
        30: "20261120153000",
        31: ".1",
        32: "1",
        33: "10.2",
        34: "9.9",
        35: "10.1/1000/123456.5",
        36: "1000",
        38: ".82",
        43: "3",
        57: "12.34565",
    }.items():
        quote[i] = value
    values = [10, 10.1, 10.2, 9.9, 1000, 123456.5, 3, 1, 0.1, 0.82]
    existing = pd.DataFrame(
        [values], columns=COLUMNS, index=pd.to_datetime(["2026-11-19"]), dtype=float
    )
    rows = [
        [day, "10", "10.1", "10.2", "9.9", "1000", {}, "unused"]
        for day in ("2026-11-18", "2026-11-19", "2026-11-20")
    ]
    data = {"code": 0, "data": {"bj920001": {"day": rows}}}
    return meta, quote, existing, data


def tencent_parse(meta, quote, existing, data):
    payload = ("kline_day2026=" + json.dumps(data) + ";").encode()
    return tencent_beijing_bar(payload, quote, meta, "2026-11-19", "2026-11-20", existing)


def test_tencent_beijing_uses_target_close_and_observed_overlap_only():
    frame, details = tencent_parse(*tencent_sample())
    assert list(frame.index.strftime("%Y-%m-%d")) == ["2026-11-20"]
    assert frame.iloc[0].amount == 123456.5 and frame.iloc[0].volume_lots == 1000
    assert details["daily_ohlcv_checks"] == 10
    assert details["provider_independent_corroboration"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "other_board",
        "code",
        "stale",
        "intraday",
        "amount",
        "volume",
        "reference",
        "target_price",
        "old_price",
        "duplicate",
        "future",
        "missing",
        "adjusted",
    ],
)
def test_tencent_rejects_wrong_scope_timing_and_internal_conflicts(fault):
    meta, quote, existing, data = tencent_sample()
    rows = data["data"]["bj920001"]["day"]
    if fault == "other_board":
        meta["symbol"] = "sz002867"
    elif fault == "code":
        quote[2] = "920002"
    elif fault == "stale":
        quote[30] = "20261119153000"
    elif fault == "intraday":
        quote[30] = "20261120145959"
    elif fault == "amount":
        quote[57] = "12.34575"
    elif fault == "volume":
        quote[36] = "1001"
    elif fault == "reference":
        quote[4] = "0"
    elif fault == "target_price":
        rows[-1][1] = "10.01"
    elif fault == "old_price":
        rows[-2][2] = "10.01"
    elif fault == "duplicate":
        rows.append(rows[-1])
    elif fault == "future":
        rows[-1][0] = "2026-11-23"
    elif fault == "missing":
        rows.pop(-2)
    elif fault == "adjusted":
        data["data"]["bj920001"] = {"qfqday": rows}
    with pytest.raises(ValueError):
        tencent_parse(meta, quote, existing, data)


def test_quote_envelope_keeps_other_boards_out_of_beijing_validation():
    raw = 'v_bj920001="a~b";\nv_sz002867="unvalidated";'.encode("gb18030")
    assert quote_entries(raw) == {"bj920001": ["a", "b"], "sz002867": ["unvalidated"]}
    with pytest.raises(ValueError, match="duplicate"):
        quote_entries(raw + raw)


@pytest.mark.parametrize("index,value", [(32, "2"), (43, "4")])
def test_beijing_percent_fields_use_the_reported_reference_price(index, value):
    meta, quote, existing, data = tencent_sample()
    quote[index] = value
    with pytest.raises(ValueError, match="percent"):
        tencent_parse(meta, quote, existing, data)


@pytest.mark.parametrize(
    "timestamp,accepted",
    [
        ("2026-11-20T08:00:00+00:00", True),
        ("2026-11-20T16:00:00+08:00", True),
        ("2026-11-20T07:59:59+00:00", False),
        ("2026-11-20T15:59:59+08:00", False),
        ("2026-11-20T16:00:00", False),
    ],
)
def test_quote_acquisition_is_checked_in_shanghai_time(tmp_path, timestamp, accepted):
    folder = tmp_path / "quotes"
    folder.mkdir()
    file = folder / "batch.txt"
    file.write_bytes(b'v_bj920001="data";')
    save(
        folder / "protocol.json",
        {
            "provider": "tencent_closing_quote",
            "target_date": "2026-11-20",
            "source_bundle_sha256": "parent",
            "symbols": ["bj920001"],
        },
    )
    save(
        folder / "collection.json",
        {
            "status": "COMPLETE",
            "records": [
                {
                    "requested_at": timestamp,
                    "status": "RECEIVED",
                    "file": "quotes/batch.txt",
                    "sha256": sha(file),
                    "symbols": ["bj920001"],
                    "returned_symbols": ["bj920001"],
                }
            ],
        },
    )
    item = {
        "path": "quotes",
        "protocol_sha256": sha(folder / "protocol.json"),
        "collection_sha256": sha(folder / "collection.json"),
    }
    protocol = {"completed_date": "2026-11-20", "collection_source_sha256": "parent"}
    if accepted:
        quotes, _ = _closing_quotes(Paths(tmp_path), item, protocol)
        assert set(quotes) == {"bj920001"}
    else:
        with pytest.raises(ValueError):
            _closing_quotes(Paths(tmp_path), item, protocol)


def test_display_intervals_do_not_hide_real_turnover_or_amount_disagreements():
    payload, meta, symbol = bao_sample()
    frame, details = baostock_bars(payload, meta, "2026-11-20")[symbol]
    primary = frame.iloc[0].to_dict()
    primary.update(turnover=0.81, amount=123457.5)
    assert {x["field"] for x in compare_target(primary, details, "baostock_daily")} == {
        "turnover",
        "amount",
    }


@pytest.mark.parametrize("fault", [None, "industry", "periods", "source", "missing_review"])
def test_older_collection_can_only_retain_explicit_dated_status_additions(tmp_path, fault):
    original = {"meta": {"sh600001": {"industry": "A"}}, "raws": {}}
    parent = deepcopy(original)
    periods = [{"start": "2025-01-01", "end": "2025-06-01"}]
    parent["meta"]["sh600001"]["risk_warning_periods"] = periods
    if fault == "industry":
        parent["meta"]["sh600001"]["industry"] = "B"
    save(tmp_path / "source.json", {})
    review = {
        "status": "DATED_STATUS_ADDITIONS_VERIFIED",
        "before_meta_sha256": digest(original["meta"]),
        "after_meta_sha256": digest(parent["meta"]),
        "before_bundle_sha256": "old",
        "after_bundle_sha256": "new",
        "added_risk_warning_periods": {"sh600001": periods if fault != "periods" else []},
        "source_review": "source.json",
        "source_review_sha256": sha(tmp_path / "source.json"),
    }
    save(tmp_path / "review.json", review)
    protocol = {
        "source_bundle_sha256": "new",
        "collection_source_sha256": "old",
        "collection_metadata_review": {
            "path": "review.json",
            "sha256": sha(tmp_path / "review.json"),
        },
    }
    if fault == "source":
        save(tmp_path / "source.json", {"changed": True})
    if fault == "missing_review":
        protocol.pop("collection_metadata_review")
    before = deepcopy(parent)
    if fault:
        with pytest.raises(ValueError):
            _collection_metadata(Paths(tmp_path), protocol, parent, original)
    else:
        _collection_metadata(Paths(tmp_path), protocol, parent, original)
    assert parent == before


@pytest.mark.parametrize("fault", [None, "unfinished", "date", "hash", "parent", "path"])
def test_fallback_collection_must_match_the_registered_completed_source(tmp_path, fault):
    directory = tmp_path / "data/raw/example"
    save(
        directory / "protocol.json",
        {"target_date": "2026-11-20", "provider": "baostock", "source_bundle_sha256": "parent"},
    )
    save(
        directory / "collection.json",
        {"status": "RUNNING" if fault == "unfinished" else "COMPLETE", "records": [{}]},
    )
    item = {
        "path": "data/raw/example",
        "protocol_sha256": sha(directory / "protocol.json"),
        "collection_sha256": sha(directory / "collection.json"),
    }
    if fault == "hash":
        save(directory / "collection.json", {})
    if fault == "path":
        item["path"] = "../outside"
    arguments = (
        Paths(tmp_path),
        item,
        "2026-11-19" if fault == "date" else "2026-11-20",
        "other" if fault == "parent" else "parent",
        "baostock",
    )
    if fault:
        with pytest.raises((ValueError, FileNotFoundError)):
            _daily_collection(*arguments)
    else:
        _daily_collection(*arguments)


def test_fallback_assignment_cannot_silently_switch_provider(tmp_path, monkeypatch):
    import quantfolio.refresh as refresh

    _, meta, _ = bao_sample()
    parent = {"meta": meta, "raws": {}, "calendar": pd.to_datetime(["2026-11-19"])}

    def fallback(paths, protocol, parent, received):
        received["sh600001"] = None
        return [{"symbol": "sh600001", "provider": "baostock_daily", "used": True}]

    monkeypatch.setattr(refresh, "_fallback_raws", fallback)
    save(
        tmp_path / "assignment.json",
        {
            "parent_bundle_sha256": "parent",
            "assignments": [{"symbol": "sh600001", "provider": "tencent"}],
        },
    )
    protocol = {
        "collections": [],
        "fallback_collections": [{}],
        "source_bundle_sha256": "parent",
        "source_assignment": {
            "path": "assignment.json",
            "sha256": sha(tmp_path / "assignment.json"),
        },
    }
    with pytest.raises(ValueError, match="assignment"):
        _reviewed_raws(Paths(tmp_path), protocol, parent, parent)
