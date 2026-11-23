"""Extraction and validation of archived industry tables."""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from .storage import dump_gzip, log, now, read, save, sha

HEADERS = (
    "上市公司代码",
    "上市公司简称",
    "门类代码",
    "门类简称",
    "次类代码",
    "次类简称",
    "大类代码",
    "大类简称",
)
FIELDS = (
    "code",
    "name",
    "section",
    "section_name",
    "subclass",
    "subclass_name",
    "division",
    "division_name",
)
ALIAS_HEADERS = ("序号", "证券简称", "上市日期", "旧代码", "新代码")
WORD = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def compact(value):
    """Remove layout whitespace; retain punctuation and every other source character."""
    if value is None:
        return ""
    if not isinstance(value, str) or "\ufffd" in value or "\x00" in value:
        raise ValueError("Unreadable industry source cell")
    return "".join(value.split())


def codebook_rows(tables):
    """Read the three published levels, retaining merged group rows as raw evidence."""
    records = []
    section = subclass = None
    seen = set()
    for table_index, table in enumerate(tables, 1):
        for row_index, cells in enumerate(table, 1):
            flat = [compact("".join(cell)) for cell in cells]
            if not any(flat) or flat[0] in ("代码", "门类"):
                continue
            if len(cells) != 6:
                raise ValueError(f"Unexpected codebook row: {table_index}/{row_index}")
            upper = [i for i in range(3) if flat[i]]
            if not upper:
                # Group definitions are preserved but not imputed to the stock tables.
                if flat[3] and not re.fullmatch(r"(?:[0-9]{3})+", flat[3]):
                    raise ValueError(f"Unknown group code: {flat[3]}")
                continue
            if len(upper) != 1:
                raise ValueError("Ambiguous merged classification levels")
            column = upper[0]
            code = flat[column]
            if not re.fullmatch((r"[A-S]", r"C[A-Z]", r"[0-9]{2}")[column], code):
                raise ValueError(f"Invalid classification code: {code}")
            names = [compact(value) for value in cells[4] if compact(value)]
            if not names:
                raise ValueError(f"Missing classification name: {code}")
            # A merged division row may append group names in later paragraphs.
            if len(names) > 1 and not flat[3]:
                raise ValueError(f"Ambiguous classification name: {code}")
            if column == 0:
                section, subclass = code, None
            elif column == 1:
                if section != "C":
                    raise ValueError("Manufacturing subclass outside section C")
                subclass = code
            elif section is None or (section == "C" and subclass is None):
                raise ValueError("Division has no parent classification")
            level = ("section", "subclass", "division")[column]
            if (level, code) in seen:
                raise ValueError(f"Duplicate codebook entry: {level}/{code}")
            seen.add((level, code))
            records.append(
                {
                    "level": level,
                    "code": code,
                    "name": names[0],
                    "section": section,
                    "subclass": subclass,
                    "table": table_index,
                    "row": row_index,
                    "raw_paragraphs": cells,
                }
            )
    if not records:
        raise ValueError("Codebook contains no classifications")
    return records


def read_codebook(path):
    with ZipFile(path) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
    tables = []
    for table in document.findall(".//w:tbl", WORD):
        rows = []
        for row in table.findall("w:tr", WORD):
            cells = []
            for cell in row.findall("w:tc", WORD):
                cells.append(
                    [
                        "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD))
                        for paragraph in cell.findall("w:p", WORD)
                    ]
                )
            rows.append(cells)
        tables.append(rows)
    return {"records": codebook_rows(tables), "raw_tables": tables}


def classification_row(cells, codebook, *, collect_findings=False):
    if len(cells) != len(FIELDS) or any(cell is None for cell in cells):
        raise ValueError("Industry record must have eight unmerged cells")
    values = [compact(cell) for cell in cells]
    row = dict(zip(FIELDS, values))
    if not re.fullmatch(r"[0-9]{6}", row["code"]) or not row["name"]:
        raise ValueError("Invalid security identity in industry row")
    findings = []

    def mismatch(field, actual, expected, problem):
        findings.append(
            {"field": field, "source": actual, "expected": expected, "problem": problem}
        )

    for level in ("section", "subclass", "division"):
        code, name = row[level], row[level + "_name"]
        if level == "subclass" and row["section"] != "C":
            if code or name:
                mismatch(level, code, "", "nonmanufacturing_subclass")
            continue
        reference = codebook.get((level, code))
        if reference is None:
            mismatch(level, code, None, "unknown_code")
            continue
        if reference["name"] != name:
            mismatch(level + "_name", name, reference["name"], "name_mismatch")
        if reference["section"] != row["section"]:
            mismatch(level + ".section", row["section"], reference["section"], "parent_mismatch")
        if level == "division" and reference["subclass"] != (row["subclass"] or None):
            mismatch(
                level + ".subclass",
                row["subclass"] or None,
                reference["subclass"],
                "parent_mismatch",
            )
    if findings and not collect_findings:
        raise ValueError(f"Classification mismatch: {row['code']}/{findings}")
    return {**row, "raw_cells": cells, "taxonomy_findings": findings}


