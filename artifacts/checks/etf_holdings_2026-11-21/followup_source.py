"""Adjudicate the seven explicit report-source and extraction failures."""

import bisect
import datetime as dt
import importlib.util
import re
from collections import Counter
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_holdings_2026-11-21"
OUT = ROOT / "data/processed/etf_holdings_followup_2026-11-21"
spec = importlib.util.spec_from_file_location(
    "original_etf_extractor", CHECKS / "extract_source.py"
)
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
OVERPRINT = re.compile(r"((?:20\d{2}){4})年+(\d{4,8})月+(\d{4,8})日+")


def cover_date(doc):
    pages = load_gzip(ROOT / doc["extracted_pages"])["pages"]
    records, identities = [], []
    for reader in ("fitz", "pypdf"):
        texts = [p[reader] for p in pages if p["page"] <= 10]
        cover = original.compact(texts[0])
        matches = list(OVERPRINT.finditer(cover))
        if len(matches) != 1:
            raise ValueError("Cover does not have exactly one four-layer date")
        match = matches[0]
        parts = []
        for value in match.groups():
            if len(value) % 4:
                raise ValueError("Date layer width differs")
            width = len(value) // 4
            layers = [value[i : i + width] for i in range(0, len(value), width)]
            if len(set(layers)) != 1:
                raise ValueError("Date layers are not identical")
            parts.append(int(layers[0]))
        date = dt.date(*parts).isoformat()
        replacement = f"{parts[0]}年{parts[1]}月{parts[2]}日"
        texts[0] = cover[: match.start()] + replacement + cover[match.end() :]
        row = {**doc, "report_year": parts[0], "quarter": 4}
        identity = original.identity(texts, row)
        if date != doc["title_period_end"]:
            raise ValueError("Observed four-layer date differs from the catalogue period")
        identities.append(identity)
        records.append(
            {
                "reader": reader,
                "source_page": 1,
                "original_span": match[0],
                "replacement_for_field_reading_only": replacement,
                "normalization": "four identical overprinted numeric layers; no other span changed",
            }
        )
    if identities[0] != identities[1]:
        raise ValueError("Independent identity adjudications disagree")
    return {
        **doc,
        "initial_errors": doc["errors"],
        "identity": identities[0],
        "errors": [],
        "status": "IDENTITY_AND_SECTION_REVIEWED",
        "cover_date_adjudication": records,
    }


def boundaries(texts, kind):
    compact = list(map(original.compact, texts))
    offsets = [0]
    for text in compact:
        offsets.append(offsets[-1] + len(text))
    joined = "".join(compact)
    start_text = (
        "投资组合报告" + ("报告期末" if kind == "quarterly" else "期末") + "基金资产组合情况"
    )
    starts = [m.start() for m in re.finditer(re.escape(start_text), joined)]
    if len(starts) != 1:
        raise ValueError("Portfolio text heading is missing or ambiguous")
    end_text = "开放式基金份额变动" if kind == "quarterly" else "基金份额持有人信息"
    end = joined.find(end_text, starts[0] + len(start_text))
    if end < 0:
        raise ValueError("Closing portfolio heading missing")
    return (bisect.bisect_right(offsets, starts[0]) - 1, bisect.bisect_right(offsets, end) - 1)


