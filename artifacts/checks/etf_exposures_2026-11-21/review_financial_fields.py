"""Review disclosed contract cells and preserve the scope of absence statements."""

import re
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
OUTPUT = ROOT / "data/processed/etf_exposures_2026-11-21"
CODE = re.compile(r"(?:(?:IF|IC|IH|IM|TF|TS|TL|T)\d{4}|Au\(T\+D\))")
NUMBER = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
TERMS = {
    "stock_index_futures": "股指期货",
    "treasury_futures": "国债期货",
    "warrants": "权证",
    "stock_options": "(?:股票|个股)期权",
    "gold_derivatives": "黄金衍生品",
    "derivative_balance": "衍生金融(?:资产/负债|工具)",
    "security_lending": "(?:转融通证券出借业务|转融通出借证券)",
}


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def decimal(value):
    value = compact(value)
    if value in ("-", "", "—"):
        return None
    if NUMBER.fullmatch(value) is None:
        raise ValueError("Unrecognized decimal cell: " + value)
    return str(Decimal(value.replace(",", "")))


def proof(page, value):
    value = compact(value)
    result = {}
    for reader in ("fitz", "pypdf"):
        offset = compact(page[reader]).find(value)
        if not value or offset < 0:
            raise ValueError(f"Page {page['page']}: {reader} ordered cells missing: {value[:90]}")
        result[reader] = {"compact_offset": offset, "characters": len(value)}
    return result


def table_rows(page, table, table_index, first_row):
    result = []
    for row_index, cells in enumerate(table["cells"][first_row + 1 :], first_row + 1):
        row = [compact(x) for x in cells]
        if not row or not CODE.fullmatch(row[0]):
            continue
        if len(row) not in (5, 6):
            raise ValueError("Unexpected contract column count")
        fields = [decimal(x) for x in row[2:5]]
        if any(x is None for x in fields):
            raise ValueError("Contract quantity, market value or P&L is not numeric")
        if row[0].startswith(("IF", "IC", "IH", "IM")):
            kind = "stock_index_futures"
        elif row[0] == "Au(T+D)":
            kind = "gold_delayed_contract"
        else:
            kind = "treasury_futures"
        result.append(
            {
                "code": row[0],
                "name": row[1],
                "kind": kind,
                "reported_quantity": fields[0],
                "reported_market_value_cny": fields[1],
                "reported_fair_value_change_cny": fields[2],
                "quantity_unit": "not_explicit_in_column_header",
                "direction": "reported_signed_quantity_not_inferred_from_pnl",
                "page": page["page"],
                "table": table_index,
                "row": row_index,
                "source_cells": cells,
                "proof": proof(page, "".join(row)),
            }
        )
    if not result:
        raise ValueError("Contract header has no complete recognized contract rows")
    return result


def contracts(pages, note):
    tables, failures = [], []
    for page in pages:
        for index, table in enumerate(page.get("tables", [])):
            for ri, cells in enumerate(table["cells"]):
                row = [compact(x) for x in cells]
                if not (any("持仓量" in x for x in row) and any("市值" in x for x in row)):
                    continue
                item = {"page": page["page"], "table": index, "header_row": ri}
                try:
                    if row[:2] != ["代码", "名称"] or not row[4].startswith("公允价值变动"):
                        raise ValueError("Incomplete contract header")
                    header_proof = proof(page, "".join(row))
                    if "(元)" in row[3] and "(元)" in row[4]:
                        unit = {"source": "column_headers", "currency": "CNY"}
                    else:
                        context = compact(table.get("preceding_text", ""))
                        if "单位:人民币元" not in context:
                            # A previous-page section unit is retained separately for review.
                            raise ValueError("CNY unit requires a cross-page review")
                        unit = {"source": "preceding_page_text", "currency": "CNY"}
                        proof(page, "单位:人民币元")
                    rows = table_rows(page, table, index, ri)
                    item.update(
                        header=cells,
                        header_proof=header_proof,
                        unit=unit,
                        rows=rows,
                        market_value_sum_cny=str(
                            sum(Decimal(r["reported_market_value_cny"]) for r in rows)
                        ),
                        fair_value_change_sum_cny=str(
                            sum(Decimal(r["reported_fair_value_change_cny"]) for r in rows)
                        ),
                        source_table_cells=table["cells"],
                        section_scope="current_report_end_contract_schedule",
                    )
                    tables.append(item)
                except ValueError as error:
                    item.update(error=str(error), source_table_cells=table["cells"])
                    failures.append(item)
    # Do not turn a missing table or a zero carrying value into an empty economic position.
    return {"tables": tables, "failures": failures, "missing_table_means_zero": False}


