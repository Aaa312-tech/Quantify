"""Reconcile disclosed ETF tables without turning stale reports into daily holdings."""

import re
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

ROOT = Path.cwd()
P = read(ROOT / "configs/protocols/etf_holdings.json")
OUT = ROOT / P["processed_output"]
CHECKS = ROOT / P["review_output"]
NUMBER = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\Z")


def compact(text):
    import unicodedata

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def number(text):
    text = compact(text)
    if text == "-":
        return None
    if not NUMBER.fullmatch(text):
        raise ValueError("Not a displayed decimal: " + repr(text))
    return str(Decimal(text.replace(",", "")))


def headings(text, chapter):
    return list(re.finditer(r"(?m)^\s*(" + str(chapter) + r"\.(\d+)(?:\.\d+)*)(?=\s|$)", text))


def last_section(text, chapter, previous, omitted_numbers):
    if not omitted_numbers:
        matches = headings(text, chapter)
        return matches[-1][1] if matches else previous
    # Printed numbers are missing from both text readers in three reviewed PDFs.
    # Root keys describe the visible category; they are not extracted number strings.
    markers = {
        1: ["期末基金资产组合情况"],
        2: ["期末按行业分类的股票投资组合", "期末按行业分类的境内股票投资组合"],
        3: [
            "期末按公允价值占基金资产净值比例大小排序的前十名股票投资明细",
            "期末按公允价值占基金资产净值比例大小排序的所有股票投资明细",
        ],
        4: ["期末按债券品种分类的债券投资组合", "报告期内股票投资组合的重大变动"],
    }
    text = compact(text)
    found = [
        (text.rfind(marker), kind)
        for kind, values in markers.items()
        for marker in values
        if marker in text
    ]
    return f"{chapter}.{max(found)[1]}" if found else previous


def proof(cells, text):
    joined = "".join(map(compact, cells))
    offset = compact(text).find(joined)
    if offset < 0:
        raise ValueError("Independent reader does not contain the complete row in cell order")
    return {"pypdf_compact_offset": offset, "characters": len(joined)}


def rounding_matches(value, weight, denominator):
    amount = Fraction(Decimal(value))
    printed = Decimal(weight)
    half_unit = Fraction(Decimal(10) ** printed.as_tuple().exponent) / 2
    actual = amount * 100 / Fraction(Decimal(denominator))
    return Fraction(printed) - half_unit <= actual <= Fraction(printed) + half_unit


def reported_nav(pages, doc):
    evidence = {}
    year, month, day = map(int, doc["title_period_end"].split("-"))
    for reader in ("fitz", "pypdf"):
        text = "".join(compact(page[reader]) for page in pages if page["page"] <= 10)
        matches = list(re.finditer(r"(?:期末基金资产净值)+(?:\(元\))?([\d,]+\.\d{2})", text))
        if len(matches) != 1:
            raise ValueError(f"{reader} NAV row is missing or ambiguous")
        match = matches[0]
        before = text[: match.start()]
        if doc["report_type"] == "annual":
            context = before[before.rfind("3.1.2") :]
            dates = re.findall(r"(20\d{2})年+末", context)
            if not dates or int(dates[0]) != year:
                raise ValueError(f"{reader} current annual NAV column is not identified")
        else:
            context = before[-1500:]
            if not re.search(
                str(year) + r"年+0?" + str(month) + r"月+0?" + str(day) + r"日+", context
            ):
                raise ValueError(f"{reader} NAV reporting-period header is not identified")
        evidence[reader] = {
            "amount_cny": number(match[1]),
            "label_and_amount": match[0],
            "period_context": context,
            "compact_offset": match.start(),
        }
    if evidence["fitz"]["amount_cny"] != evidence["pypdf"]["amount_cny"]:
        raise ValueError("Independent reported NAV amounts differ")
    return {"amount_cny": evidence["fitz"]["amount_cny"], "evidence": evidence}


