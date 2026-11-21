"""Inventory all residual events and extract candidate texts without accepting new facts."""

import re
from collections import Counter
from importlib.metadata import version
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.distributions import extract_terms, verified_terms
from quantfolio.storage import digest, dump_gzip, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked(relative, expected=None):
    file = (root / relative).resolve()
    require(file.is_relative_to(root), (relative, "Path escapes repository"))
    actual = sha(file)
    require(frozen.get(relative) == actual, (relative, "Frozen source changed"))
    require(expected is None or expected == actual, (relative, "Registered hash differs"))
    return file


def mentions(pages):
    result = []
    pattern = r"每(?:10|十|1)?股.{0,55}(?:现金|红利|转增)|登记日|除权除息日|上市日|发放日|直接.{0,3}资金账户|调整为|更正"
    for number, page in enumerate(pages, 1):
        compact = re.sub(r"\s+", "", page)
        for match in re.finditer(pattern, compact):
            result.append(
                {"page": number, "text": compact[max(0, match.start() - 80) : match.end() + 160]}
            )
    return result


root = Path.cwd().resolve()
pfile = root / "configs/protocols/distribution_residuals.json"
p = read(pfile)
base = root / p["checks_output"]
frozen = read(root / "configs/integrity_manifest.json")["files"]
scope = read(checked(p["scope"]["path"], p["scope"]["sha256"]))
previous = read(checked(p["previous_review"]["path"], p["previous_review"]["sha256"]))
inputs = {
    name: read(checked(item["path"], item["sha256"])) for name, item in p["source_inputs"].items()
}
require(scope["events"] == 387, "Residual scope changed")
require(
    digest(previous["facts"]) == scope["prior_supported_facts_sha256"], "Supported facts changed"
)
for key in ("inventory", "source_text", "initial_diagnostics"):
    require(not (root / p["outputs"][key]).exists(), "Existing output requires recovery review")
source = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
require(source == p["active_state"]["source_sha256"], "Runtime changed before extraction")
symbols = {r["symbol"] for r in scope["records"]}
index = {(r["symbol"], r["ex_date"]): r for r in inputs["event_index"]["records"]}
structured = {(r["symbol"], r["ex_date"]): r for r in inputs["structured_actions"]["records"]}
documents = {}
for item in inputs["disclosures"]["documents"]:
    receipt_file = checked(item["receipt"])
    receipt = read(receipt_file)
    if not symbols.intersection(receipt["requested_symbols"]):
        continue
    require(receipt["status"] == "RECEIVED", (item["receipt"], "Missing received document"))
    file = checked(receipt["path"], receipt["sha256"])
    require(receipt["path"] not in documents, "Duplicate archive path")
    documents[receipt["path"]] = {
        "id": receipt["id"],
        "path": receipt["path"],
        "sha256": sha(file),
        "receipt": item["receipt"],
        "receipt_sha256": sha(receipt_file),
        "title": receipt["title"],
        "published_code": receipt["published_code"],
        "catalog_date": receipt["catalog_date"],
        "url": receipt["url"],
        "kind": receipt["kind"],
        "requested_symbols": receipt["requested_symbols"],
        "scoped_symbols": sorted(symbols.intersection(receipt["requested_symbols"])),
        "candidate_for": [],
        "selected_primary_for": [],
    }
events = []
for row in scope["records"]:
    key = row["symbol"], row["ex_date"]
    candidate_paths = []
    for candidate in index[key]["candidates"]:
        document = documents[candidate["source"]]
        require(document["sha256"] == candidate["source_sha256"], (key, "Candidate hash"))
        require(row["symbol"] in document["requested_symbols"], (key, "Candidate issuer"))
        require(candidate["receipt"] == document["receipt"], (key, "Candidate receipt"))
        document["candidate_for"].append(list(key))
        candidate_paths.append(document["path"])
    if row.get("source"):
        document = documents[row["source"]["path"]]
        require(document["sha256"] == row["source"]["sha256"], (key, "Primary hash"))
        document["selected_primary_for"].append(list(key))
    events.append(
        {
            "symbol": key[0],
            "ex_date": key[1],
            "prior_status": row["status"],
            "original_action_sha256": row["original_action_sha256"],
            "index_status": index[key]["status"],
            "structured_status": structured[key]["status"],
            "candidates": candidate_paths,
            "selected_primary": row.get("source", {}).get("path"),
            "archived_issuer_documents": [
                d["path"] for d in documents.values() if key[0] in d["requested_symbols"]
            ],
        }
    )
