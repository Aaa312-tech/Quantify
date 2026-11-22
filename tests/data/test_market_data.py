"""Collected bars stay outside the active snapshot until timing and revisions are reviewed."""

import json
from io import BytesIO

import numpy as np
import pandas as pd
import pytest

from quantfolio import market_data
from quantfolio.market_data import COLUMNS, append_bars, fetch_bars, inspect_bars, parse_bars
from quantfolio.settings import Paths
from quantfolio.storage import read, save, sha


def sample():
    values = [10, 10.1, 10.2, 9.9, 1000, 1000000, 3.0, 1.0, 0.1, 0.2]
    existing = pd.DataFrame(
        [values], index=pd.to_datetime(["2026-11-18"]), columns=COLUMNS, dtype=float
    )
    payload = {
        "data": {
            "code": "600001",
            "market": 1,
            "klines": [
                date + "," + ",".join(map(str, values)) for date in ("2026-11-18", "2026-11-19")
            ],
        }
    }
    meta = {"symbol": "sh600001", "code": "600001"}
    return payload, meta, existing


def check(payload, meta, existing):
    return inspect_bars(json.dumps(payload), meta, "2026-11-18", "2026-11-19", existing)


def test_completed_bars_and_unmodified_overlap():
    payload, meta, existing = sample()
    result = check(payload, meta, existing)
    assert result["status"] == "CONSISTENT"
    assert result["overlap_dates"] == 1
    assert result["target_present"]


def test_missing_target_and_revised_history_are_flagged():
    payload, meta, existing = sample()
    payload["data"]["klines"].pop()
    assert check(payload, meta, existing)["status"] == "REVIEW"
    payload, meta, existing = sample()
    existing.at[pd.Timestamp("2026-11-18"), "open"] = 9.8
    result = check(payload, meta, existing)
    assert result["status"] == "REVIEW"
    assert result["historical_revisions"][0]["field"] == "open"


def test_append_preserves_revised_and_missing_history_and_integer_volume():
    payload, meta, existing = sample()
    existing["volume_lots"] = existing.volume_lots.astype("int64")
    existing.iloc[0, existing.columns.get_loc("turnover")] = np.nan
    old = existing.copy(deep=True)
    collected = parse_bars(json.dumps(payload), meta, "2026-11-18", "2026-11-19")
    result = append_bars(existing, collected, "2026-11-19")
    pd.testing.assert_frame_equal(result.iloc[:-1], old, check_exact=True)
    assert result.volume_lots.dtype == existing.volume_lots.dtype
    assert result.iloc[-1].turnover == 0.2
    assert pd.isna(result.iloc[0].turnover)
    collected.iloc[-1, collected.columns.get_loc("volume_lots")] = 1000.5
    with pytest.raises(ValueError, match="precision"):
        append_bars(existing, collected, "2026-11-19")


@pytest.mark.parametrize("fault", ["security", "future", "duplicate", "ohlc", "schema"])
def test_malformed_provider_response_is_rejected(fault):
    payload, meta, existing = sample()
    rows = payload["data"]["klines"]
    if fault == "security":
        payload["data"]["code"] = "600002"
    elif fault == "future":
        rows[-1] = rows[-1].replace("2026-11-19", "2026-11-20")
    elif fault == "duplicate":
        rows.append(rows[-1])
    elif fault == "ohlc":
        rows[-1] = rows[-1].replace(",10.2,", ",9.0,")
    else:
        rows[-1] += ",0"
    with pytest.raises(ValueError):
        check(payload, meta, existing)


def test_retry_only_missing_requests_and_preserves_the_original(tmp_path, monkeypatch):
    payload, meta, existing = sample()
    paths = Paths(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/project.toml").write_text('bundle = "bundle.joblib"\n', encoding="utf-8")
    (tmp_path / "bundle.joblib").write_bytes(b"fixture")
    bundle = {
        "calendar": existing.index,
        "meta": {meta["symbol"]: meta, "sh600002": {"symbol": "sh600002", "code": "600002"}},
        "raws": {meta["symbol"]: existing},
    }
    previous = tmp_path / "data/raw/first"
    save(
        previous / "protocol.json",
        {"source_bundle_sha256": sha(paths.bundle), "requested_completed_date": "2026-11-19"},
    )
    save(
        previous / "collection.json",
        {
            "requested_completed_date": "2026-11-19",
            "records": [
                {"symbol": "sh600001", "status": "FAILED"},
                {"symbol": "sh600002", "status": "CONSISTENT"},
            ],
        },
    )
    before = sha(previous / "collection.json")
    requests = []

    def response(request, **kwargs):
        requests.append(request.full_url)
        return BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(market_data, "load_bundle", lambda paths: bundle)
    monkeypatch.setattr(market_data, "now", lambda: "2026-11-20T10:00:00+08:00")
    monkeypatch.setattr(market_data.urllib.request, "urlopen", response)
    monkeypatch.setattr(market_data.time, "sleep", lambda _: None)
    result = fetch_bars(paths, "2026-11-19", "data/raw/retry", "data/raw/first")
    assert result["instruments_requested"] == result["consistent"] == 1
    assert len(requests) == 1 and "600001" in requests[0]
    assert sha(previous / "collection.json") == before
    registration = read(tmp_path / "data/raw/retry/protocol.json")
    assert registration["symbols"] == ["sh600001"]
    assert registration["retry_source"]["collection_sha256"] == before
    with pytest.raises(ValueError, match="No missing"):
        fetch_bars(paths, "2026-11-19", "data/raw/third", "data/raw/retry")
    assert not (tmp_path / "data/raw/third").exists()
