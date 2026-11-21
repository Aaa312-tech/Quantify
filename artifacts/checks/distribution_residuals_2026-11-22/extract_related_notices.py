"""Extract every remaining received issuer notice for missing-source and amendment review."""

from importlib.metadata import version
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


base = Path("artifacts/checks/distribution_residuals_2026-11-22")
inventory = read(base / "document_inventory.json")
candidate_texts = load_gzip(base / "source_text.json.gz")["documents"]
frozen = read("configs/integrity_manifest.json")["files"]
text_path = base / "related_source_text.json.gz"
summary_path = base / "related_extraction.json"
require(not text_path.exists() and not summary_path.exists(), "Outputs already exist")
require(version("pypdf") == "6.9.2" and fitz.VersionBind == "1.27.2.3", "Reader versions changed")
all_documents = {row["path"]: row for row in inventory["documents"]}
require(len(all_documents) == 1135 and len(candidate_texts) == 423, "Document scope changed")
require(set(candidate_texts) <= set(all_documents), "Candidate document not in archive")
pending = {path: row for path, row in all_documents.items() if path not in candidate_texts}
documents, failures = {}, []
started_at = now()
for count, (path, row) in enumerate(sorted(pending.items()), 1):
    require(sha(path) == row["sha256"] == frozen.get(path), (path, "Source identity differs"))
    require(sha(row["receipt"]) == row["receipt_sha256"], (path, "Receipt changed"))
    receipt = read(row["receipt"])
    require(
        receipt["path"] == path and receipt["sha256"] == row["sha256"], "Receipt binding differs"
    )
    readers = {}
    for reader in ("pypdf", "mupdf"):
        try:
            if reader == "pypdf":
                pages = [page.extract_text() or "" for page in PdfReader(path).pages]
            else:
                with fitz.open(path) as pdf:
                    pages = [page.get_text(sort=True) for page in pdf]
            readers[reader] = {
                "status": "EXTRACTED_NOT_REVIEWED",
                "pages": pages,
                "page_count": len(pages),
                "empty_pages": [number for number, page in enumerate(pages, 1) if not page.strip()],
            }
        except Exception as error:
            failure = {
                "source": path,
                "reader": reader,
                "error_type": type(error).__name__,
                "error": str(error),
            }
            readers[reader] = {"status": "EXTRACTION_FAILED", **failure}
            failures.append(failure)
    documents[path] = {
        "source": row,
        "readers": readers,
        "machine_text_only": True,
        "whole_pages_visually_reviewed": False,
    }
    if count % 50 == 0 or count == len(pending):
        print(
            "RELATED_NOTICES_EXTRACTED",
            count,
            "of",
            len(pending),
            "failures",
            len(failures),
            flush=True,
        )
require(
    set(documents) | set(candidate_texts) == set(all_documents), "Incomplete archive extraction"
)
dump_gzip(
    text_path,
    {
        "status": "EXTRACTED_NOT_ACCEPTED",
        "started_at": started_at,
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "inventory_sha256": sha(base / "document_inventory.json"),
        "candidate_text_sha256": sha(base / "source_text.json.gz"),
        "readers": {"pypdf": version("pypdf"), "mupdf": fitz.VersionBind},
        "documents": documents,
        "extraction_failures": failures,
        "facts_accepted": 0,
    },
)
save(
    summary_path,
    {
        "status": "EXTRACTION_COMPLETE_NOT_FINANCIAL_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "text_sha256": sha(text_path),
        "candidate_documents_previously_extracted": len(candidate_texts),
        "additional_received_documents_extracted": len(documents),
        "total_received_issuer_documents_in_scope": len(all_documents),
        "additional_primary_reader_pages": sum(
            row["readers"]["pypdf"].get("page_count", 0) for row in documents.values()
        ),
        "extraction_failures": failures,
        "facts_accepted": 0,
        "scope": "All received archive documents for scoped issuers, not proof of a complete public archive or of financial facts. Empty/scanned pages and source conflicts still require review.",
    },
    immutable=True,
)
print("RELATED_ARCHIVE_EXTRACTION_COMPLETE", len(documents), "facts accepted 0", flush=True)
