"""Record the additional BYD primary observation without accepting source facts."""

from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
pointer_path = root / ".git/cash_entitlement_primary_progress.json"
pointer = read(pointer_path)
previous_path = root / pointer["progress_path"]
assert sha(previous_path) == pointer["sha256"]
previous = read(previous_path)
protocol_path = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_path)
assert sha(protocol_path) == previous["protocol_sha256"]
for path, expected in {**previous["observations"], **previous["evidence"]}.items():
    assert sha(root / path) == expected, path
for item in protocol["source_inputs"].values():
    assert sha(root / item["path"]) == item["sha256"], item["path"]
state = protocol["active_state"]
assert sha(root / state["configuration"]) == state["configuration_sha256"]
for name in ("bundle", "plan"):
    assert sha(root / state[name]["path"]) == state[name]["sha256"], name
for name, expected in state["source_sha256"].items():
    assert sha(root / "src/quantfolio" / name) == expected, name

checks = root / protocol["checks_output"]
files = sorted((checks / "observations").glob("*.json"))
observations = [read(path) for path in files]
keys = {(item["symbol"], item["ex_date"]) for item in observations}
assert len(keys) == len(observations) == 18
for item in observations:
    assert item["facts_accepted"] == 0 and item["runtime_inputs_changed"] is False
    for name in ("source", "receipt"):
        assert sha(root / item[name]["path"]) == item[name]["sha256"]
    for page in item["page_observations"]:
        assert page["whole_page_visually_inspected"] is True
        assert sha(root / page["image"]) == page["image_sha256"]

progress = dict(previous)
progress.update(
    recorded_at=now(),
    preceding_progress={"path": pointer["progress_path"], "sha256": pointer["sha256"]},
    progress_writer_sha256=sha(Path(__file__)),
    formal_primary_observations=len(observations),
    full_text_pages_read_for_formal_observations=sum(
        item["full_text_pages_read"] for item in observations
    ),
    primary_whole_page_images_actually_inspected=sum(
        len(item["page_observations"]) for item in observations
    ),
    observations={path.relative_to(root).as_posix(): sha(path) for path in files},
    remaining_primary_observations=[
        item
        for item in previous["remaining_primary_observations"]
        if (item["symbol"], item["ex_date"]) not in keys
    ],
    prior_goal_turn_classification=previous["this_goal_turn_classification"],
    this_goal_turn_classification=(
        "progress: one additional complete primary observation and three actual page "
        "inspections; no new source acceptance or runtime change"
    ),
    next_step=(
        "Review the remaining 13 scoped primary events, complete independent and "
        "related-notice closure, preserve the unresolved source aggregate difference, "
        "then make field-scoped acceptance decisions before separate integration."
    ),
)
assert len(progress["remaining_primary_observations"]) == 13
assert progress["new_facts_accepted"] == 0
assert progress["whole_goal_complete"] is False
output = checks / "primary_review_progress_byd.json"
save(output, progress, immutable=True)
save(pointer_path, {"progress_path": output.relative_to(root).as_posix(), "sha256": sha(output)})
print(
    "PRIMARY_REVIEW_IN_PROGRESS",
    progress["formal_primary_observations"],
    "observations;",
    len(progress["remaining_primary_observations"]),
    "remaining; active inputs unchanged",
)
