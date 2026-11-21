"""Record PDF repair warnings by source, independently of extracted financial fields."""

import logging
from collections import Counter
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import now, read, save, sha

ROOT = Path.cwd()
OUT = ROOT / "artifacts/checks/etf_holdings_2026-11-21"


class Messages(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def inspect(row):
    receipt = row["download"]
    path = ROOT / receipt["path"]
    if sha(path) != receipt["sha256"]:
        raise ValueError("Raw file differs from download")
    result = {
        "symbol": row["symbol"],
        "source": receipt["path"],
        "source_sha256": receipt["sha256"],
    }
    fitz.TOOLS.mupdf_warnings(reset=True)
    with fitz.open(path) as document:
        result.update(fitz_pages=len(document), fitz_repaired=document.is_repaired)
    result["fitz_warnings"] = fitz.TOOLS.mupdf_warnings(reset=True)
    logger = logging.getLogger("pypdf")
    messages = Messages()
    old_handlers, old_propagate = logger.handlers[:], logger.propagate
    logger.handlers = [messages]
    logger.propagate = False
    try:
        try:
            strict = PdfReader(path, strict=True)
            result.update(strict_status="READABLE", strict_pages=len(strict.pages))
        except Exception as error:
            result.update(strict_status="FAILED", strict_error=f"{type(error).__name__}: {error}")
        result["strict_warnings"] = list(messages.messages)
        messages.messages.clear()
        relaxed = PdfReader(path, strict=False)
        result["pypdf_pages"] = len(relaxed.pages)
        result["pypdf_warnings"] = list(messages.messages)
    finally:
        logger.handlers, logger.propagate = old_handlers, old_propagate
    result["page_count_agrees"] = result["fitz_pages"] == result["pypdf_pages"]
    result["status"] = (
        "REPAIR_REQUIRED"
        if result["strict_status"] == "FAILED"
        or result["fitz_repaired"]
        or result["pypdf_warnings"]
        else "READABLE"
    )
    if not result["page_count_agrees"]:
        result["status"] = "PAGE_COUNT_CONFLICT"
    return result


def main():
    if (OUT / "pdf_health_protocol.json").exists():
        raise FileExistsError("Preserve the prior structural review")
    collections = [
        ROOT / f"data/raw/{name}/collection.json"
        for name in ("etf_reports_2026-11-21", "etf_reports_retry_2026-11-21")
    ]
    rows = [r for path in collections for r in read(path)["documents"] if r["status"] == "RECEIVED"]
    assert len(rows) == len({r["url"] for r in rows}) == 552
    save(
        OUT / "pdf_health_protocol.json",
        {
            "registered_at": now(),
            "source_sha256": sha(Path(__file__)),
            "collections": {path.relative_to(ROOT).as_posix(): sha(path) for path in collections},
            "scope": "Attribute strict-reader failures, parser recovery warnings and page counts to every original PDF. No original source repair or portfolio-cell acceptance is implied.",
        },
        immutable=True,
    )
    results = []
    for index, row in enumerate(rows, 1):
        results.append(inspect(row))
        if index % 50 == 0:
            print(index, "/", len(rows), dict(Counter(r["status"] for r in results)), flush=True)
    save(
        OUT / "pdf_health.json",
        {
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "counts": dict(Counter(r["status"] for r in results)),
            "documents": results,
            "original_files_modified": False,
        },
        immutable=True,
    )
    print("FINISHED", dict(Counter(r["status"] for r in results)), flush=True)


if __name__ == "__main__":
    main()
