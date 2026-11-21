"""Locate complete derivative notes and retain their original financial tables."""

import bisect
import re
import unicodedata
from collections import Counter
from pathlib import Path

import fitz
import pypdf

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

ROOT = Path.cwd()
PROTOCOL_FILE = ROOT / "configs/protocols/etf_exposures.json"
PROTOCOL = read(PROTOCOL_FILE)
CHECKS = ROOT / PROTOCOL["checks_output"]
OUTPUT = ROOT / PROTOCOL["processed_output"]


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def locate(text):
    pattern = r"(?m)^\s*([67]\.4\.7\.\d+)(?:\.\d+)?\s*衍生金融(?:资产|工具)"
    starts = list(re.finditer(pattern, text))
    roots = {match[1] for match in starts}
    if len(roots) != 1:
        raise ValueError(f"Expected one derivative-note root, found {sorted(roots)}")
    first = starts[0]
    prefix, number = first[1].rsplit(".", 1)
    following = prefix + "." + str(int(number) + 1)
    ending = re.search(
        r"(?m)^\s*" + re.escape(following) + r"\s+[\u4e00-\u9fff]", text[first.end() :]
    )
    if ending is None:
        raise ValueError("Derivative note has no following peer heading")
    return {
        "section": first[1],
        "following_section": following,
        "start": first.start(),
        "end": first.end() + ending.start(),
        "following_heading_end": first.end() + ending.end(),
    }


def grids(page):
    return [
        {
            "bbox": list(table.bbox),
            "row_count": table.row_count,
            "col_count": table.col_count,
            "cells": table.extract(),
            "preceding_text": page.get_text(clip=fitz.Rect(0, 0, page.rect.width, table.bbox[1])),
            "row_boxes": [list(row.bbox) for row in table.rows],
            "cell_boxes": [
                [list(cell) if cell else None for cell in row.cells] for row in table.rows
            ],
        }
        for table in page.find_tables(strategy="lines_strict").tables
    ]


def lending_context(pages):
    evidence = []
    for page in pages:
        matches = {}
        for reader in ("fitz", "pypdf"):
            sentences = re.split(r"[。；]", compact(page[reader]))
            matches[reader] = [
                s
                for s in sentences
                if "出借" in s
                and any(
                    phrase in s
                    for phrase in (
                        "包括",
                        "包含",
                        "计入",
                        "列示",
                        "列报",
                        "终止确认",
                        "不含",
                        "公允价值",
                    )
                )
            ]
        if any(matches.values()):
            evidence.append({"page": page["page"], "reader_context": matches})
    return evidence


