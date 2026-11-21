"""Check complete reader inventories, original cells and exact contract reconciliations."""

import re
import unicodedata
from collections import Counter
from fractions import Fraction
from pathlib import Path

from quantfolio.storage import load_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
SOURCE = ROOT / "data/processed/etf_exposures_2026-11-21/financial_observations.json.gz"
CODE = re.compile(r"(?<![A-Za-z])(?:(?:IF|IC|IH|IM|TF|TS|TL|T)\d{4}|Au\(T\+D\))")


def clean(value):
    return "".join(unicodedata.normalize("NFKC", value or "").split())


def amount(value):
    return Fraction(clean(value).replace(",", ""))


def verify_tokens(page, cells):
    tokens = "".join(clean(c) for c in cells)
    if not tokens or any(tokens not in clean(page[reader]) for reader in ("fitz", "pypdf")):
        raise ValueError("Independent original row/text comparison failed")


def run():
    docs = {d["source"]: d for d in read(CHECKS / "notes_reviewed.json")["documents"]}
    results = load_gzip(SOURCE)
    counters = Counter()
    reconciliations, failures = [], []
    for result in results:
        doc = docs[result["source"]]
        try:
            original = load_gzip(ROOT / doc["extracted_pages"])
            pages = {p["page"]: p for p in original["pages"] if p["page"] in doc["portfolio_pages"]}
            note = None
            if doc["financial_notes"]:
                note = load_gzip(ROOT / doc["financial_notes"])
                pages.update({p["page"]: p for p in note["pages"]})
            texts = {}
            for reader in ("fitz", "pypdf"):
                texts[reader] = (
                    note.get("derivative_boundaries", {}).get(reader, {}).get("section_text", "")
                    if note
                    else "\n".join(p[reader] for p in pages.values())
                )
            rows = [r for t in result["contracts"]["tables"] for r in t["rows"]]
            expected = CODE.findall(" ".join(r["code"] + " " + r["name"] for r in rows))
            for reader, text in texts.items():
                if CODE.findall(clean(text)) != expected:
                    raise ValueError(f"{reader}: complete ordered contract inventory differs")
                counters["complete_reader_inventories"] += 1
            if (
                note
                and any("合同/名义" in clean(t) for t in texts.values())
                and len(result["nominal"]["tables"]) != 1
            ):
                raise ValueError("Nominal table missing from complete financial-note text")
            for row in rows:
                fragments = row.get("fragments") or [
                    {
                        "page": row["page"],
                        "table": row["table"],
                        "row": row["row"],
                        "cells": row["source_cells"],
                    }
                ]
                original_cells = []
                for fragment in fragments:
                    page = pages[fragment["page"]]
                    cells = page["tables"][fragment["table"]]["cells"][fragment["row"]]
                    if cells != fragment["cells"]:
                        raise ValueError("Contract fragments differ from frozen source cells")
                    verify_tokens(page, cells)
                    original_cells.append(cells)
                merged = [
                    "".join(clean(c[i]) for c in original_cells)
                    for i in range(len(original_cells[0]))
                ]
                if merged != [clean(c) for c in row["source_cells"]]:
                    raise ValueError("Merged contract cells differ")
                if merged[:2] != [row["code"], row["name"]]:
                    raise ValueError("Contract identifiers differ")
                for index, field in enumerate(
                    (
                        "reported_quantity",
                        "reported_market_value_cny",
                        "reported_fair_value_change_cny",
                    ),
                    2,
                ):
                    if amount(merged[index]) != Fraction(row[field]):
                        raise ValueError("Contract numeric field differs")
                counters["contract_rows"] += 1
                counters["negative_pnl_rows"] += Fraction(row["reported_fair_value_change_cny"]) < 0
            if rows:
                last = max((r["page"], r["table"], r["row"]) for r in rows)
                found = None
                for page_number, page in sorted(pages.items()):
                    for ti, table in enumerate(page.get("tables", [])):
                        for ri, cells in enumerate(table["cells"]):
                            if (page_number, ti, ri) <= last:
                                continue
                            label = clean(cells[0])
                            if label == "合计" or label == "公允价值变动总额合计(元)":
                                found = (page, cells)
                                break
                        if found:
                            break
                    if found:
                        break
                if found is None:
                    raise ValueError("Contract current-P&L total is missing")
                page, cells = found
                verify_tokens(page, cells)
                calculated = sum(Fraction(r["reported_fair_value_change_cny"]) for r in rows)
                if amount(cells[-1]) != calculated:
                    raise ValueError("Contract P&L sum does not match source footer")
                counters["contract_footer_reconciliations"] += 1
            for table in result["nominal"]["tables"]:
                named = []
                for row in table["rows"]:
                    fragments = row.get("fragments") or [
                        {
                            "page": table["page"],
                            "table": table["table"],
                            "row": row["row"],
                            "cells": row["source_cells"],
                        }
                    ]
                    for fragment in fragments:
                        page = pages[fragment["page"]]
                        cells = page["tables"][fragment["table"]]["cells"][fragment["row"]]
                        if fragment["cells"] != cells:
                            raise ValueError("Nominal fragment differs")
                        verify_tokens(page, cells)
                    cells = fragments[0]["cells"]
                    for index, field in enumerate(
                        (
                            "contract_nominal_cny",
                            "fair_value_asset_cny",
                            "fair_value_liability_cny",
                        ),
                        1,
                    ):
                        expected_value = (
                            None if clean(cells[index]) in ("", "-", "—") else amount(cells[index])
                        )
                        actual_value = None if row[field] is None else Fraction(row[field])
                        if actual_value != expected_value:
                            raise ValueError("Nominal amount or dash differs")
                    named.append(row)
                total = named[-1]
                if total["name"] != "合计":
                    raise ValueError("Nominal total missing")
                fields = [r for r in named[:-1] if "股指期货" not in r["name"]]
                if {r["name"] for r in fields} != {
                    "利率衍生工具",
                    "货币衍生工具",
                    "权益衍生工具",
                    "其他衍生工具",
                }:
                    raise ValueError("Nominal category inventory differs")
                if total["contract_nominal_cny"] is not None:
                    if sum(Fraction(r["contract_nominal_cny"] or "0") for r in fields) != Fraction(
                        total["contract_nominal_cny"]
                    ):
                        raise ValueError("Nominal categories do not sum")
                    if rows:
                        difference = sum(
                            Fraction(r["reported_market_value_cny"])
                            - Fraction(r["reported_fair_value_change_cny"])
                            for r in rows
                        ) - Fraction(total["contract_nominal_cny"])
                        if difference:
                            raise ValueError("Market-value/nominal reconciliation failed")
                        reconciliations.append(
                            {"source": result["source"], "difference_numerator": 0}
                        )
                elif any(r["contract_nominal_cny"] is not None for r in fields):
                    raise ValueError("A nonempty nominal row has a dash total")
                counters["nominal_tables"] += 1
            for items in result["explicit_absence_clauses"].values():
                for item in items:
                    verify_tokens(pages[item["page"]], [item["phrase"]])
                    counters["absence_clauses"] += 1
            for item in result["security_lending"]["reported_amounts"]:
                verify_tokens(pages[item["page"]], [item["phrase"]])
                counters["lending_amount_clauses"] += 1
            counters["reports_passed"] += 1
        except (ValueError, KeyError, StopIteration) as error:
            failures.append({"source": result["source"], "error": str(error)})
    return {
        "status": "PASS" if not failures and counters["reports_passed"] == 528 else "FAIL",
        "completed_at": now(),
        "source_sha256": sha(Path(__file__)),
        "input_sha256": sha(SOURCE),
        "counts": dict(counters),
        "failures": failures,
        "nominal_market_reconciliations": reconciliations,
        "source_dashes_preserved": True,
        "complete_economic_exposure_certified": False,
        "limitations": [
            "Complete known-contract code inventories are checked within the extracted relevant sections; unknown types and unspecified positions remain unknown.",
            "An explicit balance absence is not a certificate of all economic derivatives.",
            "Some contract currencies inherit the financial-note unit; source provenance distinguishes this from a table-local unit.",
            "Lending recognition clauses do not by themselves certify inclusion in every holdings schedule.",
        ],
    }


if __name__ == "__main__":
    result = run()
    save(CHECKS / "financial_audit.json", result, immutable=True)
    print({k: v for k, v in result.items() if k != "nominal_market_reconciliations"})