def review(doc):
    result = {
        k: doc[k]
        for k in (
            "symbol",
            "title",
            "source",
            "source_sha256",
            "catalogue_date",
            "report_type",
            "title_period_end",
        )
    }
    if doc["status"] != "IDENTITY_AND_SECTION_REVIEWED":
        return {
            **result,
            "status": "REVIEW_REQUIRED",
            "errors": ["Identity/section review not accepted"],
        }
    path = ROOT / doc["extracted_pages"]
    if sha(path) != doc["extraction_sha256"]:
        raise ValueError("Page extraction changed")
    pages = load_gzip(path)["pages"]
    chapter = doc["portfolio_chapter"]
    result.update(
        identity=doc["identity"],
        source_extraction_sha256=doc["extraction_sha256"],
        stock_schedule_coverage="TOP_HOLDINGS_ONLY"
        if doc["report_type"] == "quarterly"
        else "ALL_REPORTED_STOCKS",
    )
    groups, errors, ignored, row_proofs = {}, [], [], 0
    section = None
    for page in pages:
        if not doc["portfolio_first_page"] <= page["page"] <= doc["portfolio_last_page"]:
            continue
        for table_index, table in enumerate(page.get("tables", [])):
            section = last_section(
                table["preceding_text"],
                chapter,
                section,
                doc.get("heading_numbers_omitted_from_text", False),
            )
            minor = int(section.split(".")[1]) if section else None
            if minor not in (1, 2, 3):
                ignored.append({"page": page["page"], "table": table_index, "section": section})
                continue
            kind = {1: "assets", 2: "sectors", 3: "stocks"}[minor]
            expected = 6 if kind == "stocks" else 4
            if table["col_count"] != expected:
                errors.append(
                    {
                        "page": page["page"],
                        "table": table_index,
                        "section": section,
                        "error": f"Expected {expected} columns, received {table['col_count']}",
                    }
                )
                continue
            group = groups.setdefault(
                section, {"kind": kind, "section": section, "headers": [], "rows": []}
            )
            for row_index, cells in enumerate(table["cells"]):
                raw = [cell or "" for cell in cells]
                clean = list(map(compact, raw))
                provenance = {
                    "page": page["page"],
                    "table": table_index,
                    "row": row_index,
                    "cells": raw,
                    "bbox": table["row_boxes"][row_index],
                }
                if not any(clean):
                    continue
                if clean[0] in ("序号", "代码") or clean[1] in ("股票代码", "行业类别", "项目"):
                    group["headers"].append(provenance)
                    continue
                # A source row may wrap its name onto the next physical page.
                if not clean[0] and clean[1] and not any(clean[2:]) and group["rows"]:
                    previous = group["rows"][-1]
                    if row_index == 0 and previous["fragments"][-1]["page"] + 1 == page["page"]:
                        try:
                            provenance["proof"] = proof(raw, page["pypdf"])
                            previous["name"] += clean[1]
                            previous["fragments"].append(provenance)
                            row_proofs += 1
                            continue
                        except Exception as error:
                            errors.append({**provenance, "section": section, "error": str(error)})
                            continue
                try:
                    provenance["proof"] = proof(raw, page["pypdf"])
                    if kind == "stocks":
                        if (
                            not clean[0].isdigit()
                            or not re.fullmatch(r"\d{6}", clean[1])
                            or not clean[2]
                        ):
                            raise ValueError("Invalid stock ordinal, code or name")
                        item = {
                            "ordinal": int(clean[0]),
                            "code": clean[1],
                            "name": clean[2],
                            "quantity_shares": number(clean[3]),
                            "market_value_cny": number(clean[4]),
                            "percent_nav": number(clean[5]),
                        }
                        if any(
                            item[key] is None
                            for key in ("quantity_shares", "market_value_cny", "percent_nav")
                        ):
                            raise ValueError("Stock amount or percentage is a dash")
                    elif kind == "sectors":
                        total = clean[0] == "合计" or clean[1] == "合计"
                        if not total and not re.fullmatch("[A-S]", clean[0]):
                            raise ValueError("Unrecognized source sector code")
                        item = {
                            "code": "TOTAL" if total else clean[0],
                            "name": clean[1],
                            "market_value_cny": number(clean[2]),
                            "percent_nav": number(clean[3]),
                        }
                    else:
                        if clean[0] and not clean[0].isdigit():
                            raise ValueError("Invalid asset ordinal")
                        item = {
                            "ordinal": int(clean[0]) if clean[0] else None,
                            "name": clean[1],
                            "amount_cny": number(clean[2]),
                            "percent_total_assets": number(clean[3]),
                        }
                    item["fragments"] = [provenance]
                    group["rows"].append(item)
                    row_proofs += 1
                except Exception as error:
                    errors.append({**provenance, "section": section, "error": str(error)})
        section = last_section(
            page["fitz"], chapter, section, doc.get("heading_numbers_omitted_from_text", False)
        )
    checks = []
    try:
        nav = reported_nav(pages, doc)
    except Exception as error:
        nav = None
        errors.append({"error": str(error), "field": "reported_nav"})
    for group in groups.values():
        rows, kind = group["rows"], group["kind"]
        header_text = "".join(
            compact(cell) for header in group["headers"] for cell in header["cells"]
        )
        denominator = "基金总资产" if kind == "assets" else "基金资产净值"
        group["denominator"] = "TOTAL_ASSETS" if kind == "assets" else "NET_ASSETS"
        if denominator not in header_text:
            errors.append(
                {
                    "section": group["section"],
                    "error": "Percentage denominator is not stated in extracted headers",
                }
            )
        if not rows:
            errors.append({"section": group["section"], "error": "No extracted rows"})
            continue
        if kind == "stocks":
            ordinals = [r["ordinal"] for r in rows]
            codes = [r["code"] for r in rows]
            if ordinals != list(range(1, len(rows) + 1)) or len(codes) != len(set(codes)):
                errors.append(
                    {
                        "section": group["section"],
                        "error": "Stock ordinals are not continuous or codes repeat",
                    }
                )
        elif kind == "sectors":
            codes = [r["code"] for r in rows]
            if codes != list("ABCDEFGHIJKLMNOPQRS") + ["TOTAL"]:
                errors.append(
                    {
                        "section": group["section"],
                        "error": "Source sector table is not A-S plus total",
                        "codes": codes,
                    }
                )
            else:
                subtotal = sum(
                    (Decimal(r["market_value_cny"] or "0") for r in rows[:-1]), Decimal(0)
                )
                total = Decimal(rows[-1]["market_value_cny"] or "0")
                checks.append(
                    {
                        "section": group["section"],
                        "check": "sector_amount_sum",
                        "difference_cny": str(subtotal - total),
                    }
                )
                if subtotal != total:
                    errors.append(
                        {
                            "section": group["section"],
                            "error": "Sector values do not sum to displayed total",
                        }
                    )
        else:
            totals = [r for r in rows if r["name"] == "合计"]
            numbered = [r for r in rows if r["ordinal"] is not None and r["name"] != "合计"]
            if len(totals) != 1 or not numbered:
                errors.append(
                    {
                        "section": group["section"],
                        "error": "Asset composition total missing or ambiguous",
                    }
                )
            else:
                total = Decimal(totals[0]["amount_cny"])
                subtotal = sum((Decimal(r["amount_cny"] or "0") for r in numbered), Decimal(0))
                checks.append(
                    {
                        "section": group["section"],
                        "check": "asset_amount_sum",
                        "difference_cny": str(subtotal - total),
                    }
                )
                if subtotal != total:
                    errors.append(
                        {
                            "section": group["section"],
                            "error": "Asset values do not sum to displayed total",
                        }
                    )
                for row in rows:
                    if (
                        row["amount_cny"] is not None
                        and row["percent_total_assets"] is not None
                        and not rounding_matches(
                            row["amount_cny"], row["percent_total_assets"], str(total)
                        )
                    ):
                        errors.append(
                            {
                                "section": group["section"],
                                "error": "Total-asset percentage rounding differs",
                                "name": row["name"],
                            }
                        )
        if kind in ("stocks", "sectors") and nav is not None:
            for row in rows:
                if (
                    row["market_value_cny"] is not None
                    and row["percent_nav"] is not None
                    and not rounding_matches(
                        row["market_value_cny"], row["percent_nav"], nav["amount_cny"]
                    )
                ):
                    errors.append(
                        {
                            "section": group["section"],
                            "error": "NAV percentage rounding differs",
                            "name": row["name"],
                        }
                    )
    stocks = [r for g in groups.values() if g["kind"] == "stocks" for r in g["rows"]]
    sectors = [
        r
        for g in groups.values()
        if g["kind"] == "sectors"
        for r in g["rows"]
        if r["code"] == "TOTAL"
    ]
    if doc["report_type"] != "quarterly" and stocks and sectors:
        stock_sum = sum((Decimal(r["market_value_cny"]) for r in stocks), Decimal(0))
        sector_sum = sum((Decimal(r["market_value_cny"] or "0") for r in sectors), Decimal(0))
        checks.append(
            {
                "check": "all_stocks_vs_sector_total",
                "stock_market_value_cny": str(stock_sum),
                "sector_market_value_cny": str(sector_sum),
                "difference_cny": str(stock_sum - sector_sum),
            }
        )
        if stock_sum != sector_sum:
            errors.append({"error": "Full stock holdings do not equal sector total"})
    absence = {}
    if not stocks:
        for reader in ("fitz", "pypdf"):
            text = "".join(
                compact(page[reader])
                for page in pages
                if page["page"] >= doc["portfolio_first_page"]
            )
            matches = re.findall(r"本基金(?:本)?报告期末(?:未持有|未投资)股票", text)
            absence[reader] = matches
            if not matches:
                errors.append({"error": reader + " has no explicit end-period absence of stocks"})
    asset_rows = [r for g in groups.values() if g["kind"] == "assets" for r in g["rows"]]
    equity = [r for r in asset_rows if r["name"] == "其中:股票"]
    if len(equity) != 1:
        errors.append({"error": "Equity amount in asset composition is not uniquely identified"})
    elif sectors:
        sector_total = sum((Decimal(r["market_value_cny"] or "0") for r in sectors), Decimal(0))
        difference = Decimal(equity[0]["amount_cny"] or "0") - sector_total
        checks.append({"check": "asset_equity_vs_sector_total", "difference_cny": str(difference)})
        if difference:
            errors.append(
                {
                    "error": "Asset equity and sector values differ; source footnotes require reconciliation",
                    "difference_cny": str(difference),
                }
            )
    result.update(
        status="TABLES_EXTRACTED_RECONCILIATION_PENDING" if not errors else "REVIEW_REQUIRED",
        groups=list(groups.values()),
        checks=checks,
        errors=errors,
        independent_row_fragments=row_proofs,
        ignored_tables=ignored,
        reported_nav_validation="DUAL_READER_FIELD_CHECKED" if nav else "PENDING",
        reported_nav=nav,
        stock_absence=absence,
        derivatives_exposure_validation="PENDING",
    )
    return result


