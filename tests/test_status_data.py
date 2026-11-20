"""Retrospective flags must not become inferred opening permissions or filled bars."""

import socket
import sys
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from quantfolio import status_data
from quantfolio.settings import Paths
from quantfolio.status_data import FIELDS, audit_status_history, inspect_history, parse_history
from quantfolio.storage import load_gzip, read, sha

CALENDAR = pd.to_datetime(["2026-11-17", "2026-11-18", "2026-11-19"])
SYMBOL = "sh600001"


def sample():
    return {
        "fields": list(FIELDS),
        "error_code": "0",
        "adjustment": "3",
        "rows": [
            ["2026-11-17", "sh.600001", "10", "11", "9", "10", "100", "1000", "1", "0"],
            ["2026-11-18", "sh.600001", "10", "10", "10", "10", "", "", "0", "1"],
            ["2026-11-19", "sh.600001", "10", "11", "9", "10", "200", "2000", "1", "1"],
        ],
    }


def test_suspended_empty_quantities_stay_missing_and_never_gain_a_known_at():
    payload = sample()
    original = deepcopy(payload)
    frame = parse_history(payload, SYMBOL, CALENDAR)
    assert frame.loc[CALENDAR[1], ["volume", "amount"]].isna().all()
    assert "known_at" not in frame
    assert payload == original


@pytest.mark.parametrize(
    "fault",
    ["unknown_flag", "active_missing", "identity", "adjusted", "duplicate", "future", "ohlc"],
)
def test_ambiguous_status_or_price_data_is_rejected(fault):
    payload = sample()
    if fault == "unknown_flag":
        payload["rows"][1][-1] = ""
    elif fault == "active_missing":
        payload["rows"][1][-2] = "1"
    elif fault == "identity":
        payload["rows"][0][1] = "sz.600001"
    elif fault == "adjusted":
        payload["adjustment"] = "2"
    elif fault == "duplicate":
        payload["rows"].append(payload["rows"][-1])
    elif fault == "future":
        payload["rows"][-1][0] = "2026-11-20"
    else:
        payload["rows"][0][3] = "8"
    with pytest.raises(ValueError):
        parse_history(payload, SYMBOL, CALENDAR)


def test_gap_and_st_screening_does_not_certify_unobserved_days_or_change_history():
    frame = parse_history(sample(), SYMBOL, CALENDAR)
    raw = frame.iloc[[0, 2]][["open", "high", "low", "close"]].astype(float)
    old = raw.copy(deep=True)
    eligible = pd.Series([True, False, True], index=CALENDAR)
    detail = inspect_history(frame, raw, eligible, CALENDAR)
    assert detail["gaps_flagged_suspended"] == 1
    assert detail["st_eligible_dates"] == ["2026-11-19"]
    assert detail["ohlc_mismatches"] == []
    reduced = frame.drop(CALENDAR[1])
    assert inspect_history(reduced, raw, eligible, CALENDAR)["gaps_without_status"] == 1
    frame["open"] = frame.open.astype(float)
    frame.loc[CALENDAR[2], "open"] = 10.1
    assert len(inspect_history(frame, raw, eligible, CALENDAR)["ohlc_mismatches"]) == 1
    pd.testing.assert_frame_equal(raw, old)


def test_intervals_break_at_missing_sessions():
    periods = status_data.status_periods(CALENDAR[[0, 2]], CALENDAR)
    assert len(periods) == 2
    assert all(p["sessions"] == 1 for p in periods)
    with pytest.raises(ValueError, match="ordered"):
        status_data.status_periods(CALENDAR[[1, 0]], CALENDAR)


class Response:
    def __init__(self, symbol, failed=False):
        self.error_code = "ERROR" if failed else "0"
        self.error_msg = "network failed" if failed else "success"
        self.fields = list(FIELDS)
        self.rows = sample()["rows"] if not failed else []
        for row in self.rows:
            row[1] = symbol
        self.position = -1

    def next(self):
        self.position += 1
        return self.position < len(self.rows)

    def get_row_data(self):
        return self.rows[self.position]


def fixture_provider(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/project.toml").write_text('bundle = "bundle.joblib"\n', encoding="utf-8")
    (tmp_path / "bundle.joblib").write_bytes(b"fixture")
    frame = parse_history(sample(), SYMBOL, CALENDAR)
    symbols = [SYMBOL, "sh600002"]
    bundle = {
        "calendar": CALENDAR,
        "meta": {s: {"kind": "stock", "name": s} for s in symbols},
        "raws": {s: frame.iloc[[0, 2]][["open", "high", "low", "close"]] for s in symbols},
        "panels": {
            "eligible": pd.DataFrame({s: [True, False, True] for s in symbols}, index=CALENDAR)
        },
    }
    requests = []

    def query(symbol, *_args, **_kwargs):
        requests.append(symbol)
        return Response(symbol, failed=symbol == "sh.600002" and len(requests) == 2)

    provider = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="0"),
        logout=lambda: None,
        query_history_k_data_plus=query,
    )
    monkeypatch.setitem(sys.modules, "baostock", provider)
    monkeypatch.setattr(status_data.importlib.metadata, "version", lambda _: "0.9.4")
    monkeypatch.setattr(status_data, "load_bundle", lambda _: bundle)
    monkeypatch.setattr(status_data.time, "sleep", lambda _: None)
    return paths, requests


def test_failed_batch_is_preserved_retry_only_fetches_missing_and_audit_stays_research(
    tmp_path, monkeypatch
):
    paths, requests = fixture_provider(tmp_path, monkeypatch)
    timeout = socket.getdefaulttimeout()
    first = status_data.fetch_status_history(paths, "data/raw/first")
    assert first["status"] == "PARTIAL" and first["parsed"] == 1
    frozen = sha(tmp_path / "data/raw/first/collection.json")
    second = status_data.fetch_status_history(paths, "data/raw/second", "data/raw/first")
    assert second["status"] == "COMPLETE" and second["parsed"] == 1
    assert requests == ["sh.600001", "sh.600002", "sh.600002"]
    assert sha(tmp_path / "data/raw/first/collection.json") == frozen
    assert socket.getdefaulttimeout() == timeout
    result = audit_status_history(
        paths, ["data/raw/first", "data/raw/second"], "artifacts/checks/test"
    )
    assert result["screened_securities"] == 2
    assert result["st_eligible_sessions"] == 2
    assert result["status"] == "SOURCE_SCREENED_NOT_CERTIFIED"
    assert not result["activated"] and not result["known_at_established"]
    assert len(load_gzip(tmp_path / "artifacts/checks/test/securities.json.gz")) == 2
    assert not any("opening_status" in p.name for p in tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="No missing"):
        status_data.fetch_status_history(paths, "data/raw/third", "data/raw/second")
    assert not (tmp_path / "data/raw/third").exists()


def test_tampered_response_and_overlapping_success_are_rejected(tmp_path, monkeypatch):
    paths, _ = fixture_provider(tmp_path, monkeypatch)
    status_data.fetch_status_history(paths, "data/raw/first")
    with pytest.raises(ValueError, match="Overlapping"):
        audit_status_history(
            paths, ["data/raw/first", "data/raw/first"], "artifacts/checks/overlap"
        )
    response = tmp_path / "data/raw/first/sh600001.json.gz"
    response.write_bytes(response.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        audit_status_history(paths, ["data/raw/first"], "artifacts/checks/tampered")
    assert not (tmp_path / "artifacts/checks/tampered").exists()
    assert read(tmp_path / "data/raw/first/collection.json")["status"] == "PARTIAL"
