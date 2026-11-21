"""Replay both extracted readers across the complete registered residual inventory."""

from collections import Counter
from pathlib import Path

from quantfolio.distributions import (
    extract_terms,
    resolve_companion_notices,
    source_available,
    verified_terms,
)
from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


base = Path("artifacts/checks/distribution_residuals_2026-11-22")
protocol = read("configs/protocols/distribution_residuals.json")
layout_protocol = read("configs/protocols/distribution_layouts.json")
texts = load_gzip(base / "source_text.json.gz")["documents"]
require(
    sha(base / "source_text.json.gz")
    == layout_protocol["input_evidence"][(base / "source_text.json.gz").as_posix()],
    "Text extraction changed",
)
scope = read(protocol["scope"]["path"])
inputs = {}
for name in ("event_index", "structured_actions"):
    source = protocol["source_inputs"][name]
    require(sha(source["path"]) == source["sha256"], "Input changed")
    inputs[name] = {(row["symbol"], row["ex_date"]): row for row in read(source["path"])["records"]}
extractor = sha("src/quantfolio/distributions.py")
terms, extraction_failures = {}, []
for path, document in texts.items():
    terms[path] = {}
    for reader, data in document["readers"].items():
        try:
            terms[path][reader] = extract_terms(data["pages"])
        except (ValueError, KeyError, TypeError) as error:
            extraction_failures.append(
                {
                    "source": path,
                    "reader": reader,
                    "error": str(error),
                    "type": type(error).__name__,
                }
            )
records, counts = [], {reader: Counter() for reader in ("pypdf", "mupdf")}
for row in scope["records"]:
    key = row["symbol"], row["ex_date"]
    event = inputs["event_index"][key]
    structured = inputs["structured_actions"][key]
    action = structured["stored_action"]
    record = {
        "symbol": key[0],
        "ex_date": key[1],
        "prior_status": row["status"],
        "original_action_sha256": row["original_action_sha256"],
        "readers": {},
    }
    for reader in counts:
        candidate, resolution = None, None
        if event["status"] == "ONE_TEXT_CANDIDATE":
            candidate = event["candidates"][0]
        elif event["status"] == "MULTIPLE_TEXT_CANDIDATES":
            documents = {
                item["id"]: texts[item["source"]]["readers"][reader]["pages"]
                for item in event["candidates"]
            }
            resolution = resolve_companion_notices(key[0], event["candidates"], documents)
            candidate = next(
                (item for item in event["candidates"] if item["id"] == resolution["selected_id"]),
                None,
            )
        fields, known_at = {}, None
        if candidate is None:
            status = event["status"]
        elif structured["status"] not in ("STRUCTURED_DATE_CANDIDATE", "STRUCTURED_EVENT_MATCH"):
            status = structured["status"]
        else:
            known_at = source_available(candidate, action)
            if not known_at:
                status = "UNSUPPORTED_TITLE_OR_LATE_SOURCE"
            elif reader not in terms[candidate["source"]]:
                status = "EXTRACTION_FAILED"
            else:
                fields, status = verified_terms(
                    action, structured["candidate"], terms[candidate["source"]][reader]
                )
        record["readers"][reader] = {
            "status": status,
            "candidate_fields_not_accepted": fields,
            "selected_primary": candidate["source"] if candidate else None,
            "known_at": known_at,
            "candidate_resolution": resolution,
        }
        counts[reader][status] += 1
    records.append(record)
require(len(records) == 387 and len(terms) == 423, "Incomplete inventory")
require(sha("src/quantfolio/distributions.py") == extractor, "Reader changed during probe")
dump_gzip(
    base / "candidate_terms.json.gz",
    {
        "status": "PROPOSED_TERMS_NOT_ACCEPTED",
        "extractor_sha256": extractor,
        "source_text_sha256": sha(base / "source_text.json.gz"),
        "documents": terms,
        "extraction_failures": extraction_failures,
        "facts_accepted": 0,
    },
)
save(
    base / "candidate_probe.json",
    {
        "status": "DIAGNOSTIC_NOT_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "extractor_sha256": extractor,
        "protocol_sha256": sha("configs/protocols/distribution_layouts.json"),
        "source_text_sha256": sha(base / "source_text.json.gz"),
        "terms_sha256": sha(base / "candidate_terms.json.gz"),
        "records": records,
        "reader_status_counts": {reader: dict(values) for reader, values in counts.items()},
        "extraction_failures": extraction_failures,
        "facts_accepted": 0,
    },
    immutable=True,
)
print(
    "CANDIDATE_PROBE_COMPLETE",
    len(records),
    len(terms),
    "extraction failures",
    len(extraction_failures),
)
print("READER_STATUS_COUNTS", {reader: dict(values) for reader, values in counts.items()})