def nominal(note):
    if note is None:
        return {
            "status": "NOT_DISCLOSED_IN_QUARTERLY_FINANCIAL_NOTES",
            "tables": [],
            "failures": [],
        }
    tables, failures = [], []
    for page in note["pages"]:
        for ti, table in enumerate(page.get("tables", [])):
            cells = table["cells"]
            current = next(
                (i for i, row in enumerate(cells) if any("本期末" in compact(x) for x in row)),
                None,
            )
            if current is None or not any(
                "合同/名义" in compact(x) for row in cells[current:] for x in row
            ):
                continue
            try:
                end = next(
                    (
                        i
                        for i, row in enumerate(cells[current + 1 :], current + 1)
                        if any("上年度末" in compact(x) for x in row)
                    ),
                    len(cells),
                )
                rows = []
                for ri in range(current, end):
                    row = [compact(x) for x in cells[ri]]
                    if (
                        row[0]
                        not in (
                            "利率衍生工具",
                            "货币衍生工具",
                            "权益衍生工具",
                            "其他衍生工具",
                            "合计",
                        )
                        and "股指期货" not in row[0]
                    ):
                        continue
                    if len(row) != 5:
                        raise ValueError("Unexpected nominal/asset/liability columns")
                    rows.append(
                        {
                            "name": row[0],
                            "contract_nominal_cny": decimal(row[1]),
                            "fair_value_asset_cny": decimal(row[2]),
                            "fair_value_liability_cny": decimal(row[3]),
                            "source_cells": cells[ri],
                            "row": ri,
                            "proof": proof(page, "".join(row)),
                        }
                    )
                if not rows or rows[-1]["name"] != "合计":
                    raise ValueError("Current-period nominal table is incomplete")
                context = compact(table.get("preceding_text", ""))
                if "单位:人民币元" not in context:
                    raise ValueError("Current-period nominal unit is unverified")
                tables.append(
                    {
                        "page": page["page"],
                        "table": ti,
                        "current_header": cells[current],
                        "header_proof": proof(page, "".join(compact(x) for x in cells[current])),
                        "rows": rows,
                        "dash_is_not_certified_zero": True,
                    }
                )
            except ValueError as error:
                failures.append({"page": page["page"], "table": ti, "error": str(error)})
    return {"status": "FIELD_SCOPED_REVIEW", "tables": tables, "failures": failures}


def clauses(pages):
    result = {kind: [] for kind in TERMS}
    for page in pages:
        text = compact(page["fitz"])
        for kind, term in TERMS.items():
            prefix = r"(?:本基金(?:本报告期(?:末|内)?(?:及上年度末)?|报告期(?:末|内)?)?|本报告期(?:末|内)?(?:及上年度末)?[,，]?(?:本基金)?)"
            pattern = (
                prefix
                + r"(?:均)?(?:未持有|未投资|未参与|无)(?:任何)?"
                + term
                + r"(?:合约|投资|余额)?"
            )
            for match in re.finditer(pattern, text):
                phrase = match[0]
                try:
                    evidence = proof(page, phrase)
                except ValueError:
                    continue
                scope = "unspecified"
                if "本报告期末" in phrase or "报告期末" in phrase:
                    scope = "report_end"
                elif "本报告期" in phrase or "报告期内" in phrase:
                    scope = "report_period"
                result[kind].append(
                    {
                        "page": page["page"],
                        "phrase": phrase,
                        "scope": scope,
                        "context": text[max(0, match.start() - 180) : match.end() + 100],
                        "proof": evidence,
                        "certifies_all_economic_derivatives": False,
                    }
                )
    return result