def alias_row(cells):
    if len(cells) != 5 or any(cell is None for cell in cells):
        raise ValueError("Alias record must have five unmerged cells")
    number, name, date, old, new = map(compact, cells)
    if not number.isascii() or not number.isdigit() or not name:
        raise ValueError("Invalid alias row identity")
    dt.date.fromisoformat(date)
    if not re.fullmatch(r"[48][0-9]{5}", old) or not re.fullmatch(r"920[0-9]{3}", new):
        raise ValueError("Unexpected Beijing code alias")
    return {
        "number": int(number),
        "name": name,
        "source_listing_date": date,
        "old_code": old,
        "new_code": new,
        "raw_cells": cells,
    }


def verify_page(records, text, aliases=False):
    """Compare every cell against another PDF parser and account for all source codes."""
    normalized = compact(text)
    position = 0
    expected_codes = []
    for row in records:
        value = "".join(compact(cell) for cell in row["raw_cells"])
        found = normalized.find(value, position)
        if found < 0:
            raise ValueError(f"Independent PDF text differs at row {row['row']}")
        position = found + len(value)
        expected_codes.extend([row["old_code"], row["new_code"]] if aliases else [row["code"]])
    actual_codes = re.findall(r"(?<![0-9])[0-9]{6}(?![0-9])", text)
    if Counter(actual_codes) != Counter(expected_codes):
        raise ValueError("Independent PDF security-code inventory differs")


def continuation_columns(cells, header_cells):
    """A headerless alias page must retain the actual five-column geometry."""
    if len(cells) != 5 or len(header_cells) != 5 or any(cell is None for cell in cells):
        raise ValueError("Alias continuation has missing or merged columns")
    if any(
        abs(cell[i] - header[i]) > 0.1 for cell, header in zip(cells, header_cells) for i in (0, 2)
    ):
        raise ValueError("Alias continuation column positions changed")


def extract_pdf(path, codebook, aliases=False, *, followup=False):
    try:
        import pymupdf
        from pypdf import PdfReader
    except ImportError as error:
        raise ImportError("Install the industry-data extra to review industry PDFs") from error
    independent = PdfReader(path)
    records, pages = [], []
    header_cells = None
    headers = ALIAS_HEADERS if aliases else HEADERS
    with pymupdf.open(path) as document:
        if len(document) != len(independent.pages):
            raise ValueError("PDF readers disagree on page count")
        for page_number, page in enumerate(document, 1):
            tables = page.find_tables().tables
            if len(tables) != 1:
                raise ValueError(f"Expected one table on PDF page {page_number}")
            table = tables[0]
            current, header_found, inherited_header = [], False, False
            for row_number, cells in enumerate(table.extract(), 1):
                values = tuple(map(compact, cells))
                if not any(values):
                    continue
                if values == headers:
                    header_found = True
                    if aliases:
                        header_cells = table.rows[row_number - 1].cells
                    continue
                if (
                    not header_found
                    and not any(values[1:])
                    and re.fullmatch(r"20[0-9]{2}年[上下]半年上市公司行业分类结果", values[0])
                ):
                    continue
                if not header_found:
                    if not (followup and aliases and page_number > 1 and header_cells):
                        raise ValueError(f"Missing table header on page {page_number}")
                    continuation_columns(table.rows[row_number - 1].cells, header_cells)
                    header_found = inherited_header = True
                if inherited_header:
                    continuation_columns(table.rows[row_number - 1].cells, header_cells)
                parsed = (
                    alias_row(cells)
                    if aliases
                    else classification_row(cells, codebook, collect_findings=followup)
                )
                current.append(
                    {
                        **parsed,
                        "page": page_number,
                        "row": row_number,
                        "bbox": list(table.rows[row_number - 1].bbox),
                    }
                )
            if not header_found or not current:
                raise ValueError(f"Empty industry table on page {page_number}")
            try:
                verify_page(current, independent.pages[page_number - 1].extract_text(), aliases)
            except ValueError as error:
                raise ValueError(f"Page {page_number}: {error}") from error
            records.extend(current)
            pages.append(
                {
                    "page": page_number,
                    "rows": len(current),
                    "text_comparison": "PASS",
                    "header_inherited": inherited_header,
                }
            )
            if page_number % 25 == 0:
                log("Industry source pages checked", source=path.name, pages=page_number)
    key = "old_code" if aliases else "code"
    codes = [row[key] for row in records]
    if len(set(codes)) != len(codes):
        raise ValueError("Duplicate code in source document")
    if aliases:
        if len({row["new_code"] for row in records}) != len(records):
            raise ValueError("Alias map is not one-to-one")
        if [row["number"] for row in records] != list(range(1, len(records) + 1)):
            raise ValueError("Alias row ordinals are not contiguous from one")
    elif codes != sorted(codes):
        raise ValueError("Stock-code-sorted source is out of order")
    return {
        "records": records,
        "pages": pages,
        "rows": len(records),
        "pymupdf_version": pymupdf.VersionBind,
    }


