"""Extend the frozen independent reader for two explicit gross-cash layouts."""

import importlib.util
import re
from decimal import Decimal
from pathlib import Path

import fitz

READER = Path.cwd() / "artifacts/checks/distribution_terms_2026-11-21/independent_fields.py"
SPEC = importlib.util.spec_from_file_location("prior_independent_fields", READER)
PRIOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRIOR)


def extract(pdf):
    evidence, layouts, rejected, first = PRIOR.extract(pdf)
    with fitz.open(pdf) as document:
        for number, page in enumerate(document, 1):
            text = PRIOR.compact(page.get_text(sort=True))
            matches = []
            if "重要内容提示:" in text and "每股分配比例" in text:
                summary = text.split("每股分配比例", 1)[1].split("相关日期", 1)[0]
                for match in re.finditer(
                    r"A股每股现金红利([0-9]+(?:\.[0-9]+)?)元\(含税\)", summary
                ):
                    matches.append((match[0], Decimal(match[1]), "labelled_a_share_gross_summary"))
            headings = list(re.finditer(r"[一二三四五六七八九十]+、", text))
            for index, heading in enumerate(headings):
                end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                section = text[heading.end() : end]
                if not section.startswith("权益分派方案"):
                    continue
                for match in re.finditer(
                    r"向全体股东每10股派(?:发)?([0-9]+(?:\.[0-9]+)?)元\(含税\)", section
                ):
                    matches.append(
                        (match[0], Decimal(match[1]) / 10, "all_shareholder_gross_per_ten")
                    )
            for text, amount, family in matches:
                evidence.setdefault("cash_per_share", []).append(
                    {
                        "value": str(amount),
                        "page": number,
                        "method": family,
                        "context": text,
                    }
                )
                layouts.append({"page": number, "family": family})
    return evidence, layouts, rejected, first
