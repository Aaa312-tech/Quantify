"""Verify the complete navigation repair without changing financial source acceptance."""

from pathlib import Path

from quantfolio.storage import digest, load_gzip, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


base = Path("artifacts/checks/distribution_residuals_2026-11-22")
protocol = read("configs/protocols/residual_date_navigation.json")
require(
    sha(protocol["reader"]["path"]) == protocol["reader"]["sha256"], "Registered reader changed"
)
require(
    sha(protocol["prior_navigation"]["path"]) == protocol["prior_navigation"]["sha256"],
    "First index changed",
)
require(
    sha("src/quantfolio/distributions.py") == protocol["production_reader_sha256"],
    "Production reader changed",
)
old = load_gzip(protocol["prior_navigation"]["path"])
new = load_gzip(base / "dated_event_navigation.json.gz")
require(
    set(old["documents"]) == set(new["documents"]) and len(new["documents"]) == 1135,
    "Document inventory differs",
)
require(old["empty_text_pages"] == new["empty_text_pages"], "Empty page inventory changed")
changed_documents = []
for path, before in old["documents"].items():
    after = new["documents"][path]
    require(before["source"] == after["source"], "Source metadata changed")
    require(
        before["title_has_amendment_word"] == after["title_has_amendment_word"],
        "Title classification changed",
    )
    for reader, extracted in before["readers"].items():
        result = after["readers"][reader]
        require(
            extracted["amendment_word_mentions"] == result["amendment_word_mentions"],
            "Amendment index changed",
        )
        old_counts = {key: len(value) for key, value in extracted["dated_mentions"].items()}
        new_counts = {key: len(value) for key, value in result["dated_mentions"].items()}
        if old_counts != new_counts:
            changed_documents.append(
                {
                    "source": path,
                    "reader": reader,
                    "before_date_counts": old_counts,
                    "after_date_counts": new_counts,
                }
            )
for example in protocol["source_examples"]:
    for reader, extracted in new["documents"][example["source"]]["readers"].items():
        require(
            set(example["expected_dates"]) <= set(extracted["dated_mentions"]),
            (example["source"], reader, "Printed dates absent"),
        )
before_events = {(row["symbol"], row["ex_date"]): row for row in old["events"]}
after_events = {(row["symbol"], row["ex_date"]): row for row in new["events"]}
require(
    set(before_events) == set(after_events) and len(after_events) == 387, "Event inventory differs"
)
changes = []
for key, before in before_events.items():
    after = after_events[key]
    for field in ("symbol", "ex_date", "record_date", "prior_status", "status"):
        require(before[field] == after[field], "Event metadata changed")
    if before["documents"] != after["documents"]:
        changes.append(
            {
                "symbol": key[0],
                "ex_date": key[1],
                "before_sha256": digest(before["documents"]),
                "after_sha256": digest(after["documents"]),
                "before_source_paths": [r["source"] for r in before["documents"]],
                "after_source_paths": [r["source"] for r in after["documents"]],
            }
        )
save(
    base / "dated_navigation_closure.json",
    {
        "status": "NAVIGATION_REPAIR_CHECKED_NOT_FINANCIAL_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "protocol_sha256": sha("configs/protocols/residual_date_navigation.json"),
        "prior_navigation_sha256": sha(protocol["prior_navigation"]["path"]),
        "corrected_navigation_sha256": sha(base / "dated_event_navigation.json.gz"),
        "documents_checked": 1135,
        "events_checked": 387,
        "document_date_count_changes": changed_documents,
        "event_context_or_source_changes": changes,
        "metadata_amendments_and_empty_pages_unchanged": True,
        "source_examples_checked": protocol["source_examples"],
        "facts_accepted": 0,
    },
    immutable=True,
)
print(
    "DATE_NAVIGATION_REPAIR_CHECKED",
    len(changed_documents),
    "document-reader date inventories changed",
)