def omitted_numbers(doc):
    path = ROOT / doc["source"]
    if sha(path) != doc["source_sha256"]:
        raise ValueError("Original source changed")
    with fitz.open(path) as document:
        reader = PdfReader(path)
        if len(document) != len(reader.pages):
            raise ValueError("Reader page counts differ")
        fitz_text = [p.get_text() for p in document]
        pypdf_text = [p.extract_text() for p in reader.pages]
        a, b = boundaries(fitz_text, doc["report_type"]), boundaries(pypdf_text, doc["report_type"])
        if a != b:
            raise ValueError("Independent portfolio boundaries differ")
        pages = []
        for i in sorted(set(range(min(10, len(document)))) | set(range(a[0], a[1] + 1))):
            item = {"page": i + 1, "fitz": fitz_text[i], "pypdf": pypdf_text[i]}
            if a[0] <= i <= a[1]:
                page = document[i]
                item["tables"] = [
                    {
                        "bbox": list(t.bbox),
                        "row_count": t.row_count,
                        "col_count": t.col_count,
                        "cells": t.extract(),
                        "preceding_text": page.get_text(
                            clip=fitz.Rect(0, 0, page.rect.width, t.bbox[1])
                        ),
                        "row_boxes": [list(r.bbox) for r in t.rows],
                        "cell_boxes": [[list(c) if c else None for c in r.cells] for r in t.rows],
                    }
                    for t in page.find_tables(strategy="lines_strict").tables
                ]
            pages.append(item)
        target = OUT / "pages" / doc["symbol"] / (path.stem + ".json.gz")
        if target.exists():
            raise FileExistsError("Keep the previous source review")
        dump_gzip(
            target,
            {
                "source": doc["source"],
                "source_sha256": doc["source_sha256"],
                "pages_in_document": len(document),
                "pages": pages,
            },
        )
        return {
            **doc,
            "initial_errors": doc["errors"],
            "errors": [],
            "status": "IDENTITY_AND_SECTION_REVIEWED",
            "section_locator": "dual-reader textual headings; printed chapter numbers visually verified but absent from extracted text",
            "portfolio_chapter": 5 if doc["report_type"] == "quarterly" else 7,
            "heading_numbers_omitted_from_text": True,
            "portfolio_first_page": a[0] + 1,
            "portfolio_last_page": a[1] + 1,
            "extracted_pages": target.relative_to(ROOT).as_posix(),
            "extraction_sha256": sha(target),
            "dual_reader_pages": len(pages),
        }


def main():
    if (CHECKS / "extraction_reviewed.json").exists():
        raise FileExistsError("Keep the previous adjudication")
    protocol = read(CHECKS / "followup_protocol.json")
    if protocol["extraction_sha256"] != sha(CHECKS / "extraction.json") or protocol[
        "source_sha256"
    ] != sha(CHECKS / "extract_source.py"):
        raise ValueError("Original extraction identity changed")
    save(
        CHECKS / "followup_source_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(CHECKS / "followup_protocol.json"),
        },
        immutable=True,
    )
    extraction = read(CHECKS / "extraction.json")
    documents, adjudications = [], []
    for doc in extraction["documents"]:
        if doc["status"] == "EXTRACTION_FAILED":
            retry = read(ROOT / "data/raw/etf_reports_truncation_2026-11-21/collection.json")[
                "documents"
            ][0]
            if retry["url"] != doc["url"] or retry["status"] != "RECEIVED":
                raise ValueError("Complete same-URL source missing")
            original.OUT = OUT
            revised = original.extract(retry)
        elif doc["status"] == "REVIEW_REQUIRED":
            if doc["errors"] == ["identity: Report-period end missing from cover"]:
                revised = cover_date(doc)
            elif doc["errors"] == ["Expected one actual portfolio chapter, found 0"]:
                revised = omitted_numbers(doc)
            else:
                raise ValueError("Unregistered source failure")
        else:
            documents.append(doc)
            continue
        if revised["status"] != "IDENTITY_AND_SECTION_REVIEWED":
            raise ValueError("Source follow-up did not pass: " + doc["source"])
        adjudications.append({"original": doc, "reviewed": revised})
        documents.append(revised)
        print(doc["symbol"], doc["title"], "REVIEWED", flush=True)
    if len(adjudications) != 7:
        raise ValueError("Unexpected follow-up count")
    counts = dict(Counter(d["status"] for d in documents))
    if counts != {"IDENTITY_AND_SECTION_REVIEWED": 528, "NOTICE_ARCHIVED_NOT_PORTFOLIO": 24}:
        raise ValueError("Not all registered report identities were reviewed")
    save(
        CHECKS / "extraction_reviewed.json",
        {
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "original_extraction_sha256": sha(CHECKS / "extraction.json"),
            "counts": counts,
            "documents": documents,
            "adjudications": adjudications,
            "holdings_cells_validated": False,
            "model_inputs_changed": False,
        },
        immutable=True,
    )
    print("FINISHED", counts, flush=True)


if __name__ == "__main__":
    main()
