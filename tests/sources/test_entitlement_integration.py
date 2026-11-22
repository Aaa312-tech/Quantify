"""Recipient cash needs field acceptance and an unchanged target action identity."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from quantfolio import distributions, snapshots
from quantfolio.storage import digest, save, sha


def example():
    action = {
        "symbol": "sh600989",
        "id": "original-event",
        "ex_date": "2024-07-24",
        "record_date": "2024-07-23",
        "share_multiplier": 1.0,
        "cash_per_share": 0.27999999999999997,
        "cash_pay_date": None,
        "cash_available_date": None,
    }
    other = dict(action, symbol="sh600001", id="other-event")
    fields = {
        "cash_per_share": "0.3158",
        "cash_pay_date": "2024-07-24",
        "ex_reference_cash_per_share": "0.28",
    }
    field_review = {"path": "decisions.json", "sha256": "decisions"}
    fact = {
        "symbol": action["symbol"],
        "ex_date": action["ex_date"],
        "original_action_sha256": digest(action),
        "status": "VERIFIED_FIELDS",
        "verified_fields": fields,
        "method": "MANUAL_PRIMARY_REVIEW",
        "source": {"path": "filing.pdf", "sha256": "filing", "field_review": field_review},
        "evidence_pages": [1, 2],
        "known_at": "2024-07-20T00:00:00+08:00",
    }
    basis = [action, other]
    review = {
        "original_actions_sha256": digest(basis),
        "facts": [fact],
        "records": [
            fact,
            {
                "symbol": other["symbol"],
                "ex_date": other["ex_date"],
                "original_action_sha256": digest(other),
                "status": "UNVERIFIED",
                "verified_fields": {},
            },
        ],
    }
    evidence = {"primary_observation": {"path": "observation.json", "sha256": "observation"}}
    unresolved = {"status": "SOURCE_AGGREGATE_DISCREPANCY_UNRESOLVED"}
    record = {
        **{
            name: deepcopy(fact[name])
            for name in ("symbol", "ex_date", "original_action_sha256", "verified_fields")
        },
        "field_evidence": evidence,
        "preserve_original_cash_binary64_at_integration": False,
        "initial_failed_checks_retained": ["aggregate"],
        "unresolved_primary_aggregate": unresolved,
    }
    decision = {
        "symbol": action["symbol"],
        "ex_date": action["ex_date"],
        "evidence": evidence,
        "control_fields": {
            "record_date": action["record_date"],
            "ex_date": action["ex_date"],
            "share_multiplier": "1",
        },
        "field_decisions": {
            name: {
                "status": "SUPPORTED_PRIMARY_FIELD" if name in fields else "NOT_ESTABLISHED",
                "value": fields.get(name),
            }
            for name in distributions.FIELDS
        },
        "initial_failed_checks_retained": ["aggregate"],
        "unresolved_primary_aggregate": unresolved,
    }
    current = deepcopy(basis)
    current[1]["distribution_date_reviews"] = [{"old": "retain"}]
    return current, basis, review, [record], [decision], {"field_decisions": field_review}


def test_cash_reference_and_available_opening_have_distinct_roles():
    args = example()
    before = deepcopy(args)
    facts = snapshots.entitlement_facts(*args)
    result, changes = snapshots.reviewed_distribution_actions(
        args[0], pd.to_datetime(["2024-07-23", "2024-07-24", "2024-07-25"]), facts
    )
    assert args == before
    assert result[0]["cash_per_share"] == 0.3158
    assert result[0]["ex_reference_cash_per_share"] == 0.28
    assert result[0]["cash_available_date"] == "2024-07-25"
    assert "cash_rounding" not in result[0]
    assert result[1] == args[0][1]
    assert len(changes) == 1
    assert facts[0]["entitlement_basis"]["independent_record_sha256"] == digest(args[3][0])
    assert facts[0]["source"]["field_review"] == args[5]["field_decisions"]


@pytest.mark.parametrize("part", [0, 1, 3, 4])
def test_duplicate_inventory_entries_are_rejected(part):
    args = example()
    args[part].append(deepcopy(args[part][0]))
    with pytest.raises(ValueError):
        snapshots.entitlement_facts(*args)


@pytest.mark.parametrize("part", ["facts", "records"])
def test_review_must_cover_the_original_inventory_without_duplicates(part):
    args = example()
    args[2][part].append(deepcopy(args[2][part][0]))
    with pytest.raises(ValueError):
        snapshots.entitlement_facts(*args)


@pytest.mark.parametrize("part", [0, 1, 3, 4])
def test_missing_or_unknown_events_are_rejected(part):
    args = example()
    args[part][0]["symbol"] = "sh600999"
    with pytest.raises(ValueError):
        snapshots.entitlement_facts(*args)


@pytest.mark.parametrize(
    "field,value", [("cash_per_share", 0.3), ("id", "replaced"), ("cash_pay_date", "2024-07-25")]
)
def test_target_cannot_be_rebound_after_current_action_changes(field, value):
    args = example()
    args[0][0][field] = value
    with pytest.raises(ValueError):
        snapshots.entitlement_facts(*args)


@pytest.mark.parametrize("field", ["distribution_review", "distribution_date_reviews"])
def test_existing_target_review_cannot_be_overwritten_even_with_matching_hashes(field):
    args = example()
    for rows in args[:2]:
        rows[0][field] = {"old": "review"}
    action_sha = digest(args[1][0])
    args[2]["original_actions_sha256"] = digest(args[1])
    args[2]["facts"][0]["original_action_sha256"] = action_sha
    args[3][0]["original_action_sha256"] = action_sha
    with pytest.raises(ValueError):
        snapshots.entitlement_facts(*args)


@pytest.mark.parametrize(
    "mutation",
    [
        "omitted",
        "unaccepted",
        "extra",
        "wrong_value",
        "evidence",
        "failure",
        "aggregate",
        "cash_storage",
        "control",
    ],
)
def test_independent_field_acceptance_and_source_decisions_must_agree(mutation):
    args = example()
    record, decision = args[3][0], args[4][0]
    if mutation == "omitted":
        record["verified_fields"].pop("cash_pay_date")
    elif mutation == "unaccepted":
        decision["field_decisions"]["cash_per_share"]["status"] = "NOT_ESTABLISHED"
    elif mutation == "extra":
        record["verified_fields"]["cash_rounding"] = "FLOOR_TO_CENT"
    elif mutation == "wrong_value":
        record["verified_fields"]["cash_per_share"] = "0.28"
    elif mutation == "evidence":
        record["field_evidence"] = {}
    elif mutation == "failure":
        record["initial_failed_checks_retained"] = []
    elif mutation == "aggregate":
        record["unresolved_primary_aggregate"] = None
    elif mutation == "cash_storage":
        record["preserve_original_cash_binary64_at_integration"] = True
    else:
        decision["control_fields"]["record_date"] = "2024-07-22"
    with pytest.raises(ValueError):
        snapshots.entitlement_facts(*args)


def test_matching_decimal_cash_preserves_original_binary64():
    args = example()
    for row in (args[2]["facts"][0], args[3][0]):
        row["verified_fields"]["cash_per_share"] = "0.28"
    args[4][0]["field_decisions"]["cash_per_share"]["value"] = "0.28"
    args[3][0]["preserve_original_cash_binary64_at_integration"] = True
    facts = snapshots.entitlement_facts(*args)
    repaired, changes = snapshots.reviewed_distribution_actions(
        args[0], pd.to_datetime(["2024-07-24", "2024-07-25"]), facts
    )
    assert repaired[0]["cash_per_share"].hex() == args[0][0]["cash_per_share"].hex()
    assert "cash_per_share" not in changes[0]["fields"]


def test_registered_operation_reaches_accepted_entitlement_binding(tmp_path, monkeypatch):
    protocol_path, source = tmp_path / "protocol.json", tmp_path / "source.joblib"
    source.write_bytes(b"source")
    protocol = {
        "operation": "apply_verified_entitlements",
        "snapshot_output": "target",
        "review": "review.json",
        "source_bundle": source.name,
        "source_bundle_sha256": sha(source),
        "source_data_cutoff": "2024-07-25",
        "source_inputs": {},
    }
    save(protocol_path, protocol)
    save(
        tmp_path / "review.json",
        {"extractor_sha256": sha(Path(distributions.__file__)), "source_inputs": {}, "facts": []},
    )
    protocol["review_sha256"] = sha(tmp_path / "review.json")
    save(tmp_path / "manifest.json", {"files": {"review.json": protocol["review_sha256"]}})
    monkeypatch.setattr(distributions, "registered_inputs", lambda *a: (protocol, {}))
    monkeypatch.setattr(snapshots, "load_snapshot", lambda *a: {})

    class BindingReached(Exception):
        pass

    def bind(*args):
        raise BindingReached

    monkeypatch.setattr(snapshots, "_accepted_entitlement_facts", bind, raising=False)
    paths = SimpleNamespace(root=tmp_path, manifest=tmp_path / "manifest.json")
    with pytest.raises(BindingReached):
        snapshots.repair_distribution_snapshot(paths, tmp_path / "target", protocol_path, {})


def test_date_supplement_without_a_label_review_requires_exact_labels():
    original = {"table": pd.DataFrame({"date_index": [0], "y1": [0.02]})}
    repaired = deepcopy(original)
    snapshots._check_supplemented_labels(None, {}, {}, None, original, repaired, [])
    repaired["table"].loc[0, "y1"] += 1e-12
    with pytest.raises(AssertionError):
        snapshots._check_supplemented_labels(None, {}, {}, None, original, repaired, [])


def test_date_supplement_rejects_an_unregistered_label_review():
    protocol = {"label_review": {"path": "review.json", "sha256": "0" * 64}}
    with pytest.raises(ValueError, match="Independent label review is not frozen"):
        snapshots._check_supplemented_labels(
            None, protocol, {"files": {}}, None, {"table": pd.DataFrame()}, {}, []
        )


def receipt_example(tmp_path, monkeypatch, mutation=None):
    actions, basis, review, additions, decisions, binding = example()
    counts = {"cash_per_share": 1, "cash_pay_date": 1, "ex_reference_cash_per_share": 1}
    previous = deepcopy(review)
    previous["facts"] = []
    previous["records"][0]["status"] = "UNVERIFIED"
    previous["records"][0]["verified_fields"] = {}
    manifest = {"files": {}}

    def freeze(name, value):
        save(tmp_path / name, value)
        manifest["files"][name] = sha(tmp_path / name)
        return {"path": name, "sha256": manifest["files"][name]}

    source_basis = freeze("basis.json", {"actions": basis})
    context = {
        "protocol_sha256": "evidence",
        "issuer_context": {"all_issuer_context_complete": True},
        "issuer_context_checks": {"context_check_failures": 0},
    }
    decision_receipt = {
        "context_completion": freeze("context.json", context),
        "protocol": {"path": "evidence.json", "sha256": "evidence"},
        "events": 1,
        "supported_field_counts": counts,
        "supported_fields": 3,
        "records": decisions,
    }
    decisions_ref = freeze("decisions.json", decision_receipt)
    review["facts"][0]["source"]["field_review"] = decisions_ref
    protocol = {
        "source_data_cutoff": "2024-07-25",
        "source_bundle_sha256": "current",
        "source_inputs": {},
        "review": "review.json",
        "review_basis": source_basis,
        "field_decisions": decisions_ref,
        "source_context": decision_receipt["context_completion"],
        "entitlement_fields": sorted(distributions.FIELDS - {"cash_rounding"}),
        "verified_additions": {**counts, "events": 1},
    }
    registered = {
        "phase": "recipient_and_adjusted_entitlements",
        "source_bundle": source_basis["path"],
        "source_bundle_sha256": source_basis["sha256"],
        "source_data_cutoff": protocol["source_data_cutoff"],
        "review": protocol["review"],
        "source_inputs": {},
        "field_decisions": decisions_ref,
        "evidence_protocol": decision_receipt["protocol"],
        "previous_review": freeze("previous.json", previous),
    }
    protocol["review_protocol"] = freeze("registered.json", registered)
    review.update(
        protocol_sha256=protocol["review_protocol"]["sha256"],
        data_snapshot_sha256=source_basis["sha256"],
    )
    protocol["review_sha256"] = freeze("review.json", review)["sha256"]
    independent = {
        "status": "FIELD_SOURCE_ACCEPTANCE_PASSED",
        "facts_applied": False,
        "protocol_sha256": protocol["review_protocol"]["sha256"],
        "source_review_sha256": protocol["review_sha256"],
        "field_decisions_sha256": decisions_ref["sha256"],
        "previous_review_sha256": registered["previous_review"]["sha256"],
        "records": additions,
        "new_fields": counts,
        "new_field_count": 3,
        "initial_primary_failures_retained": 1,
        "unresolved_primary_aggregates": 1,
        "events_checked": 2,
        "unscoped_records_exact": 1,
        "prior_supported_fields_preserved": 0,
        # This is the sorted scoped subset; review binds the whole basis separately.
        "source_basis_actions_sha256": digest([basis[0]]),
    }
    acceptance = {
        "status": "SOURCE_REVIEW_VALIDATED_LOCALLY",
        "facts_applied": False,
        "source_review_sha256": protocol["review_sha256"],
        "production_protocol_sha256": protocol["review_protocol"]["sha256"],
        "active_state": {"bundle": {"sha256": "current"}},
        "protocol_sha256": "evidence",
        "new_supported_fields": counts,
        "initial_primary_failures_preserved": 1,
        "unresolved_primary_source_aggregates": 1,
        "events_checked": 2,
        "prior_supported_fields_preserved": 0,
    }
    if mutation:
        section, key, value = mutation
        {"acceptance": acceptance, "independent": independent, "protocol": protocol}[section][
            key
        ] = value
    protocol["independent_review"] = freeze("independent.json", independent)
    acceptance["field_acceptance_sha256"] = protocol["independent_review"]["sha256"]
    protocol["source_acceptance"] = freeze("acceptance.json", acceptance)
    monkeypatch.setattr(distributions, "registered_inputs", lambda *args: (registered, {}))
    monkeypatch.setattr(snapshots, "load_snapshot", lambda *args: {"actions": basis})
    return SimpleNamespace(root=tmp_path), protocol, review, {"actions": actions}, manifest


def test_completed_receipts_bind_scoped_digest_and_preserve_full_inventory(tmp_path, monkeypatch):
    args = receipt_example(tmp_path, monkeypatch)
    facts = snapshots._accepted_entitlement_facts(*args)
    assert len(facts) == 1
    assert facts[0]["verified_fields"]["cash_per_share"] == "0.3158"
    assert facts[0]["entitlement_basis"]["source_context"] == args[1]["source_context"]


@pytest.mark.parametrize(
    "mutation",
    [
        ("acceptance", "status", "PENDING"),
        ("acceptance", "facts_applied", True),
        ("acceptance", "source_review_sha256", "other"),
        ("acceptance", "production_protocol_sha256", "other"),
        ("acceptance", "active_state", {"bundle": {"sha256": "other"}}),
        ("acceptance", "new_supported_fields", {}),
        ("acceptance", "initial_primary_failures_preserved", 0),
        ("acceptance", "unresolved_primary_source_aggregates", 0),
        ("acceptance", "prior_supported_fields_preserved", 1),
        ("independent", "status", "FAILED"),
        ("independent", "facts_applied", True),
        ("independent", "field_decisions_sha256", "other"),
        ("independent", "previous_review_sha256", "other"),
        ("independent", "new_field_count", 2),
        ("independent", "events_checked", 1),
        ("independent", "unscoped_records_exact", 0),
        ("independent", "source_basis_actions_sha256", "other"),
        ("protocol", "source_inputs", {"unaccepted": "source"}),
        ("protocol", "verified_additions", {"events": 1}),
        ("protocol", "entitlement_fields", ["cash_rounding"]),
    ],
)
def test_incomplete_or_conflicting_acceptance_cannot_enter_runtime(tmp_path, monkeypatch, mutation):
    args = receipt_example(tmp_path, monkeypatch, mutation)
    with pytest.raises(ValueError):
        snapshots._accepted_entitlement_facts(*args)


def test_accepted_receipts_must_also_be_frozen(tmp_path, monkeypatch):
    args = receipt_example(tmp_path, monkeypatch)
    args[-1]["files"].pop("independent.json")
    with pytest.raises(ValueError, match="not frozen"):
        snapshots._accepted_entitlement_facts(*args)
