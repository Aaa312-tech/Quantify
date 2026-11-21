"""Review complete source cells, including rows physically split across PDF pages."""

import copy
import importlib.util
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

import fitz

from quantfolio.storage import digest, dump_gzip, load_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_holdings_2026-11-21"
OUT = ROOT / "data/processed/etf_holdings_cells_2026-11-21"
spec = importlib.util.spec_from_file_location(
    "etf_initial_table_review", CHECKS / "review_source.py"
)
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
compact, number = prior.compact, prior.number
MANUAL = read(CHECKS / "manual_boundary_protocol.json")
MANUAL_REVIEW = read(CHECKS / "manual_boundaries_reviewed.json")
if (
    MANUAL_REVIEW["protocol_sha256"] != sha(CHECKS / "manual_boundary_protocol.json")
    or MANUAL_REVIEW["status"] != "VISUALLY_REVIEWED"
    or MANUAL_REVIEW["render_sha256"] != sha(CHECKS / "visuals/manual_boundaries/render.json")
):
    raise ValueError("Manual boundary evidence is not verified")


def manual_boundary(doc, left, right):
    for case in MANUAL["cases"]:
        if case["source"] != doc["source"] or case["source_sha256"] != doc["source_sha256"]:
            continue
        if case["id"] not in MANUAL_REVIEW["reviewed_case_ids"]:
            continue
        if any(
            left[key] != case["left"][key] or right[key] != case["right"][key]
            for key in ("page", "table", "row")
        ):
            continue
        first = left["cells"][: len(case["left"]["cells"])]
        if first != case["left"]["cells"] or right["cells"] != case["right"]["cells"]:
            raise ValueError("Manual source fragments differ")
        return case["id"]
    return None


def section_at(text, doc, previous):
    chapter = doc["portfolio_chapter"]
    if doc.get("heading_numbers_omitted_from_text"):
        return prior.last_section(text, chapter, previous, True)
    pattern = (
        r"(?m)^\s*("
        + str(chapter)
        + r"\.([1-9]\d*)(?:\.\d+)*)(?=\s*(?:期末|报告期末|报告期内|指数投资|积极投资|累计|买入|本基金))"
    )
    matches = list(re.finditer(pattern, text))
    return matches[-1][1] if matches else previous


def column_bounds(table):
    return [
        min(
            (cell for row in table["cell_boxes"] if (cell := row[index]) is not None),
            key=lambda cell: cell[2] - cell[0],
            default=None,
        )
        for index in range(table["col_count"])
    ]


def same_columns(left, right):
    if len(left) != len(right) or any(x is None for x in [*left, *right]):
        return False
    if abs(left[0][0] - right[0][0]) > 1.0 or abs(left[-1][2] - right[-1][2]) > 1.0:
        return False
    return all(
        min(a[2], b[2]) - max(a[0], b[0]) >= 0.95 * max(a[2] - a[0], b[2] - b[0])
        for a, b in zip(left, right, strict=True)
    )


def repair_header(doc, page, table, pages):
    if (
        table["col_count"] != 5
        or table["row_count"] != 1
        or compact(table["cells"][0][0]) != "序号"
    ):
        return table, None
    following = next((p for p in pages if p["page"] == page["page"] + 1), None)
    if not following or not following.get("tables"):
        return table, None
    next_table = following["tables"][0]
    if next_table["col_count"] != 6:
        return table, None
    bounds = column_bounds(next_table)
    reviewed = [
        case
        for case in MANUAL["cases"]
        if case["source"] == doc["source"]
        and case["source_sha256"] == doc["source_sha256"]
        and case["left"]["page"] == page["page"]
        and case["left"]["cells"] == table["cells"][0]
        and case["right"]["cells"] == next_table["cells"][0]
        and case["id"] in MANUAL_REVIEW["reviewed_case_ids"]
    ]
    if not same_columns(column_bounds(table), bounds[:5]) and len(reviewed) != 1:
        return table, None
    box = [bounds[5][0], table["bbox"][1], bounds[5][2], table["bbox"][3]]
    with fitz.open(ROOT / doc["source"]) as pdf:
        text = pdf[page["page"] - 1].get_text(clip=fitz.Rect(box)).strip()
    if not text or compact(text) not in compact(page["pypdf"]):
        raise ValueError("Missing header cell is not independently readable")
    result = copy.deepcopy(table)
    result["col_count"] = 6
    result["cells"][0].append(text)
    result["cell_boxes"][0].append(box)
    result["bbox"][2] = box[2]
    result["row_boxes"][0][2] = box[2]
    return result, {
        "page": page["page"],
        "original": table,
        "additional_cell": text,
        "additional_box": box,
        "basis": "adjacent-page six-column frame; original text in both readers",
        "manual_boundary_id": reviewed[0]["id"] if reviewed else None,
    }


