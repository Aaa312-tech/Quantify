"""Reviewed extensions must retain source identities and conservative cash timing."""

import json
from copy import deepcopy

import pandas as pd
import pytest

from quantfolio.market_data import COLUMNS, inspect_bars
from quantfolio.refresh import _reviewed_raws, reviewed_actions
from quantfolio.settings import Paths
from quantfolio.storage import save, sha


def event():
    return {
        "symbol": "sz002281",
        "notice_date": "2026-11-11",
        "record_date": "2026-11-18",
        "ex_date": "2026-11-19",
        "cash_pay_date": "2026-11-19",
        "cash_per_share": 0.37,
        "share_multiplier": 1.0,
        "share_list_date": None,
        "ex_reference_cash_per_share": 0.37,
        "ex_reference_share_multiplier": 1.0,
        "simulation_cash_available_date": None,
        "source": {
            "source": "https://example.org/filing.pdf",
            "local_pdf": "filing.pdf",
            "sha256": "test-hash",
        },
        "evidence_pages": [2],
    }


def test_announced_event_waits_for_observed_next_opening():
    fact = event()
    old = deepcopy(fact)
    meta = {"sz002281": {"code": "002281", "name": "Example"}}
    dates = pd.to_datetime(["2026-11-18", "2026-11-19"])
    reviews = [{"mode": "append", "facts": [fact]}]
    actions = reviewed_actions([], dates, reviews, meta)
    assert actions[0]["cash_pay_date"] == "2026-11-19"
    assert actions[0]["cash_available_date"] is None
    assert actions[0]["cash_per_share"] == 0.37
    assert actions[0]["source_evidence"]["pages"] == [2]
    dates = pd.to_datetime(["2026-11-18", "2026-11-19", "2026-11-20"])
    extended = reviewed_actions(actions, dates, [], meta)
    assert extended[0]["cash_available_date"] == "2026-11-20"
    assert actions[0]["cash_available_date"] is None
    assert fact == old
    with pytest.raises(ValueError, match="duplicate"):
        reviewed_actions(actions, dates, reviews, meta)


@pytest.mark.parametrize(
    "field,value",
    [
        ("notice_date", "2026-11-21"),
        ("cash_pay_date", "2026-11-18"),
        ("record_date", "2026-11-20"),
        ("cash_per_share", -0.1),
        ("share_multiplier", 0),
    ],
)
def test_unavailable_notice_or_invalid_entitlement_is_rejected(field, value):
    fact = event()
    fact[field] = value
    with pytest.raises(ValueError, match="Invalid"):
        reviewed_actions(
            [],
            pd.to_datetime(["2026-11-19"]),
            [{"mode": "append", "facts": [fact]}],
            {"sz002281": {"code": "002281", "name": "Example"}},
        )


@pytest.mark.parametrize(
    "fault", [None, "price_revision", "changed_response", "duplicate", "unfinished"]
)
def test_reviewed_collections_preserve_missing_tail_and_reject_unregistered_changes(
    tmp_path, fault
):
    symbol = "sh600001"
    meta = {"symbol": symbol, "code": "600001"}
    values = [10, 10.1, 10.2, 9.9, 1000, 1000000, 3, 1, 0.1, 0.2]
    raw = pd.DataFrame([values], columns=COLUMNS, index=pd.to_datetime(["2026-11-17"]), dtype=float)
    payload = {
        "data": {
            "code": "600001",
            "market": 1,
            "klines": [
                day + "," + ",".join(map(str, values))
                for day in ("2026-11-17", "2026-11-18", "2026-11-19")
            ],
        }
    }
    if fault == "price_revision":
        raw.at[pd.Timestamp("2026-11-17"), "open"] = 9.9
    parent = {
        "meta": {symbol: meta},
        "raws": {symbol: raw},
        "calendar": pd.to_datetime(["2026-11-17", "2026-11-18"]),
    }
    folder = tmp_path / "data/raw/collection"
    folder.mkdir(parents=True)
    response = json.dumps(payload).encode()
    (folder / f"{symbol}.json").write_bytes(response)
    save(
        folder / "protocol.json",
        {
            "source_bundle_sha256": "source",
            "requested_completed_date": "2026-11-19",
            "historical_overlap_start": "2026-11-17",
            "adjustment": "unadjusted",
        },
    )
    audit = inspect_bars(response, meta, "2026-11-17", "2026-11-19", raw)
    save(
        folder / "collection.json",
        {
            "status": "RUNNING" if fault == "unfinished" else "COMPLETE",
            "requested_completed_date": "2026-11-19",
            "records": [
                {"symbol": symbol, "response_sha256": sha(folder / f"{symbol}.json"), **audit}
            ],
        },
    )
    protocol = {
        "collection_source_sha256": "source",
        "completed_date": "2026-11-19",
        "approved_overlap_fields": ["turnover", "amount", "amplitude", "pct"],
        "collections": [
            {
                "path": "data/raw/collection",
                "protocol_sha256": sha(folder / "protocol.json"),
                "collection_sha256": sha(folder / "collection.json"),
            }
        ],
    }
    if fault == "changed_response":
        (folder / f"{symbol}.json").write_bytes(response + b" ")
    if fault == "duplicate":
        protocol["collections"] *= 2
    if fault:
        with pytest.raises(ValueError):
            _reviewed_raws(Paths(tmp_path), protocol, parent, parent)
    else:
        updated, _ = _reviewed_raws(Paths(tmp_path), protocol, parent, parent)
        assert list(updated[symbol].index.strftime("%Y-%m-%d")) == ["2026-11-17", "2026-11-19"]
        pd.testing.assert_frame_equal(updated[symbol].iloc[:1], raw, check_exact=True)