def lending(pages, nav):
    amounts, policies, failures = [], [], []
    seen = set()
    for page in pages:
        text = compact(page["fitz"])
        pattern = r"(?:参与|通过)转融通证券出借业务(?:借出股票|出借的证券|出借股票)的公允价值为([\d,]+\.\d{2})元[,，]占(?:本)?基金(?:期末)?资产净值的?(?:比例为?)?([\d.]+)%"
        for match in re.finditer(pattern, text):
            try:
                evidence = proof(page, match[0])
                amount, percent = Decimal(match[1].replace(",", "")), Decimal(match[2])
                # Published percentages are rounded to 0.01 percentage points.
                if abs(amount / nav * 100 - percent) > Decimal("0.005"):
                    raise ValueError("Lending percentage does not round from reported NAV")
                key = (str(amount), str(percent))
                if key in seen:
                    continue
                seen.add(key)
                amounts.append(
                    {
                        "page": page["page"],
                        "amount_cny": str(amount),
                        "percent_nav": str(percent),
                        "phrase": match[0],
                        "proof": evidence,
                    }
                )
            except ValueError as error:
                failures.append({"page": page["page"], "phrase": match[0], "error": str(error)})
        for match in re.finditer(
            r"不终止确认(?:该)?出借证券[,，]仍按原金融资产类别进行后续计量", text
        ):
            try:
                evidence = proof(page, match[0])
                policies.append({"page": page["page"], "phrase": match[0], "proof": evidence})
            except ValueError:
                continue
    return {
        "reported_amounts": amounts,
        "retained_recognition_clauses": policies,
        "schedule_inclusion_certified": False,
        "added_to_equity_schedule": False,
        "missing_amount_is_zero": False,
        "failures": failures,
    }


def review(doc, observation):
    old_file = ROOT / doc["extracted_pages"]
    if sha(old_file) != doc["extraction_sha256"]:
        raise ValueError("Portfolio source changed")
    old = load_gzip(old_file)
    pages = [p for p in old["pages"] if p["page"] in doc["portfolio_pages"]]
    note = None
    if doc["financial_notes"]:
        note_file = ROOT / doc["financial_notes"]
        if sha(note_file) != doc["financial_notes_sha256"]:
            raise ValueError("Financial-note source changed")
        note = load_gzip(note_file)
        pages += note["pages"]
    result = {
        key: doc[key]
        for key in (
            "symbol",
            "catalogue_date",
            "identity",
            "report_type",
            "source",
            "source_sha256",
        )
    }
    result.update(
        contracts=contracts(pages, note),
        nominal=nominal(note),
        explicit_absence_clauses=clauses(pages),
        security_lending=lending(pages, Decimal(observation["reported_nav"]["amount_cny"])),
        complete_economic_exposure_certified=False,
        unreviewed_other_assets_retained=True,
    )
    result["field_failures"] = sum(
        len(result[field]["failures"]) for field in ("contracts", "nominal", "security_lending")
    )
    return result


def main():
    save(
        CHECKS / "financial_fields_source_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(ROOT / "configs/protocols/etf_exposures.json"),
            "notes_reviewed_sha256": sha(CHECKS / "notes_reviewed.json"),
        },
        immutable=True,
    )
    docs = read(CHECKS / "notes_reviewed.json")["documents"]
    observations = {
        d["source"]: d
        for d in load_gzip(
            ROOT / "data/processed/etf_holdings_cells_2026-11-21/observations.json.gz"
        )
    }
    results = [review(doc, observations[doc["source"]]) for doc in docs]
    target = OUTPUT / "financial_fields_initial.json.gz"
    dump_gzip(target, results)
    summary = {
        "completed_at": now(),
        "source_sha256": sha(Path(__file__)),
        "output": target.relative_to(ROOT).as_posix(),
        "output_sha256": sha(target),
        "reports": len(results),
        "contract_tables": sum(len(r["contracts"]["tables"]) for r in results),
        "contract_rows": sum(len(t["rows"]) for r in results for t in r["contracts"]["tables"]),
        "nominal_tables": sum(len(r["nominal"]["tables"]) for r in results),
        "lending_amount_reports": sum(
            bool(r["security_lending"]["reported_amounts"]) for r in results
        ),
        "failure_categories": dict(
            Counter(
                f["error"]
                for r in results
                for k in ("contracts", "nominal", "security_lending")
                for f in r[k]["failures"]
            )
        ),
        "reports_with_failures": sum(bool(r["field_failures"]) for r in results),
        "complete_economic_exposure_certified": False,
        "model_inputs_changed": False,
    }
    save(CHECKS / "financial_fields_initial.json", summary, immutable=True)
    print(summary)


if __name__ == "__main__":
    main()