def may_continue(cells, kind):
    if compact(cells[0]):
        return False
    label = compact(cells[1])
    if kind == "sectors" and label == "合计":
        return False
    if kind == "assets" and (label.startswith("其中:") or label == "资产支持证券"):
        return False
    return True


def physical_groups(doc, pages):
    groups, errors, repairs = {}, [], []
    section = None
    for page in pages:
        if not doc["portfolio_first_page"] <= page["page"] <= doc["portfolio_last_page"]:
            continue
        for table_index, original in enumerate(page.get("tables", [])):
            section = section_at(original["preceding_text"], doc, section)
            minor = int(section.split(".")[1]) if section else None
            if minor not in (1, 2, 3):
                continue
            kind = {1: "assets", 2: "sectors", 3: "stocks"}[minor]
            table, repair = (
                repair_header(doc, page, original, pages) if kind == "stocks" else (original, None)
            )
            if repair:
                repairs.append(repair)
            expected = 6 if kind == "stocks" else 4
            if table["col_count"] != expected:
                errors.append(
                    {
                        "page": page["page"],
                        "table": table_index,
                        "error": "Unexpected grid column count",
                    }
                )
                continue
            group = groups.setdefault(
                section, {"section": section, "kind": kind, "logical_rows": []}
            )
            for index, raw in enumerate(table["cells"]):
                cells = [compact(value) for value in raw]
                if not any(cells):
                    continue
                fragment = {
                    "page": page["page"],
                    "table": table_index,
                    "row": index,
                    "last_table_row": index == table["row_count"] - 1,
                    "cells": [value or "" for value in raw],
                    "bbox": table["row_boxes"][index],
                    "columns": column_bounds(table),
                }
                previous = group["logical_rows"][-1] if group["logical_rows"] else None
                if previous and index == 0 and may_continue(cells, kind):
                    last = previous["fragments"][-1]
                    manual = manual_boundary(doc, last, fragment)
                    if (
                        last["page"] + 1 == page["page"]
                        and last["last_table_row"]
                        and (same_columns(last["columns"], fragment["columns"]) or manual)
                    ):
                        previous["cells"] = [
                            a + b for a, b in zip(previous["cells"], cells, strict=True)
                        ]
                        previous["fragments"].append(fragment)
                        if manual:
                            previous["manual_boundary_id"] = manual
                        continue
                group["logical_rows"].append({"cells": cells, "fragments": [fragment]})
        section = section_at(page["fitz"], doc, section)
    return groups, errors, repairs


def fragment_proofs(row, pages, header=False):
    results = []
    for fragment in row["fragments"]:
        page = pages[fragment["page"]]
        proof = {"page": fragment["page"], "table": fragment["table"], "row": fragment["row"]}
        for reader in ("fitz", "pypdf"):
            if header:
                positions = [
                    compact(page[reader]).find(compact(cell))
                    for cell in fragment["cells"]
                    if compact(cell)
                ]
                if any(position < 0 for position in positions):
                    raise ValueError("Header cell absent from independent source text")
                proof[reader] = positions
            else:
                proof[reader] = prior.proof(fragment["cells"], page[reader])
        results.append(proof)
    return results


def split_nav(doc, pages):
    fragments = []
    with fitz.open(ROOT / doc["source"]) as pdf:
        for index in range(min(10, len(pdf))):
            for table_index, table in enumerate(
                pdf[index].find_tables(strategy="lines_strict").tables
            ):
                for row_index, cells in enumerate(table.extract()):
                    if len(cells) == 4 and compact(cells[0]) in ("期末基金", "资产净值"):
                        fragments.append(
                            {
                                "page": index + 1,
                                "table": table_index,
                                "row": row_index,
                                "cells": [cell or "" for cell in cells],
                                "bbox": list(table.rows[row_index].bbox),
                                "columns": column_bounds(
                                    {
                                        "col_count": table.col_count,
                                        "cell_boxes": [
                                            [list(cell) if cell else None for cell in row.cells]
                                            for row in table.rows
                                        ],
                                    }
                                ),
                            }
                        )
    if (
        len(fragments) != 2
        or fragments[0]["page"] + 1 != fragments[1]["page"]
        or not same_columns(fragments[0]["columns"], fragments[1]["columns"])
    ):
        raise ValueError("Missing paired NAV label fragments")
    cells = [
        compact(a) + compact(b)
        for a, b in zip(fragments[0]["cells"], fragments[1]["cells"], strict=True)
    ]
    if cells[0] != "期末基金资产净值" or any(fragments[1]["cells"][1:]):
        raise ValueError("NAV continuation contains additional amounts")
    value = number(cells[1])
    proofs = fragment_proofs({"fragments": fragments}, {p["page"]: p for p in pages})
    contexts = {}
    for reader in ("fitz", "pypdf"):
        text = "".join(compact(p[reader]) for p in pages if p["page"] <= fragments[0]["page"])
        context = text[text.rfind("3.1.2") :]
        dates = re.findall(r"(20\d{2})年+末", context)
        if not dates or dates[0] != doc["title_period_end"][:4]:
            raise ValueError("Split NAV current-year column is not identified")
        contexts[reader] = context
    return {
        "amount_cny": value,
        "fragments": fragments,
        "proofs": proofs,
        "period_context": contexts,
        "method": "adjacent-page label continuation",
    }


