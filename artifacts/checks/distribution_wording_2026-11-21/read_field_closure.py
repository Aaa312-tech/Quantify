"""Read an explicitly A-share gross summary in addition to implementation paragraphs."""

import importlib.util
import re
from decimal import Decimal
from pathlib import Path

import fitz

READER = Path.cwd() / "artifacts/checks/distribution_wording_2026-11-21/read_primary_fields.py"
SPEC = importlib.util.spec_from_file_location("wording_paragraph_reader", READER)
PRIOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRIOR)
compact = PRIOR.PRIOR.compact


def extract(pdf):
    evidence, layouts, rejected, first = PRIOR.extract(pdf)
    with fitz.open(pdf) as document:
        text = compact(document[0].get_text(sort=True))
    if "重要内容提示:" in text and "每股分配比例" in text:
        summary = text.split("每股分配比例", 1)[1].split("相关日期", 1)[0]
        for match in re.finditer(
            r"A股每股(?:派发)?现金(?:股息|红利|股利|分红)(?:人民币)?([0-9]+(?:\.[0-9]+)?)元\(含税\)",
            summary,
        ):
            evidence.setdefault("cash_per_share", []).append(
                {
                    "value": str(Decimal(match[1])),
                    "page": 1,
                    "method": "labelled_a_share_gross_summary",
                    "context": match[0],
                }
            )
            layouts.append({"page": 1, "family": "labelled_a_share_gross_summary"})
    return evidence, layouts, rejected, first
