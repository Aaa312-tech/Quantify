"""Reviewed extensions must retain source identities and conservative cash timing."""

from copy import deepcopy

import pandas as pd
import pytest

from quantfolio.refresh import reviewed_actions


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