def main():
    extraction = read(CHECKS / "extraction_reviewed.json")
    if (CHECKS / "tables_review.json").exists():
        raise FileExistsError("Do not overwrite a completed table review")
    save(
        CHECKS / "tables_protocol.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "extraction_sha256": sha(CHECKS / "extraction_reviewed.json"),
            "source_protocol_sha256": sha(ROOT / "configs/protocols/etf_holdings.json"),
        },
        immutable=True,
    )
    documents = [r for r in extraction["documents"] if not r.get("publication_notice", False)]
    results = []
    for index, doc in enumerate(documents, 1):
        result = review(doc)
        results.append(result)
        if index % 25 == 0:
            print(
                index, "/", len(documents), dict(Counter(r["status"] for r in results)), flush=True
            )
    dump_gzip(OUT / "table_observations.json.gz", results)
    save(
        CHECKS / "tables_review.json",
        {
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "observations_sha256": sha(OUT / "table_observations.json.gz"),
            "counts": dict(Counter(r["status"] for r in results)),
            "documents": len(results),
            "independent_row_fragments": sum(
                r.get("independent_row_fragments", 0) for r in results
            ),
            "activation_status": "NOT_ACTIVATED",
            "model_inputs_changed": False,
            "remaining": [
                "Resolve every extraction/reconciliation error",
                "Independent source NAV checks",
                "Cross-report/publication-time review",
                "CAPCO mapping and dated exposure controls",
                "Derivative and residual exposure treatment",
            ],
        },
        immutable=True,
    )
    print("FINISHED", Counter(r["status"] for r in results), flush=True)


if __name__ == "__main__":
    main()
