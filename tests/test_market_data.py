"""Collected bars stay outside the active snapshot until timing and revisions are reviewed."""

import json

import pandas as pd
import pytest

from quantfolio.market_data import COLUMNS, inspect_bars


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
