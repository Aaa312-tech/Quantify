"""Accepted date facts must preserve current entitlements and prior provenance."""

from copy import deepcopy

import pandas as pd
import pytest

from quantfolio.snapshots import date_supplement_facts, reviewed_distribution_actions
from quantfolio.storage import digest


def example():
    basis = {
        "symbol": "sh600001",
        "code": "600001",
        "id": "original-event",
        "record_date": "2024-03-28",
        "ex_date": "2024-03-29",
        "cash_per_share": 0.2,
        "share_multiplier": 1.4,
        "bonus_per_share": 0.0,
        "capitalization_per_share": 0.4,
        "cash_pay_date": None,
        "cash_available_date": None,
        "share_list_date": None,
    }
    current = dict(basis, source_note="preserved current evidence")
    fact = {
        "symbol": basis["symbol"],
        "ex_date": basis["ex_date"],
        "original_action_sha256": digest(basis),
        "status": "VERIFIED_FIELDS",
        "verified_fields": {"cash_pay_date": "2024-03-29", "share_list_date": "2024-04-02"},
        "method": "LABELLED_PRIMARY_AND_STRUCTURED",
        "source": {"path": "filing.pdf", "sha256": "primary"},
        "evidence_pages": [1, 2],
        "known_at": "2024-03-22T00:00:00+08:00",
    }
    addition = {
        "symbol": basis["symbol"],
        "ex_date": basis["ex_date"],
        "passed": True,
        "added_fields": deepcopy(fact["verified_fields"]),
    }
    return basis, current, fact, addition


def bind(basis, current, fact, addition):
    return date_supplement_facts(
        [current], [basis], [fact], [addition], {"review_sha256": "review"}
    )


def test_date_supplement_keeps_current_identity_and_existing_provenance():
    basis, current, fact, addition = example()
    current["distribution_review"] = {"method": "previous", "fields": ["cash_rounding"]}
    current["distribution_date_reviews"] = [{"source": "previous-date-proof"}]
    before = deepcopy(current)
    facts = bind(basis, current, fact, addition)
    assert facts[0]["original_action_sha256"] == digest(current)
    assert facts[0]["date_basis"]["reviewed_action_sha256"] == digest(basis)
    updated, changes = reviewed_distribution_actions(
        [current],
        pd.to_datetime(["2024-03-28", "2024-03-29", "2024-04-01", "2024-04-02"]),
        facts,
        supplement=True,
    )
    assert current == before
    assert updated[0]["cash_available_date"] == "2024-04-01"
    assert updated[0]["share_list_date"] == "2024-04-02"
    assert updated[0]["distribution_review"] == before["distribution_review"]
    assert updated[0]["distribution_date_reviews"][:-1] == before["distribution_date_reviews"]
    assert updated[0]["source_note"] == before["source_note"]
    assert updated[0]["cash_per_share"] == 0.2 and updated[0]["share_multiplier"] == 1.4
    assert len(changes) == 1 and set(changes[0]["fields"]) == {"cash_pay_date", "share_list_date"}


def test_already_known_fields_do_not_rewrite_an_action_or_its_availability():
    basis, current, fact, addition = example()
    current.update(fact["verified_fields"], cash_available_date="2024-04-01")
    assert bind(basis, current, fact, addition) == []


@pytest.mark.parametrize("field", ["cash_pay_date", "share_list_date"])
def test_existing_date_conflicts_are_not_overwritten(field):
    basis, current, fact, addition = example()
    current[field] = "2024-04-03"
    with pytest.raises(ValueError, match="conflict"):
        bind(basis, current, fact, addition)


@pytest.mark.parametrize(
    "field,value",
    [
        ("cash_per_share", 0.3),
        ("share_multiplier", 1.5),
        ("record_date", "2024-03-27"),
        ("id", "different-event"),
    ],
)
def test_old_review_cannot_be_rebound_after_economic_identity_changes(field, value):
    basis, current, fact, addition = example()
    current[field] = value
    with pytest.raises(ValueError, match="economic|identity"):
        bind(basis, current, fact, addition)


@pytest.mark.parametrize(
    "problem",
    [
        "wrong_basis_hash",
        "unverified",
        "unreviewed_value",
        "cash_amount",
        "before_ex_date",
        "duplicate",
    ],
)
def test_independent_addition_must_match_the_original_accepted_fact(problem):
    basis, current, fact, addition = example()
    if problem == "wrong_basis_hash":
        fact["original_action_sha256"] = "different"
    elif problem == "unverified":
        addition["passed"] = False
    elif problem == "unreviewed_value":
        addition["added_fields"]["cash_pay_date"] = "2024-04-01"
    elif problem == "cash_amount":
        addition["added_fields"]["cash_per_share"] = 0.3
        fact["verified_fields"]["cash_per_share"] = 0.3
    elif problem == "before_ex_date":
        addition["added_fields"]["cash_pay_date"] = "2024-03-28"
        fact["verified_fields"]["cash_pay_date"] = "2024-03-28"
    additions = [addition, addition] if problem == "duplicate" else [addition]
    with pytest.raises(ValueError):
        date_supplement_facts([current], [basis], [fact], additions, {})


def test_inventory_drift_is_rejected_even_outside_the_supplemented_event():
    basis, current, fact, addition = example()
    with pytest.raises(ValueError, match="inventory"):
        date_supplement_facts(
            [current, dict(current, symbol="sh600002")], [basis], [fact], [addition], {}
        )


def test_share_only_supplement_preserves_existing_cash_availability():
    basis, current, fact, addition = example()
    current.update(cash_pay_date="2024-03-29", cash_available_date="2024-04-01")
    facts = bind(basis, current, fact, addition)
    assert facts[0]["verified_fields"] == {"share_list_date": "2024-04-02"}
    updated, _ = reviewed_distribution_actions(
        [current],
        pd.to_datetime(["2024-03-28", "2024-03-29", "2024-04-01", "2024-04-02"]),
        facts,
        supplement=True,
    )
    assert updated[0]["cash_available_date"] == current["cash_available_date"]