inventory = {
    "status": "INVENTORIED_NOT_ACCEPTED",
    "created_at": now(),
    "protocol_sha256": sha(pfile),
    "scope_sha256": p["scope"]["sha256"],
    "checker_sha256": sha(Path(__file__)),
    "source_sha256": source,
    "issuers": len(symbols),
    "residual_events": len(events),
    "archive_documents": len(documents),
    "candidate_documents": sum(
        bool(d["candidate_for"] or d["selected_primary_for"]) for d in documents.values()
    ),
    "events": events,
    "documents": list(documents.values()),
    "scope": "Metadata inventory of all received issuer-related archive files. Candidate text extraction follows; neither inventory nor extracted values establish financial facts or complete historical coverage.",
}
save(root / p["outputs"]["inventory"], inventory, immutable=True)
texts, failures = {}, []
targets = [d for d in documents.values() if d["candidate_for"] or d["selected_primary_for"]]
for number, document in enumerate(sorted(targets, key=lambda d: d["path"]), 1):
    file = root / document["path"]
    readers = {}
    for name in ("pypdf", "mupdf"):
        try:
            if name == "pypdf":
                pages = [page.extract_text() or "" for page in PdfReader(file).pages]
            else:
                with fitz.open(file) as pdf:
                    pages = [page.get_text(sort=True) for page in pdf]
            readers[name] = {
                "status": "EXTRACTED_NOT_REVIEWED",
                "pages": pages,
                "page_count": len(pages),
            }
        except Exception as error:
            failure = {
                "path": document["path"],
                "reader": name,
                "error_type": type(error).__name__,
                "message": str(error),
            }
            failures.append(failure)
            readers[name] = {"status": "EXTRACTION_FAILED", "failure": failure}
    available = [r["page_count"] for r in readers.values() if "page_count" in r]
    if len(available) == 2 and available[0] != available[1]:
        failures.append(
            {
                "path": document["path"],
                "reader": "comparison",
                "message": "Page count mismatch",
                "counts": available,
            }
        )
    primary = readers["pypdf"].get("pages", [])
    texts[document["path"]] = {
        "source": document,
        "readers": readers,
        "navigation_terms": extract_terms(primary),
        "navigation_mentions": mentions(primary),
        "machine_text_only": True,
        "whole_pages_visually_reviewed": False,
    }
    if number % 25 == 0 or number == len(targets):
        print(
            "CANDIDATES_EXTRACTED",
            number,
            "of",
            len(targets),
            "failures",
            len(failures),
            flush=True,
        )
text_file = root / p["outputs"]["source_text"]
dump_gzip(
    text_file,
    {
        "status": "EXTRACTED_NOT_ACCEPTED",
        "completed_at": now(),
        "protocol_sha256": sha(pfile),
        "checker_sha256": sha(Path(__file__)),
        "inventory_sha256": sha(root / p["outputs"]["inventory"]),
        "readers": {"pypdf": version("pypdf"), "mupdf": fitz.VersionBind},
        "documents": texts,
        "failures": failures,
        "facts_accepted": 0,
    },
)
diagnostics = []
for row in scope["records"]:
    key = row["symbol"], row["ex_date"]
    record = {"symbol": key[0], "ex_date": key[1], "prior_status": row["status"], "readers": {}}
    if row.get("source"):
        text = texts[row["source"]["path"]]
        for name, reader in text["readers"].items():
            if "pages" not in reader:
                record["readers"][name] = {"status": reader["status"]}
                continue
            terms = extract_terms(reader["pages"])
            fields, status = verified_terms(
                structured[key]["stored_action"], row["structured_source"], terms
            )
            record["readers"][name] = {
                "status": status,
                "candidate_fields_not_accepted": fields,
                "terms": terms,
            }
    diagnostics.append(record)
require(
    source == {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))},
    "Runtime changed during extraction",
)
require(
    sha(paths_file := root / p["active_state"]["configuration"])
    == p["active_state"]["configuration_sha256"],
    str(paths_file),
)
save(
    root / p["outputs"]["initial_diagnostics"],
    {
        "status": "DIAGNOSTIC_NOT_ACCEPTANCE",
        "completed_at": now(),
        "protocol_sha256": sha(pfile),
        "source_text_sha256": sha(text_file),
        "checker_sha256": sha(Path(__file__)),
        "extractor_sha256": source["distributions.py"],
        "records": diagnostics,
        "reader_status_counts": {
            reader: dict(
                Counter(
                    r["readers"].get(reader, {}).get("status", "NO_SELECTED_PRIMARY")
                    for r in diagnostics
                )
            )
            for reader in ("pypdf", "mupdf")
        },
        "extraction_failures": failures,
        "facts_accepted": 0,
        "scope": "Existing unchanged parser applied to both text representations for navigation. Different reader outcomes are not accepted facts; full labelled source, role and independent review remain required.",
    },
    immutable=True,
)
print("RESIDUAL_TEXT_INVENTORY_COMPLETE", len(events), len(texts), "facts accepted", 0, flush=True)