def test_interrupted_collection_needs_verified_retries_to_cover_the_entire_universe(tmp_path):
    symbols = ["sh600001", "sh600002", "sh600003"]
    values = [10, 10.1, 10.2, 9.9, 1000, 1000000, 3, 1, 0.1, 0.2]
    raw = pd.DataFrame([values], columns=COLUMNS, index=pd.to_datetime(["2026-11-18"]), dtype=float)
    parent = {
        "meta": {s: {"symbol": s, "code": s[2:]} for s in symbols},
        "raws": {s: raw.copy() for s in symbols},
        "calendar": raw.index,
    }
    sources = []
    for name, status, completed in (
        ("interrupted", "PARTIAL", symbols[:1]),
        ("retry", "COMPLETE", symbols[1:]),
    ):
        folder = tmp_path / "data/raw" / name
        folder.mkdir(parents=True)
        save(
            folder / "protocol.json",
            {
                "source_bundle_sha256": "source",
                "requested_completed_date": "2026-11-19",
                "historical_overlap_start": "2026-11-18",
                "adjustment": "unadjusted",
                "symbols": symbols if status == "PARTIAL" else completed,
            },
        )
        records = []
        for symbol in completed:
            response = json.dumps(
                {
                    "data": {
                        "code": symbol[2:],
                        "market": 1,
                        "klines": [
                            day + "," + ",".join(map(str, values))
                            for day in ("2026-11-18", "2026-11-19")
                        ],
                    }
                }
            ).encode()
            file = folder / f"{symbol}.json"
            file.write_bytes(response)
            records.append(
                {
                    "symbol": symbol,
                    "response_sha256": sha(file),
                    **inspect_bars(
                        response, parent["meta"][symbol], "2026-11-18", "2026-11-19", raw
                    ),
                }
            )
        if status == "PARTIAL":
            records.append({"symbol": symbols[1], "status": "FAILED", "error": "HTTP 403"})
        save(
            folder / "collection.json",
            {
                "status": status,
                "requested_completed_date": "2026-11-19",
                "records": records,
            },
        )
        sources.append(
            {
                "path": f"data/raw/{name}",
                "protocol_sha256": sha(folder / "protocol.json"),
                "collection_sha256": sha(folder / "collection.json"),
            }
        )
    protocol = {
        "collection_source_sha256": "source",
        "completed_date": "2026-11-19",
        "approved_overlap_fields": [],
        "collections": sources[:1],
    }
    with pytest.raises(ValueError):
        _reviewed_raws(Paths(tmp_path), protocol, parent, parent)
    protocol["collections"] = sources
    updated, records = _reviewed_raws(Paths(tmp_path), protocol, parent, parent)
    assert set(updated) == set(symbols) and len(records) == 3
    for symbol in symbols:
        pd.testing.assert_frame_equal(updated[symbol].iloc[:1], raw, check_exact=True)
        assert list(updated[symbol].index.strftime("%Y-%m-%d")) == ["2026-11-18", "2026-11-19"]
    for source in sources:
        assert sha(tmp_path / source["path"] / "collection.json") == source["collection_sha256"]