def review_industries(paths, protocol_path):
    """Extract source tables and register their hashes."""
    root = paths.root

    def inside(name):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or Path(name).is_absolute():
            raise ValueError("Industry source paths must stay inside the project")
        return path

    protocol_file = inside(protocol_path)
    protocol = read(protocol_file)
    if protocol.get("name") != "industry_sources":
        raise ValueError("Expected an industry source-review protocol")
    if not protocol.get("releases"):
        raise ValueError("Industry review has no registered releases")
    if protocol.get("followup", {}).get("column_alignment_tolerance_points", 0.1) != 0.1:
        raise ValueError("Alias continuation geometry differs from the reviewed convention")
    output = inside(protocol["output"])
    parent = (root / "data/processed").resolve()
    if not parent.is_relative_to(root) or not output.is_relative_to(parent) or output == parent:
        raise ValueError("Industry output must be under data/processed")
    checks = paths.output_path(protocol["checks_output"], "checks")
    if output.exists():
        raise FileExistsError("Industry source output already exists")
    manifest = read(paths.manifest)["files"]
    for name, expected in protocol["source_sha256"].items():
        if manifest.get(name) != expected or sha(inside(name)) != expected:
            raise ValueError(f"Industry source hash mismatch: {name}")
    if sha(inside(protocol["source_bundle"])) != protocol["source_bundle_sha256"]:
        raise ValueError("Industry review universe changed")
    configuration_sha = sha(root / "configs/project.toml")
    save(
        checks / "started.json",
        {
            "started_at": now(),
            "protocol_sha256": sha(protocol_file),
            "source_sha256": sha(Path(__file__)),
            "configuration_sha256": configuration_sha,
        },
        immutable=True,
    )
    codebook = read_codebook(inside(protocol["codebook"]))
    lookup = {(row["level"], row["code"]): row for row in codebook["records"]}
    save(output / "codebook.json", codebook, immutable=True)
    collection = read(inside(protocol["collection"]))
    sources = [{**release, "aliases": False} for release in protocol["releases"]]
    for article in collection["articles"]:
        for attachment in article.get("attachments", []):
            if "新旧代码对照表" in attachment.get("title", ""):
                sources.append(
                    {
                        "source": attachment["path"],
                        "source_sha256": attachment["sha256"],
                        "catalogue_date": article["published_date"],
                        "title": attachment["title"],
                        "aliases": True,
                    }
                )
    outcomes = []
    for source in sources:
        if protocol["source_sha256"].get(source["source"]) != source["source_sha256"]:
            raise ValueError("Unregistered industry attachment")
        name = source["catalogue_date"] + ("_aliases" if source["aliases"] else "")
        try:
            result = extract_pdf(
                inside(source["source"]),
                lookup,
                source["aliases"],
                followup=bool(protocol.get("followup")),
            )
            if "pages" in source and len(result["pages"]) != source["pages"]:
                raise ValueError("Registered source page count changed")
            target = output / (name + ".json.gz")
            dump_gzip(target, {**source, **result})
            outcomes.append(
                {
                    **source,
                    "status": "PARSED_AND_CROSS_CHECKED",
                    "rows": result["rows"],
                    "output": target.relative_to(root).as_posix(),
                    "sha256": sha(target),
                    "taxonomy_findings": sum(
                        len(row.get("taxonomy_findings", [])) for row in result["records"]
                    ),
                }
            )
        except (ValueError, KeyError, TypeError) as error:
            outcomes.append({**source, "status": "REVIEW_REQUIRED", "error": str(error)})
        save(checks / (name + ".json"), outcomes[-1], immutable=True)
        log(
            "Industry source reviewed", date=source["catalogue_date"], status=outcomes[-1]["status"]
        )
    if sha(root / "configs/project.toml") != configuration_sha:
        raise ValueError("Active configuration changed during industry source review")
    parsed = all(row["status"] == "PARSED_AND_CROSS_CHECKED" for row in outcomes)
    findings = sum(row.get("taxonomy_findings", 0) for row in outcomes)
    result = {
        "completed_at": now(),
        "status": (
            "SOURCE_CELLS_CHECKED_WITH_TAXONOMY_FINDINGS" if findings else "SOURCE_TABLES_CHECKED"
        )
        if parsed
        else "REVIEW_REQUIRED",
        "source_extraction_complete": parsed,
        "taxonomy_findings": findings,
        "protocol_sha256": sha(protocol_file),
        "source_sha256": sha(Path(__file__)),
        "codebook_sha256": sha(output / "codebook.json"),
        "sources": outcomes,
        "assignments_applied": 0,
        "configuration_sha256": configuration_sha,
        "scope": "All source rows, not a historical-availability or universe-membership certification.",
    }
    save(checks / "review.json", result, immutable=True)
    return result
