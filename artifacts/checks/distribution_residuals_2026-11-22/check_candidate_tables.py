"""Cross-check labelled PDF grid cells across every proposed residual primary notice."""

import datetime as dt
import re
import unicodedata
from collections import Counter
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def date_value(value):
    match = re.fullmatch(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?", compact(value))
    return dt.date(*(int(part) for part in match.groups())).isoformat() if match else None


base = Path("artifacts/checks/distribution_residuals_2026-11-22")
protocol_file = Path("configs/protocols/residual_field_review.json")
protocol = read(protocol_file)
labels = {
    "股权登记日": "record_date",
    "除权(息)日": "ex_date",
    "除权除息日": "ex_date",
    "除息日": "ex_date",
    "新增无限售条件流通股份上市日": "share_list_date",
    "现金红利发放日": "cash_pay_date",
}
records, totals = [], Counter()
for number, record in enumerate(protocol["records"], 1):
    source = record["source"]
    require(sha(source["path"]) == source["sha256"], "Primary PDF changed")
    expected = {
        field: record["source_identity_fields"][field] for field in ("record_date", "ex_date")
    } | record["proposed_fields"]
    observed = {field: [] for field in expected}
    tables, problems = [], []
    with fitz.open(source["path"]) as pdf:
        page_count = len(pdf)
        for page_number, page in enumerate(pdf, 1):
            for table in page.find_tables().tables:
                rows = table.extract()
                if not rows:
                    continue
                headers = [compact(value) for value in rows[0]]
                fields = [labels.get(label) for label in headers]
                if "record_date" not in fields or "ex_date" not in fields:
                    continue
                selected = [field for field in fields if field]
                if len(selected) != len(set(selected)):
                    problems.append(
                        {"page": page_number, "headers": headers, "reason": "AMBIGUOUS_COLUMNS"}
                    )
                    continue
                if any(
                    label not in labels and label not in {"股份类别", "最后交易日"}
                    for label in headers
                ):
                    problems.append(
                        {"page": page_number, "headers": headers, "reason": "UNSUPPORTED_COLUMNS"}
                    )
                    continue
                cells, row_values = [], []
                share_column = headers.index("股份类别") if "股份类别" in headers else None
                for row_number, row in enumerate(rows):
                    if (
                        row_number
                        and share_column is not None
                        and compact(row[share_column]) != "A股"
                    ):
                        continue
                    rectangles = table.rows[row_number].cells
                    if (
                        len(row) != len(headers)
                        or len(rectangles) != len(headers)
                        or not all(rectangles)
                    ):
                        problems.append(
                            {"page": page_number, "row": row_number, "reason": "INCOMPLETE_CELLS"}
                        )
                        continue
                    for column, (value, rectangle) in enumerate(zip(row, rectangles)):
                        words = page.get_text("words", clip=fitz.Rect(rectangle))
                        word_text = "".join(word[4] for word in words)
                        agrees = compact(word_text) == compact(value)
                        cells.append(
                            {
                                "row": row_number,
                                "column": column,
                                "label": headers[column],
                                "rectangle": list(rectangle),
                                "table_text": value,
                                "word_text": word_text,
                                "cell_text_agrees": agrees,
                            }
                        )
                        field = fields[column]
                        if row_number and field:
                            try:
                                parsed = date_value(value)
                            except ValueError:
                                parsed = None
                            row_values.append(
                                {"field": field, "value": parsed, "cell_text_agrees": agrees}
                            )
                cells_agree = bool(cells) and all(cell["cell_text_agrees"] for cell in cells)
                tables.append(
                    {
                        "page": page_number,
                        "rectangle": list(table.bbox),
                        "headers": headers,
                        "cells": cells,
                        "rows": row_values,
                        "all_cells_agree": cells_agree,
                    }
                )
                if cells_agree:
                    for row in row_values:
                        if row["field"] in observed:
                            observed[row["field"]].append(
                                {"page": page_number, "value": row["value"]}
                            )
                else:
                    problems.append(
                        {
                            "page": page_number,
                            "headers": headers,
                            "reason": "CELL_TEXT_DISAGREEMENT",
                        }
                    )
    checks = []
    for field, wanted in expected.items():
        values = {row["value"] for row in observed[field]}
        if not values:
            status = "NO_GRID_EVIDENCE_NARRATIVE_REVIEW_REQUIRED"
        elif values == {wanted}:
            status = "LABELLED_GRID_AGREES"
        else:
            status = "GRID_CONFLICT_OR_INVALID_VALUE"
        totals[status] += 1
        checks.append(
            {"field": field, "expected": wanted, "observed": observed[field], "status": status}
        )
    records.append(
        {
            "symbol": record["symbol"],
            "ex_date": record["ex_date"],
            "source": source,
            "physical_pages_scanned": page_count,
            "tables": tables,
            "table_problems": problems,
            "field_checks": checks,
        }
    )
    if number % 20 == 0 or number == len(protocol["records"]):
        print("CANDIDATE_TABLES_CHECKED", number, "of", len(protocol["records"]), flush=True)
require(len(records) == 95, "Incomplete proposed-primary coverage")
save(
    base / "candidate_table_review.json",
    {
        "status": "GRID_REVIEW_COMPLETE_FULL_FIELD_ACCEPTANCE_PENDING",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "protocol_sha256": sha(protocol_file),
        "reader": {"name": "PyMuPDF", "version": fitz.VersionBind},
        "records": records,
        "field_status_counts": dict(totals),
        "facts_accepted": 0,
        "limitations": "Grid checks retain named columns, share class, cell boxes and direct words. They do not independently certify narrative cash, announcement roles, source time, amendments or missing grid evidence.",
    },
    immutable=True,
)
print("GRID_FIELD_COUNTS", dict(totals), flush=True)
