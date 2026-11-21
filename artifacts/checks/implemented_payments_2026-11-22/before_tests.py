"""Date supplements bind the successful field review for their source phase."""

from copy import deepcopy

import pytest

from quantfolio.snapshots import accepted_date_records


PHASES = ("gross_cash_wording", "implementation_payout_scope")


def example(phase="gross_cash_wording"):
    protocol = {
        "review_sha256": "review",
        "review_protocol": {"sha256": "protocol"},
        "independent_review": {"sha256": "accepted"},
        "source_bundle_sha256": "current",
        "expected_changes": {"cash_pay_date": 1},
        "verified_additions": {"cash_pay_date": 2, "events": 2},
        "supplement_fields": ["cash_pay_date"],
    }
    acceptance = {
        "status": "PASS_SOURCE_ONLY",
        "protocol_sha256": "protocol",
        "facts_applied": False,
        "prior_supported_events_preserved": 1,
        "prior_supported_fields_preserved": 2,
        "independent_field_checks": 8,
        "evidence_sha256": {
            "source_review.json": "review",
            "independent_closure.json": "failed",
            "field_acceptance.json": "accepted",
        },
        "active_state": {"bundle_sha256": "current"},
        "active_missing_payment_dates_supported": 1,
        "already_present_matching_dates": 1,
        "added_payment_dates": 2,
    }
    independent = {
        "status": "PASS",
        "source_review_sha256": "review",
        "failed_events": 0,
        "events": 2,
        "field_checks": 8,
        "preserved_fields": [
            ["sh600000", "2024-03-29", "cash_pay_date", "2024-03-29"],
            ["sh600000", "2024-03-29", "share_list_date", "2024-04-01"],
        ],
        "records": [
            {
                "symbol": symbol,
                "ex_date": "2024-03-29",
                "added_fields": {"cash_pay_date": "2024-03-29"},
                "passed": True,
                "checks": {
                    field: {"passed": True}
                    for field in (
                        "issuer",
                        "title",
                        "record_date",
                        "ex_date",
                        "cash_pay_date",
                        "cash_per_share",
                    )
                },
            }
            for symbol in ("sh600000", "sz000001")
        ],
    }
    if phase == "implementation_payout_scope":
        protocol["source_page_order"] = {"sha256": "page-order"}
        acceptance["page_order_protocol_sha256"] = "page-order"
        acceptance["evidence_sha256"].pop("field_acceptance.json")
        acceptance["evidence_sha256"]["independent_closure.json"] = "accepted"
    return protocol, {"phase": phase}, acceptance, independent


@pytest.mark.parametrize("phase", PHASES)
def test_accepted_field_review_preserves_records_and_inputs(phase):
    args = example(phase)
    before = deepcopy(args)
    assert accepted_date_records(*args) == args[-1]["records"]
    assert args == before


@pytest.mark.parametrize(
    "section,key,value",
    [
        (2, "status", "FAIL"),
        (2, "protocol_sha256", "other"),
        (2, "facts_applied", True),
        (2, "independent_field_checks", 7),
        (2, "prior_supported_events_preserved", 2),
        (2, "prior_supported_fields_preserved", 1),
        (2, "active_state", {"bundle_sha256": "other"}),
        (2, "active_missing_payment_dates_supported", 2),
        (2, "already_present_matching_dates", 0),
        (2, "added_payment_dates", 3),
        (3, "status", "FAIL"),
        (3, "source_review_sha256", "other"),
        (3, "failed_events", 1),
        (3, "events", 3),
        (3, "field_checks", 7),
        (3, "preserved_fields", []),
        (3, "records", []),
        (0, "supplement_fields", []),
        (0, "supplement_fields", ["cash_per_share"]),
    ],
)
@pytest.mark.parametrize("phase", PHASES)
def test_rejects_wrong_receipts_and_incomplete_evidence(section, key, value, phase):
    args = example(phase)
    args[section][key] = value
    with pytest.raises(ValueError):
        accepted_date_records(*args)


@pytest.mark.parametrize(
    "field", ["issuer", "title", "record_date", "ex_date", "cash_pay_date", "cash_per_share"]
)
@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("phase", PHASES)
def test_every_record_requires_complete_passing_checks(field, missing, phase):
    args = example(phase)
    checks = args[-1]["records"][0]["checks"]
    if missing:
        checks.pop(field)
    else:
        checks[field]["passed"] = False
    with pytest.raises(ValueError):
        accepted_date_records(*args)


@pytest.mark.parametrize("inventory", ["records", "preserved_fields"])
@pytest.mark.parametrize("phase", PHASES)
def test_duplicate_evidence_cannot_satisfy_counts(inventory, phase):
    args = example(phase)
    args[-1][inventory][1] = deepcopy(args[-1][inventory][0])
    with pytest.raises(ValueError):
        accepted_date_records(*args)


@pytest.mark.parametrize("phase", PHASES)
def test_failed_row_cannot_be_hidden_by_summary(phase):
    args = example(phase)
    args[-1]["records"][0]["passed"] = False
    with pytest.raises(ValueError):
        accepted_date_records(*args)


def test_wording_receipt_cannot_use_the_companion_phase():
    args = example()
    args[1]["phase"] = "source_candidate_roles"
    with pytest.raises((ValueError, KeyError)):
        accepted_date_records(*args)


@pytest.mark.parametrize("phase", PHASES)
def test_each_phase_requires_its_successful_receipt_file(phase):
    args = example(phase)
    wrong_file = (
        "field_acceptance.json"
        if phase == "implementation_payout_scope"
        else "independent_closure.json"
    )
    args[2]["evidence_sha256"] = {
        "source_review.json": "review",
        wrong_file: "accepted",
    }
    with pytest.raises(ValueError):
        accepted_date_records(*args)


@pytest.mark.parametrize("reference", [None, {}, {"sha256": "wrong"}])
def test_implementation_receipt_requires_the_registered_page_order(reference):
    args = example("implementation_payout_scope")
    if reference is None:
        args[0].pop("source_page_order")
    else:
        args[0]["source_page_order"] = reference
    with pytest.raises(ValueError):
        accepted_date_records(*args)


@pytest.mark.parametrize("digest", [None, "wrong"])
def test_implementation_acceptance_must_bind_page_order(digest):
    args = example("implementation_payout_scope")
    if digest is None:
        args[2].pop("page_order_protocol_sha256")
    else:
        args[2]["page_order_protocol_sha256"] = digest
    with pytest.raises(ValueError):
        accepted_date_records(*args)
