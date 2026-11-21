"""Checkpoint issuer context closure while preserving the source-only boundary."""

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
issuer_paths = sorted((base / "issuer_checks").glob("*.json"))
issuers = {path.stem: read(path) for path in issuer_paths}
inventory = read(base / "related_source_inventory.json")
related_images = {}
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
    require(
        {row["id"] for row in received} == {row["source_id"] for row in item["related_decisions"]},
        symbol,
    )
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
remaining = [
    {"symbol": item["symbol"], "ex_date": item["ex_date"], "source_id": item["source_id"]}
    for item in observations
    if item["symbol"] not in issuers
]
require(len(issuers) == 16 and len(remaining) == 4, "Expected context coverage changed")
require(len(related_images) == 39, "Actual related page count changed")

progress = dict(previous)
progress.update(
    status="ISSUER_CONTEXT_REVIEW_IN_PROGRESS",
    recorded_at=now(),
    preceding_progress={"path": pointer["progress_path"], "sha256": pointer["sha256"]},
    progress_writer_sha256=sha(Path(__file__)),
    new_related_whole_page_images_actually_inspected=len(related_images),
    related_visual_evidence=related_images,
    issuer_context={
        "issuers_with_recorded_closure": len(issuers),
        "scoped_events_in_closed_issuers": len(observations) - len(remaining),
        "received_documents_with_recorded_full_text_decisions": sum(
            len(item["related_decisions"]) for item in issuers.values()
        ),
        "received_documents_total": len(inventory["records"]),
        "remaining_issuers": sorted({item["symbol"] for item in remaining}),
        "remaining_scoped_events": remaining,
        "all_issuer_context_complete": False,
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
    prior_goal_turn_classification="progress: complete primary-review checkpoint recorded with active-state identity checks",
    this_goal_turn_classification=(
        "progress: eleven further issuer contexts closed, 53 received document decisions, "
        "165 independent related/context checks, 12 new whole-page inspections and "
        "12 exact prior reviewed-image imports; source discrepancies retained"
    ),
    next_step=(
        "Close the four remaining received issuer inventories, then record decisions for "
        "every scoped field and verify all 1839 events and 1448 prior supported fields. "
        "Batch source acceptance and publication precede separately registered economic "
        "integration, independent feature/label rebuilding and full retraining."
    ),
)
require(
    progress["issuer_context"]["received_documents_with_recorded_full_text_decisions"] == 86,
    "Document count",
)
require(
    progress["new_facts_accepted"] == 0 and not progress["runtime_inputs_changed"],
    "Source boundary",
)
require(not progress["whole_goal_complete"], "Whole goal remains incomplete")
output = base / "issuer_context_progress.json"
save(output, progress, immutable=True)
save(pointer_path, {"progress_path": output.relative_to(root).as_posix(), "sha256": sha(output)})
print(progress["status"], progress["issuer_context"])
print("RELATED_IMAGES", len(related_images), "ACTIVE_STATE_UNCHANGED", "SOURCE_FACTS_ACCEPTED", 0)
