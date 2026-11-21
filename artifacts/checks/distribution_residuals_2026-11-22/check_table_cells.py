"""Resolve the recorded wrapped-header reader failure through labelled PDF grid cells."""

import datetime as dt
import re
import unicodedata
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


base = Path("artifacts/checks/distribution_residuals_2026-11-22")
protocol = read("configs/protocols/residual_table_cells.json")
parent = read(protocol["parent_protocol"]["path"])
failed = read(protocol["failed_check"]["path"])
require(
    sha(protocol["failed_check"]["path"]) == protocol["failed_check"]["sha256"], "Failure changed"
)
require(sha(protocol["source"]) == protocol["source_sha256"], "PDF changed")
require(
    sha("src/quantfolio/distributions.py") == failed["extractor_sha256"],
    "Reader changed after source regressions",
)
expected_failures = {(protocol["source"], "mupdf", field) for field in protocol["failed_fields"]}
actual_failures = {
    (row["source"], row["reader"], row["field"])
    for row in failed["source_checks"]
    if not row["passed"]
}
require(actual_failures == expected_failures, "Unexplained source failure")
expected = next(
    row["expected_unique_values_or_role"]
    for row in parent["observations"]
    if row["source"] == protocol["source"]
)
headers = [
    "股份类别",
    "股权登记日",
    "最后交易日",
    "除权(息)日",
    "新增无限售条件流通股份上市日",
    "现金红利发放日",
]
fields = {
    "股权登记日": "record_date",
    "除权(息)日": "ex_date",
    "新增无限售条件流通股份上市日": "share_list_date",
    "现金红利发放日": "cash_pay_date",
}
cells, field_checks = [], []
with fitz.open(protocol["source"]) as pdf:
    for number in protocol["physical_pages"]:
        page = pdf[number - 1]
        matches = []
        for table in page.find_tables().tables:
            rows = table.extract()
            if rows and [compact(value or "") for value in rows[0]] == headers:
                matches.append((table, rows))
        require(len(matches) == 1, "Missing or ambiguous six-column table")
        table, rows = matches[0]
        require(len(rows) == 2 and len(rows[1]) == 6, "Unexpected table shape")
        require(compact(rows[1][0]) == "A股", "Other share class")
        require(
            compact(rows[1][2]) in {"-", "—", "–", "−", "ー"}, "Last-trading cell is not a dash"
        )
        for row_number, row in enumerate(rows):
            rectangles = table.rows[row_number].cells
            require(len(rectangles) == 6 and all(rectangles), "Missing cell geometry")
            for column, (value, rectangle) in enumerate(zip(row, rectangles)):
                words = page.get_text("words", clip=fitz.Rect(rectangle))
                word_text = "".join(word[4] for word in words)
                require(compact(word_text) == compact(value), "Cell text/word geometry differs")
                require(
                    all(rectangle[0] <= (w[0] + w[2]) / 2 <= rectangle[2] for w in words),
                    "Word center outside labelled cell",
                )
                cells.append(
                    {
                        "page": number,
                        "row": row_number,
                        "column": column,
                        "column_label": headers[column],
                        "rectangle": list(rectangle),
                        "table_text": value,
                        "word_text": word_text,
                        "passed": True,
                    }
                )
                if row_number == 1 and headers[column] in fields:
                    field = fields[headers[column]]
                    actual = dt.datetime.strptime(compact(value), "%Y/%m/%d").date().isoformat()
                    require(actual == expected[field], (number, field, actual, expected[field]))
                    field_checks.append(
                        {"page": number, "field": field, "value": actual, "passed": True}
                    )
require(len(cells) == 24 and len(field_checks) == 8, "Incomplete independent cell coverage")
save(
    base / "table_cell_closure.json",
    {
        "status": "EXPLAINED_LAYOUT_FAILURES_CHECKED",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "protocol_sha256": sha("configs/protocols/residual_table_cells.json"),
        "failed_regressions_sha256": sha(protocol["failed_check"]["path"]),
        "extractor_sha256": failed["extractor_sha256"],
        "reader": {"name": "PyMuPDF", "version": fitz.VersionBind},
        "source": protocol["source"],
        "source_sha256": protocol["source_sha256"],
        "cells": cells,
        "field_checks": field_checks,
        "unchanged_passing_regressions": sum(row["passed"] for row in failed["source_checks"]),
        "source_regression_gate_passed": True,
        "facts_accepted": 0,
        "scope": "The original four flattened-text failures remain. All four fixed date expectations agree with labelled cells on both repeated tables; other source regressions still pass. This is not full residual financial acceptance.",
    },
    immutable=True,
)
print("TABLE_CELLS_CHECKED", len(cells), "cells", len(field_checks), "labelled date checks")
