"""Date integration requires the accepted source phase and its exact evidence."""

from copy import deepcopy

import pytest

from quantfolio.snapshots import accepted_date_records


def example():
    protocol = {
        "review_sha256": "review",
        "review_protocol": {"sha256": "protocol"},
        "independent_review": {"sha256": "independent"},
        "source_bundle_sha256": "current",
        "expected_changes": {"cash_pay_date": 1},
        "verified_additions": {"cash_pay_date": 2, "events": 2},
        "supplement_fields": ["cash_pay_date"],
    }
    acceptance = {
        "status": "PASS_SOURCE_ONLY",
        "protocol_sha256": "protocol",
        "facts_applied": False,
        "prior_supported_events_preserved": 10,
        "evidence_sha256": {
            "source_review.json": "review",
            "independent_closure.json": "independent",
        },
        "active_state": {"bundle_sha256": "current"},
        "active_missing_payment_dates_supported": 1,
        "added_payment_dates": 2,
    }
    independent = {
        "status": "PASS",
        "source_review_sha256": "review",
        "facts_applied": False,
        "failed_roles": 0,
        "failed_events": 0,
        "prior_events_preserved": 10,
        "fields": [
            {"added_fields": {"cash_pay_date": "2024-03-29"}},
            {"added_fields": {"cash_pay_date": "2024-04-01"}},
        ],
    }
    return protocol, {"phase": "source_candidate_roles"}, acceptance, independent


def test_companion_receipt_preserves_records_and_inputs():
    args = example()
    before = deepcopy(args)
    assert accepted_date_records(*args) == args[-1]["fields"]
    assert args == before


@pytest.mark.parametrize(
    "section,key,value",
    [
        (2, "status", "FAILED"),
        (2, "protocol_sha256", "other"),
        (2, "facts_applied", True),
        (2, "prior_supported_events_preserved", 9),
        (2, "active_state", {"bundle_sha256": "other"}),
        (2, "active_missing_payment_dates_supported", 2),
        (2, "added_payment_dates", 3),
        (2, "evidence_sha256", {"source_review.json": "other"}),
        (3, "status", "FAIL"),
        (3, "source_review_sha256", "other"),
        (3, "facts_applied", True),
        (3, "failed_roles", 1),
        (3, "failed_events", 1),
        (3, "fields", [{"added_fields": {"share_list_date": "2024-03-29"}}]),
        (3, "fields", [{"added_fields": {"cash_pay_date": "2024-03-29"}}]),
        (0, "supplement_fields", []),
        (0, "supplement_fields", ["cash_pay_date", "cash_per_share"]),
    ],
)
def test_rejects_conflicting_phase_receipts_and_out_of_scope_dates(section, key, value):
    args = example()
    args[section][key] = value
    with pytest.raises(ValueError):
        accepted_date_records(*args)


def test_original_receipt_schema_still_requires_prior_fact_preservation():
    protocol, registered, _, independent = example()
    registered.pop("phase")
    independent["records"] = independent.pop("fields")
    acceptance = {
        "status": "SOURCE_REVIEW_ACCEPTED_WITH_UNRESOLVED_EVENTS",
        "previous_facts_preserved": True,
        "source_review_sha256": "review",
        "active_bundle_sha256": "current",
        "independent_fields": {"sha256": "independent"},
    }
    assert (
        accepted_date_records(protocol, registered, acceptance, independent)
        == independent["records"]
    )
    acceptance["previous_facts_preserved"] = False
    with pytest.raises(ValueError):
        accepted_date_records(protocol, registered, acceptance, independent)


def test_companion_receipt_cannot_be_treated_as_legacy_acceptance():
    protocol, _, acceptance, independent = example()
    with pytest.raises(ValueError):
        accepted_date_records(protocol, {}, acceptance, independent)