def typed_groups(groups, pages):
    errors = []
    for group in groups.values():
        rows, headers = [], []
        for row in group.pop("logical_rows"):
            cells = row["cells"]
            header = cells[0] in ("序号", "代码") or cells[1] in ("股票代码", "行业类别", "项目")
            try:
                proofs = fragment_proofs(row, pages, header)
                if header:
                    headers.append({**row, "proofs": proofs})
                    continue
                kind = group["kind"]
                if kind == "stocks":
                    if (
                        not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)", cells[0])
                        or not re.fullmatch(r"\d{6}", cells[1])
                        or not cells[2]
                    ):
                        raise ValueError("Invalid complete stock row")
                    item = {
                        "ordinal": int(cells[0].replace(",", "")),
                        "code": cells[1],
                        "name": cells[2],
                        "quantity_shares": number(cells[3]),
                        "market_value_cny": number(cells[4]),
                        "percent_nav": number(cells[5]),
                    }
                    if any(
                        item[key] is None
                        for key in ("quantity_shares", "market_value_cny", "percent_nav")
                    ):
                        raise ValueError("Stock value is absent")
                elif kind == "sectors":
                    total = cells[0] == "合计" or cells[1] == "合计"
                    if not total and not re.fullmatch("[A-S]", cells[0]):
                        raise ValueError("Invalid complete sector row")
                    item = {
                        "code": "TOTAL" if total else cells[0],
                        "name": cells[1],
                        "market_value_cny": number(cells[2]),
                        "percent_nav": number(cells[3]),
                    }
                else:
                    if cells[0] and not cells[0].isdigit():
                        raise ValueError("Invalid asset ordinal")
                    item = {
                        "ordinal": int(cells[0]) if cells[0] else None,
                        "name": cells[1],
                        "amount_cny": number(cells[2]),
                        "percent_total_assets": number(cells[3]),
                    }
                rows.append(
                    {
                        **item,
                        "source_cells": cells,
                        "fragments": row["fragments"],
                        "proofs": proofs,
                        "manual_boundary_id": row.get("manual_boundary_id"),
                    }
                )
            except Exception as error:
                errors.append({"section": group["section"], "row": row, "error": str(error)})
        group.update(rows=rows, headers=headers)
    return errors


