"""Summarize completed primary reading without accepting or applying source facts."""

from pathlib import Path

from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd()
pointer_path = root / ".git/cash_entitlement_primary_progress.json"
pointer = read(pointer_path)
previous_path = root / pointer["progress_path"]
require(sha(previous_path) == pointer["sha256"], "Prior progress bytes changed")
previous = read(previous_path)
protocol_path = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_path)
require(sha(protocol_path) == previous["protocol_sha256"], "Protocol changed")
for path, expected in {**previous["observations"], **previous["evidence"]}.items():
    require(sha(root / path) == expected, path)
for item in protocol["source_inputs"].values():
    require(sha(root / item["path"]) == item["sha256"], item["path"])
state = protocol["active_state"]
require(sha(root / state["configuration"]) == state["configuration_sha256"], "Configuration")
for name in ("bundle", "plan"):
    require(sha(root / state[name]["path"]) == state[name]["sha256"], name)
for name, expected in state["source_sha256"].items():
    require(sha(root / "src/quantfolio" / name) == expected, name)

base = root / protocol["checks_output"]
files = sorted((base / "observations").glob("*.json"))
observations = [read(path) for path in files]
keys = {(item["symbol"], item["ex_date"]) for item in observations}
old_observations = [read(root / path) for path in previous["observations"]]
expected_keys = {
    (item["symbol"], item["ex_date"])
    for item in old_observations + previous["remaining_primary_observations"]
}
require(keys == expected_keys and len(keys) == len(observations) == 31, "Scope coverage")
for item in observations:
    require(item["facts_accepted"] == 0 and not item["runtime_inputs_changed"], "Boundary")
    for name in ("source", "receipt"):
        require(sha(root / item[name]["path"]) == item[name]["sha256"], item[name]["path"])
    for page in item["page_observations"]:
        require(page["whole_page_visually_inspected"] is True, "Unreviewed page")
        require(sha(root / page["image"]) == page["image_sha256"], page["image"])

issuer_paths = sorted((base / "issuer_checks").glob("*.json"))
issuers = [read(path) for path in issuer_paths]
for item in issuers:
    require(item["status"] == "INDEPENDENT_ISSUER_CHECKS_PASS", "Issuer closure")
    require(not item["failures"] and not item["facts_accepted"], "Issuer boundary")

primary = read(base / "remaining_primary_checks.json")
closure = read(base / "remaining_primary_closure.json")
adjusted = read(base / "adjusted_cash_primary_checks.json")
adjusted_closure = read(base / "adjusted_cash_arithmetic_review.json")
shareholder_path = (
    root / ".git/distribution_frontier_2026_11_22/shareholder_related_observations.json"
)
shareholder = read(shareholder_path)
evidence_paths = issuer_paths + [
    base / name
    for name in (
        "related_source_inventory.json",
        "source_text.json",
        "adjusted_cash_primary_checks.json",
        "adjusted_cash_arithmetic_review.json",
        "remaining_primary_checks.json",
        "remaining_primary_closure.json",
        "primary_tail_rendering.json",
    )
]
old_issuer_checks = sum(
    len(record["checks"]) for item in issuers for record in item.get("records", [])
)
initial_checks = old_issuer_checks + sum(
    len(record["checks"]) for item in (primary, adjusted) for record in item["records"]
)
initial_failures = len(primary["failures"]) + len(adjusted["failures"])
explained = closure["explained_failures"] + adjusted_closure["explained_check_failures"]
require(initial_checks == 436 and initial_failures == 5 and explained == 4, "Check counts")

progress = {
    "status": "PRIMARY_REVIEW_COMPLETE_PENDING_SOURCE_ACCEPTANCE",
    "recorded_at": now(),
    "protocol_sha256": sha(protocol_path),
    "preceding_progress": {"path": pointer["progress_path"], "sha256": pointer["sha256"]},
    "progress_writer_sha256": sha(Path(__file__)),
    "scope_events": len(expected_keys),
    "formal_primary_observations": len(observations),
    "full_text_pages_read_for_formal_observations": sum(
        item["full_text_pages_read"] for item in observations
    ),
    "primary_whole_page_images_actually_inspected": sum(
        len(item["page_observations"]) for item in observations
    ),
    "new_related_whole_page_images_actually_inspected": sum(
        len(item["related_visual_review"]["physical_pages"])
        if isinstance(item["related_visual_review"], dict)
        else len(item["related_visual_review"])
        for item in issuers
    ),
    "issuer_context": {
        "issuers_with_recorded_closure": len(issuers),
        "scoped_events_in_closed_issuers": sum(
            len(item.get("records", item.get("primary_events", []))) for item in issuers
        ),
        "received_documents_with_recorded_full_text_decisions": sum(
            len(item["related_decisions"]) for item in issuers
        ),
        "all_issuer_context_complete": False,
    },
    "independent_checks": {
        "initial_primary_and_prior_issuer_checks": initial_checks,
        "initial_failures_preserved": initial_failures,
        "explained_checker_failures": explained,
        "primary_source_aggregate_discrepancies_unresolved": adjusted_closure[
            "unresolved_source_aggregates"
        ],
        "additional_related_checks": sum(len(item.get("checks", {})) for item in issuers),
        "related_source_arithmetic_discrepancies": shareholder["source_arithmetic_discrepancies"],
        "related_discrepancy_evidence": {
            "path": shareholder_path.relative_to(root).as_posix(),
            "sha256": sha(shareholder_path),
            "publication_copy_pending": True,
        },
    },
    "received_related_archive": previous["received_related_archive"],
    "observations": {path.relative_to(root).as_posix(): sha(path) for path in files},
    "evidence": {path.relative_to(root).as_posix(): sha(path) for path in evidence_paths},
    "remaining_primary_observations": [],
    "active_configuration_bundle_plan_numerical_sources_and_prior_source_inputs": (
        "UNCHANGED_HASHES_VERIFIED"
    ),
    "new_facts_accepted": 0,
    "runtime_inputs_changed": False,
    "publication": "CURRENT_SOURCE_PHASE_NOT_YET_ACCEPTED_OR_PUBLISHED",
    "next_step": (
        "Finish remaining issuer contextual and amendment decisions, retain all source "
        "discrepancies, decide each scoped field and verify preservation of the entire "
        "1839-event inventory and 1448 prior supported fields before source acceptance. "
        "Economic integration and complete retraining require separate registration."
    ),
    "whole_goal_complete": False,
}
output = base / "primary_review_progress_complete.json"
save(output, progress, immutable=True)
save(pointer_path, {"progress_path": output.relative_to(root).as_posix(), "sha256": sha(output)})
print(progress["status"])
print(
    {
        key: progress[key]
        for key in (
            "formal_primary_observations",
            "full_text_pages_read_for_formal_observations",
            "primary_whole_page_images_actually_inspected",
            "new_related_whole_page_images_actually_inspected",
            "issuer_context",
            "new_facts_accepted",
            "runtime_inputs_changed",
        )
    }
)
