"""Checkpoint all received issuer contexts; field acceptance remains pending."""

from pathlib import Path

from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd()
pointer_path = root / ".git/cash_entitlement_primary_progress.json"
pointer = read(pointer_path)
previous_path = root / pointer["progress_path"]
require(sha(previous_path) == pointer["sha256"], "Previous progress changed")
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
observations = [read(root / path) for path in previous["observations"]]
for observation in observations:
    for page in observation["page_observations"]:
        require(page["whole_page_visually_inspected"], "Primary page not observed")
        require(sha(root / page["image"]) == page["image_sha256"], page["image"])
issuers = {path.stem: read(path) for path in sorted((base / "issuer_checks").glob("*.json"))}
inventory = read(base / "related_source_inventory.json")
require(set(issuers) == {item["symbol"] for item in observations}, "Incomplete issuer inventory")
related_images = {}
reviewed_ids = []
for symbol, item in issuers.items():
    require(
        item["status"] in {"INDEPENDENT_ISSUER_CHECKS_PASS", "ISSUER_CONTEXT_REVIEW_COMPLETE"},
        symbol,
    )
    require(
        not item["failures"] and not item["facts_accepted"] and not item["runtime_inputs_changed"],
        symbol,
    )
    received = [row for row in inventory["records"] if symbol in row["symbols"]]
    decisions = item["related_decisions"]
    require({row["id"] for row in received} == {row["source_id"] for row in decisions}, symbol)
    reviewed_ids.extend(row["source_id"] for row in decisions)
    for row in received:
        for key in ("source", "receipt"):
            require(sha(root / row[key]["path"]) == row[key]["sha256"], row[key]["path"])
    visual = item["related_visual_review"]
    if isinstance(visual, dict):
        rendering = read(base / "renderings" / f"{visual['source_id']}.json")
        for page in rendering["pages"]:
            if page["page"] in visual["physical_pages"]:
                related_images[page["image"]] = page["sha256"]
    else:
        for page in visual:
            require(page["whole_page_visually_inspected"] is True, "Unreviewed image")
            related_images[page["image"]] = page["image_sha256"]
    for anomaly in item.get("source_anomalies", []):
        for page in anomaly.get("visual_review", []):
            require(page["whole_page_visually_inspected"] is True, "Unreviewed anomaly page")
            related_images[page["image"]] = page["image_sha256"]
for path, expected in related_images.items():
    require(sha(root / path) == expected, path)
require(len(issuers) == 20 and len(observations) == 31, "Registered scope changed")
require(len(reviewed_ids) == len(set(reviewed_ids)) == 110, "Duplicate or missing document")
require(set(reviewed_ids) == {row["id"] for row in inventory["records"]}, "Inventory coverage")
require(len(related_images) == 39, "Actual related image count changed")
require(sum(len(row["pages"]) for row in inventory["records"]) == 381, "Full-text page count")

progress = dict(previous)
progress.update(
    status="ISSUER_CONTEXT_COMPLETE_FIELD_ACCEPTANCE_PENDING",
    recorded_at=now(),
    preceding_progress={"path": pointer["progress_path"], "sha256": pointer["sha256"]},
    progress_writer_sha256=sha(Path(__file__)),
    related_visual_evidence=related_images,
    issuer_context={
        "issuers_with_recorded_closure": len(issuers),
        "scoped_events_in_closed_issuers": len(observations),
        "received_documents_with_recorded_full_text_decisions": len(reviewed_ids),
        "received_documents_total": len(inventory["records"]),
        "remaining_issuers": [],
        "remaining_scoped_events": [],
        "all_issuer_context_complete": True,
    },
    received_related_archive={
        **previous["received_related_archive"],
        "all_documents_semantically_reviewed": True,
        "full_text_pages_actually_read": 381,
        "archive_completeness_certified": False,
        "exact_publication_time_certified": False,
    },
    issuer_context_checks={
        "additional_related_and_context_checks": sum(
            len(item.get("checks", {})) for item in issuers.values()
        ),
        "context_check_failures": 0,
        "initial_primary_failures_are_not_cleared_by_context_closure": True,
        "source_anomaly_records": {
            symbol: item["source_anomalies"]
            for symbol, item in issuers.items()
            if item.get("source_anomalies")
        },
    },
    evidence={
        path.relative_to(root).as_posix(): sha(path)
        for path in sorted(base.rglob("*"))
        if path.is_file() and path.suffix in {".json", ".py"}
    },
    prior_goal_turn_classification=previous["this_goal_turn_classification"],
    this_goal_turn_classification=(
        "progress: final four issuer contexts closed, 24 received document decisions and "
        "105 additional independent reader/phrase checks; all 110 received documents "
        "across 381 pages have full-text decisions. Existing failures and source conflicts remain."
    ),
    next_step=(
        "Record supported or unresolved decisions for every scoped field; create the manual "
        "supplement and compatible source protocol, verify all 1839 event decisions and "
        "preserve all 1448 prior supported fields. Publish accepted source evidence before "
        "separately registering economic integration, feature/rank/label rebuilding, full "
        "retraining and fixed-forecast/account/paper comparisons."
    ),
)
require(
    progress["new_facts_accepted"] == 0 and not progress["runtime_inputs_changed"],
    "Source boundary",
)
require(not progress["whole_goal_complete"], "Whole goal remains incomplete")
output = base / "issuer_context_complete.json"
save(output, progress, immutable=True)
save(pointer_path, {"progress_path": output.relative_to(root).as_posix(), "sha256": sha(output)})
print(progress["status"], progress["issuer_context"])
print("CONTEXT_CHECKS", progress["issuer_context_checks"]["additional_related_and_context_checks"])
print("ACTIVE_STATE_UNCHANGED", "SOURCE_FACTS_ACCEPTED", 0)