def validate(groups, nav):
    checks, errors = [], []
    for group in groups.values():
        rows, kind = group["rows"], group["kind"]
        header = "".join(cell for row in group["headers"] for cell in row["cells"])
        expected = "基金总资产" if kind == "assets" else "基金资产净值"
        if expected not in header:
            errors.append({"section": group["section"], "error": "Percentage denominator missing"})
        if kind == "stocks":
            if "数量(股)" not in header:
                errors.append({"section": group["section"], "error": "Share unit missing"})
            for row in rows:
                quantity = Decimal(row["quantity_shares"])
                if quantity < 0 or quantity != quantity.to_integral_value():
                    errors.append({"section": group["section"], "error": "Invalid share quantity"})
            if [r["ordinal"] for r in rows] != list(range(1, len(rows) + 1)) or len(
                {r["code"] for r in rows}
            ) != len(rows):
                errors.append(
                    {
                        "section": group["section"],
                        "error": "Stock inventory ordinals or codes differ",
                    }
                )
        elif kind == "sectors":
            if [r["code"] for r in rows] != list("ABCDEFGHIJKLMNOPQRS") + ["TOTAL"]:
                errors.append({"section": group["section"], "error": "Sector inventory differs"})
            else:
                difference = sum(
                    (Decimal(r["market_value_cny"] or "0") for r in rows[:-1]), Decimal(0)
                ) - Decimal(rows[-1]["market_value_cny"] or "0")
                checks.append(
                    {
                        "section": group["section"],
                        "check": "sector_sum",
                        "difference_cny": str(difference),
                    }
                )
                if difference:
                    errors.append(
                        {"section": group["section"], "error": "Sector values do not sum"}
                    )
        else:
            ordinals = [r["ordinal"] for r in rows if r["ordinal"] is not None]
            if ordinals != list(range(1, len(ordinals) + 1)):
                errors.append({"section": group["section"], "error": "Asset inventory differs"})
            totals = [r for r in rows if r["name"] == "合计"]
            if len(totals) != 1:
                errors.append({"section": group["section"], "error": "Asset total missing"})
            else:
                denominator = totals[0]["amount_cny"]
                difference = sum(
                    (
                        Decimal(r["amount_cny"] or "0")
                        for r in rows
                        if r["ordinal"] is not None and r["name"] != "合计"
                    ),
                    Decimal(0),
                ) - Decimal(denominator)
                checks.append(
                    {
                        "section": group["section"],
                        "check": "asset_sum",
                        "difference_cny": str(difference),
                    }
                )
                if difference:
                    errors.append({"section": group["section"], "error": "Asset values do not sum"})
                for row in rows:
                    if (
                        row["amount_cny"] is not None
                        and row["percent_total_assets"] is not None
                        and not prior.rounding_matches(
                            row["amount_cny"], row["percent_total_assets"], denominator
                        )
                    ):
                        errors.append(
                            {
                                "section": group["section"],
                                "error": "Asset percentage rounding differs",
                                "name": row["name"],
                            }
                        )
        if kind != "assets" and nav is not None:
            for row in rows:
                if (
                    row["market_value_cny"] is not None
                    and row["percent_nav"] is not None
                    and not prior.rounding_matches(
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
    return checks, errors


def stock_inventory(doc, pages, stocks):
    expected = [(row["ordinal"], row["code"]) for row in stocks]
    records = {}

    def phrase(value):
        return r"\s*".join(map(re.escape, value))

    for reader in ("fitz", "pypdf"):
        text = "\n".join(
            page[reader] for page in pages if page["page"] >= doc["portfolio_first_page"]
        )
        start = re.search(phrase("股票投资明细"), text)
        ending = (
            "按债券品种分类的债券投资组合"
            if doc["report_type"] == "quarterly"
            else "报告期内股票投资组合的重大变动"
        )
        end = re.search(phrase(ending), text[start.end() :] if start else "")
        if start is None or end is None:
            raise ValueError("Independent stock-inventory section boundary missing")
        region = text[start.end() : start.end() + end.start()]
        pattern = r"(?<![\d.,])((?:\d{1,3}(?:,\s*\d(?:\s*\d){2})+|\d{1,4}))\s+(\d{6})\s+(?=[\u4e00-\u9fffA-Za-z*])"
        actual = [
            (int(re.sub(r"[,\s]", "", ordinal)), code)
            for ordinal, code in re.findall(pattern, region)
        ]
        if actual != expected:
            raise ValueError(
                f"{reader} ordered stock inventory differs: {len(actual)} versus {len(expected)}"
            )
        records[reader] = {
            "rows": len(actual),
            "ordered_inventory_sha256": digest(actual),
            "source_region_sha256": digest(region),
        }
    return records


def review(doc):
    if sha(ROOT / doc["extracted_pages"]) != doc["extraction_sha256"]:
        raise ValueError("Source extraction changed")
    pages = load_gzip(ROOT / doc["extracted_pages"])["pages"]
    groups, errors, repairs = physical_groups(doc, pages)
    errors.extend(typed_groups(groups, {p["page"]: p for p in pages}))
    for reader in ("fitz", "pypdf"):
        if not any("人民币元" in compact(page[reader]) for page in pages):
            errors.append({"reader": reader, "error": "CNY unit missing"})
    try:
        try:
            nav = prior.reported_nav(pages, doc)
        except ValueError:
            nav = split_nav(doc, pages)
    except Exception as error:
        nav = None
        errors.append({"error": str(error), "field": "reported_nav"})
    checks, failures = validate(groups, nav)
    errors.extend(failures)
    stocks = [r for group in groups.values() if group["kind"] == "stocks" for r in group["rows"]]
    sectors = [
        r
        for group in groups.values()
        if group["kind"] == "sectors"
        for r in group["rows"]
        if r["code"] == "TOTAL"
    ]
    stock_total = sum((Decimal(r["market_value_cny"]) for r in stocks), Decimal(0))
    sector_total = sum((Decimal(r["market_value_cny"] or "0") for r in sectors), Decimal(0))
    if doc["report_type"] != "quarterly" and stocks:
        checks.append(
            {"check": "all_stocks_vs_sectors", "difference_cny": str(stock_total - sector_total)}
        )
        if stock_total != sector_total:
            errors.append({"error": "Full stock holdings do not equal sector totals"})
    try:
        inventory = stock_inventory(doc, pages, stocks)
    except Exception as error:
        inventory = None
        errors.append({"error": str(error), "field": "ordered_stock_inventory"})
    texts = {
        reader: "".join(
            compact(p[reader]) for p in pages if p["page"] >= doc["portfolio_first_page"]
        )
        for reader in ("fitz", "pypdf")
    }
    absence = {
        reader: re.findall(r"本基金(?:本)?报告期末(?:未持有|未投资)股票", text)
        for reader, text in texts.items()
    }
    if not stocks and any(not clauses for clauses in absence.values()):
        errors.append({"error": "No stocks and no explicit source absence statement"})
    assets = [r for group in groups.values() if group["kind"] == "assets" for r in group["rows"]]
    equity = [r for r in assets if r["name"] == "其中:股票"]
    residual = None
    if len(equity) != 1:
        errors.append({"error": "Asset composition equity is missing or ambiguous"})
    elif sectors:
        difference = Decimal(equity[0]["amount_cny"] or "0") - sector_total
        notes = {
            reader: [
                sentence
                for sentence in re.split(r"[。；]", text)
                if "可退替代款" in sentence and "估值增值" in sentence
            ]
            for reader, text in texts.items()
        }
        residual = {
            "amount_cny": str(difference),
            "basis": "asset-composition stocks minus sector-stock total",
            "cash_substitution_note_evidence": notes,
            "reported_independent_cash_amount": False,
            "sector_allocated": False,
            "economic_exposure_certified": False,
        }
        if difference and any(not rows for rows in notes.values()):
            errors.append(
                {
                    "error": "Equity/sector residual lacks an explicit valuation note",
                    "difference_cny": str(difference),
                }
            )
    return {
        "symbol": doc["symbol"],
        "title": doc["title"],
        "source": doc["source"],
        "source_sha256": doc["source_sha256"],
        "catalogue_date": doc["catalogue_date"],
        "identity": doc["identity"],
        "report_type": doc["report_type"],
        "groups": list(groups.values()),
        "reported_nav": nav,
        "independent_stock_inventory": inventory,
        "checks": checks,
        "errors": errors,
        "grid_repairs": repairs,
        "stock_absence": absence,
        "equity_sector_residual": residual,
        "schedule_coverage": "TOP_HOLDINGS_ONLY"
        if doc["report_type"] == "quarterly"
        else "ALL_REPORTED_STOCKS",
        "status": "SOURCE_CELLS_REVIEWED_EXPOSURE_NOT_ACTIVATED"
        if not errors
        else "REVIEW_REQUIRED",
    }


def main():
    if (CHECKS / "cell_review.json").exists():
        raise FileExistsError("Preserve the earlier review")
    save(
        CHECKS / "cell_source_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "prior_source_sha256": sha(CHECKS / "review_source.py"),
            "protocol_sha256": sha(CHECKS / "cell_review_protocol.json"),
            "geometry_protocol_sha256": sha(CHECKS / "geometry_review_protocol.json"),
            "manual_review_sha256": sha(CHECKS / "manual_boundaries_reviewed.json"),
            "stock_inventory_check": "Both text readers must match every ordered ordinal/code pair",
        },
        immutable=True,
    )
    results = []
    for doc in read(CHECKS / "extraction_reviewed.json")["documents"]:
        if doc["publication_notice"]:
            continue
        results.append(review(doc))
        if len(results) % 25 == 0:
            print(len(results), "/528", dict(Counter(r["status"] for r in results)), flush=True)
    dump_gzip(OUT / "observations.json.gz", results)
    save(
        CHECKS / "cell_review.json",
        {
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "observations_sha256": sha(OUT / "observations.json.gz"),
            "counts": dict(Counter(r["status"] for r in results)),
            "reports": len(results),
            "activation": "NOT_ACTIVATED",
            "model_inputs_changed": False,
        },
        immutable=True,
    )
    print("FINISHED", dict(Counter(r["status"] for r in results)), flush=True)


if __name__ == "__main__":
    main()