def extract(doc):
    source = ROOT / doc["source"]
    if sha(source) != doc["source_sha256"]:
        raise ValueError("Accepted source PDF changed")
    old_path = ROOT / doc["extracted_pages"]
    if sha(old_path) != doc["extraction_sha256"]:
        raise ValueError("Frozen portfolio pages changed")
    old = load_gzip(old_path)
    portfolio_pages = [p for p in old["pages"] if p["page"] >= doc["portfolio_first_page"]]
    result = {
        key: doc[key]
        for key in (
            "symbol",
            "title",
            "catalogue_date",
            "report_type",
            "identity",
            "source",
            "source_sha256",
            "pages",
            "extracted_pages",
            "extraction_sha256",
        )
    }
    result["portfolio_pages"] = [p["page"] for p in portfolio_pages]
    result["lending_context"] = lending_context(portfolio_pages)
    if doc["report_type"] == "quarterly":
        result.update(
            status="PORTFOLIO_SOURCE_REUSED_FINANCIAL_FIELDS_PENDING", financial_notes=None
        )
        return result
    with fitz.open(source) as pdf:
        # A complete prefix scan also retains the location of accounting-policy evidence.
        texts = [pdf[i].get_text() for i in range(doc["portfolio_first_page"] - 1)]
        offsets = [0]
        for text in texts:
            offsets.append(offsets[-1] + len(text) + 1)
        joined = "\n".join(texts)
        location = locate(joined)
        start = bisect.bisect_right(offsets, location["start"]) - 1
        end = bisect.bisect_right(offsets, location["following_heading_end"]) - 1
        note_indices = set(range(start, end + 1))
        lending_indices = set()
        pattern = r"(?:包含|包括|计入|列示|列报|终止确认|不含).{0,60}(?:转融通|出借)|(?:转融通|出借).{0,100}(?:包含|包括|计入|列示|列报|终止确认|不含)"
        for index, text in enumerate(texts):
            if re.search(pattern, compact(text)):
                lending_indices.add(index)
        selected = sorted(note_indices | lending_indices)
        reader = pypdf.PdfReader(source)
        if len(reader.pages) != len(pdf):
            raise ValueError("Reader page counts differ")
        pages = []
        for index in selected:
            page = {
                "page": index + 1,
                "fitz": texts[index],
                "pypdf": reader.pages[index].extract_text(),
                "roles": [
                    name
                    for name, indices in (
                        ("derivative_note", note_indices),
                        ("lending_policy", lending_indices),
                    )
                    if index in indices
                ],
            }
            if index in note_indices:
                page["tables"] = grids(pdf[index])
            pages.append(page)
        notes = [p for p in pages if "derivative_note" in p["roles"]]
        locators = {name: locate("\n".join(p[name] for p in notes)) for name in ("fitz", "pypdf")}
        if len({(x["section"], x["following_section"]) for x in locators.values()}) != 1:
            raise ValueError("Independent note boundaries differ")
        for name, found in locators.items():
            joined_notes = "\n".join(p[name] for p in notes)
            found["section_text"] = joined_notes[found["start"] : found["end"]]
        target = OUTPUT / "financial_notes" / doc["symbol"] / (source.stem + ".json.gz")
        dump_gzip(
            target,
            {
                "source": doc["source"],
                "source_sha256": doc["source_sha256"],
                "pages_in_document": len(pdf),
                "pages": pages,
                "derivative_boundaries": locators,
                "prefix_pages_scanned": len(texts),
                "source_original_modified": False,
            },
        )
        result.update(
            status="NOTE_BOUNDARIES_REVIEWED_FINANCIAL_FIELDS_PENDING",
            financial_notes=target.relative_to(ROOT).as_posix(),
            financial_notes_sha256=sha(target),
            derivative_note_pages=[p["page"] for p in notes],
            additional_dual_reader_pages=len(pages),
            lending_context=result["lending_context"] + lending_context(pages),
        )
    return result


def main():
    if (CHECKS / "notes_extraction.json").exists():
        raise FileExistsError("Keep the earlier extraction")
    for name, expected in PROTOCOL["source_sha256"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Registered source changed: " + name)
    save(
        CHECKS / "notes_source_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(PROTOCOL_FILE),
            "fitz_version": fitz.VersionBind,
            "pypdf_version": pypdf.__version__,
            "numeric_fields_accepted": False,
        },
        immutable=True,
    )
    documents = read(ROOT / "artifacts/checks/etf_holdings_2026-11-21/extraction_reviewed.json")[
        "documents"
    ]
    results = []
    for doc in documents:
        if doc["publication_notice"]:
            continue
        try:
            row = extract(doc)
        except Exception as error:
            row = {
                key: doc[key]
                for key in (
                    "symbol",
                    "title",
                    "catalogue_date",
                    "report_type",
                    "source",
                    "source_sha256",
                )
            }
            row.update(status="EXTRACTION_FAILED", error=f"{type(error).__name__}: {error}")
        results.append(row)
        if len(results) % 25 == 0:
            print(len(results), "/528", dict(Counter(r["status"] for r in results)), flush=True)
    save(
        CHECKS / "notes_extraction.json",
        {
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "counts": dict(Counter(r["status"] for r in results)),
            "documents": results,
            "financial_fields_validated": False,
            "model_inputs_changed": False,
        },
        immutable=True,
    )
    print("FINISHED", dict(Counter(r["status"] for r in results)), flush=True)


if __name__ == "__main__":
    main()
