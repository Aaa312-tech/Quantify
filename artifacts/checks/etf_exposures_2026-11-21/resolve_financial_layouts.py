"""Resolve the visually reviewed table continuations without replacing prior evidence."""

import copy
import importlib.util
from collections import Counter
from decimal import Decimal
from pathlib import Path

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
OUTPUT = ROOT / "data/processed/etf_exposures_2026-11-21"
spec = importlib.util.spec_from_file_location(
    "financial_review", CHECKS / "review_financial_fields.py"
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def note_unit(pages, page, table, note):
    before = base.compact(table.get("preceding_text", ""))
    if "单位:人民币元" in before:
        return {
            "currency": "CNY",
            "scope": "preceding_page_text",
            "page": page["page"],
            "proof": base.proof(page, "单位:人民币元"),
        }
    previous = pages.get(page["page"] - 1)
    if previous is None or "单位:人民币元" not in base.compact(previous["fitz"]):
        raise ValueError("Registered preceding-page unit is absent")
    if note is None or page["page"] not in [p["page"] for p in note["pages"]]:
        raise ValueError("Inherited units must be in the same financial note")
    text = base.compact(previous["fitz"])
    # Distinguish a table heading carried to the next page from a sibling note's unit.
    last_unit = text.rfind("单位:人民币元")
    contract_heading = text.rfind("期末基金持有的期货合约情况")
    explicit = contract_heading >= 0 and last_unit > contract_heading
    return {
        "currency": "CNY",
        "page": previous["page"],
        "scope": "preceding_page_contract_heading" if explicit else "inherited_financial_note_unit",
        "explicit_contract_table_unit": explicit,
        "proof": base.proof(previous, "单位:人民币元"),
    }


def resolve_contract(doc, failure, pages, note):
    page = pages[failure["page"]]
    table = page["tables"][failure["table"]]
    ri = failure["header_row"]
    header = [base.compact(x) for x in table["cells"][ri]]
    fragments = [
        {"page": page["page"], "table": failure["table"], "row": ri, "cells": table["cells"][ri]}
    ]
    body_page, body_table, body_index, start = page, table, failure["table"], ri
    if len(table["cells"]) == 1:
        body_page = pages[page["page"] + 1]
        body_table, body_index, start = body_page["tables"][0], 0, -1
    if failure["error"] == "Incomplete contract header":
        body_page = pages[page["page"] + 1]
        body_table, body_index, start = body_page["tables"][0], 0, 0
        rest = [base.compact(x) for x in body_table["cells"][0]]
        if len(rest) != len(header):
            raise ValueError("Split header column counts differ")
        header = [left + right for left, right in zip(header, rest)]
        fragments.append(
            {"page": body_page["page"], "table": 0, "row": 0, "cells": body_table["cells"][0]}
        )
    if header[:2] != ["代码", "名称"] or not header[4].startswith("公允价值变动"):
        raise ValueError("Reconstructed contract header differs")
    for fragment in fragments:
        fragment["proof"] = base.proof(
            pages[fragment["page"]], "".join(base.compact(x) for x in fragment["cells"])
        )
    unit = {"currency": "CNY", "scope": "column_headers"}
    if not all("(元)" in header[i] for i in (3, 4)):
        unit = note_unit(pages, page, table, note)
    dash_row = [base.compact(x) for x in body_table["cells"][start + 1]]
    if dash_row and all(x == "-" for x in dash_row):
        rows = []
        status = "REPORTED_DASH_SCHEDULE_NOT_NUMERIC_ZERO"
        dash_proof = base.proof(body_page, "".join(dash_row))
    else:
        rows = base.table_rows(body_page, body_table, body_index, start)
        status, dash_proof = "REVIEWED_CONTRACT_ROWS", None
    return {
        "page": page["page"],
        "table": failure["table"],
        "header_row": ri,
        "header": header,
        "header_fragments": fragments,
        "unit": unit,
        "rows": rows,
        "market_value_sum_cny": str(sum(Decimal(r["reported_market_value_cny"]) for r in rows))
        if rows
        else None,
        "fair_value_change_sum_cny": str(
            sum(Decimal(r["reported_fair_value_change_cny"]) for r in rows)
        )
        if rows
        else None,
        "source_table_cells": body_table["cells"],
        "body_page": body_page["page"],
        "section_scope": "current_report_end_contract_schedule",
        "status": status,
        "dash_row_proof": dash_proof,
        "visual_id": doc["symbol"] + "_" + doc["catalogue_date"],
    }


def resolve_nominal(doc, failure, pages, note):
    page = pages[failure["page"]]
    table = page["tables"][failure["table"]]
    current = next(
        i for i, r in enumerate(table["cells"]) if any("本期末" in base.compact(x) for x in r)
    )
    header = table["cells"][current]
    current_date = doc["identity"]["period_end"]
    year, month, day = [int(x) for x in current_date.split("-")]
    date_label = f"{year}年{month}月{day}日"
    if date_label not in base.compact("".join(x or "" for x in header)):
        raise ValueError("Nominal current-period date differs")
    header_proofs = [base.proof(page, x) for x in header if base.compact(x)]
    fragments = [
        {"page": page["page"], "table": failure["table"], "row": i, "cells": r}
        for i, r in enumerate(table["cells"][current + 1 :], current + 1)
    ]
    before_previous = []
    for fragment in fragments:
        if any("上年度末" in base.compact(x) for x in fragment["cells"]):
            break
        before_previous.append(fragment)
    if not any(base.compact(f["cells"][0]) == "合计" for f in before_previous):
        following = pages[page["page"] + 1]
        for i, r in enumerate(following["tables"][0]["cells"]):
            if any("上年度末" in base.compact(x) for x in r):
                break
            before_previous.append({"page": following["page"], "table": 0, "row": i, "cells": r})
    rows = []
    for index, fragment in enumerate(before_previous):
        cells = [base.compact(x) for x in fragment["cells"]]
        name = cells[0]
        if name == "货币衍":
            rest = before_previous[index + 1]
            if rest["cells"] != ["生工具", "", "", "", ""]:
                raise ValueError("Registered split currency label differs")
            name += "生工具"
            pieces = [fragment, rest]
        else:
            pieces = [fragment]
        if (
            name not in ("利率衍生工具", "货币衍生工具", "权益衍生工具", "其他衍生工具", "合计")
            and "股指期货" not in name
        ):
            continue
        if len(cells) != 5:
            raise ValueError("Nominal row has incorrect financial columns")
        evidence = []
        for piece in pieces:
            evidence.append(
                {
                    **piece,
                    "proof": base.proof(
                        pages[piece["page"]], "".join(base.compact(x) for x in piece["cells"])
                    ),
                }
            )
        rows.append(
            {
                "name": name,
                "contract_nominal_cny": base.decimal(cells[1]),
                "fair_value_asset_cny": base.decimal(cells[2]),
                "fair_value_liability_cny": base.decimal(cells[3]),
                "fragments": evidence,
            }
        )
    if [r["name"] for r in rows if "股指期货" not in r["name"]] != [
        "利率衍生工具",
        "货币衍生工具",
        "权益衍生工具",
        "其他衍生工具",
        "合计",
    ]:
        raise ValueError("Current nominal inventory is incomplete")
    return {
        "page": page["page"],
        "table": failure["table"],
        "current_header": header,
        "header_cell_proofs": header_proofs,
        "rows": rows,
        "dash_is_not_certified_zero": True,
        "unit": note_unit(pages, page, table, note),
        "visual_id": doc["symbol"] + "_" + doc["catalogue_date"],
    }


def main():
    protocol = read(CHECKS / "financial_followup_protocol.json")
    visual = read(CHECKS / "financial_visual_review.json")
    if (
        protocol["initial_source_sha256"] != sha(CHECKS / "review_financial_fields.py")
        or visual["status"] != "VISUALLY_REVIEWED"
    ):
        raise ValueError("Registered source or visual review differs")
    for item in visual["records"]:
        if sha(ROOT / item["panel"]) != item["panel_sha256"]:
            raise ValueError("Reviewed image changed")
    save(
        CHECKS / "financial_followup_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(CHECKS / "financial_followup_protocol.json"),
            "visual_review_sha256": sha(CHECKS / "financial_visual_review.json"),
            "initial_source_sha256": sha(CHECKS / "review_financial_fields.py"),
        },
        immutable=True,
    )
    docs = {d["source"]: d for d in read(CHECKS / "notes_reviewed.json")["documents"]}
    results = load_gzip(OUTPUT / "financial_fields_initial.json.gz")
    scope = {d["source"] for d in protocol["scope"]}
    initial = copy.deepcopy(results)
    for result in results:
        if result["source"] not in scope:
            continue
        doc = docs[result["source"]]
        old = load_gzip(ROOT / doc["extracted_pages"])
        pages = {p["page"]: p for p in old["pages"]}
        note = load_gzip(ROOT / doc["financial_notes"]) if doc["financial_notes"] else None
        if note:
            pages.update({p["page"]: p for p in note["pages"]})
        result["resolved_layout_failures"] = result["field_failures"]
        for kind, resolver in (("contracts", resolve_contract), ("nominal", resolve_nominal)):
            result[kind]["tables"].extend(
                resolver(doc, f, pages, note) for f in result[kind]["failures"]
            )
            result[kind]["failures"] = []
        result["field_failures"] = 0
    reconciliations = []
    for result in results:
        contracts = [row for table in result["contracts"]["tables"] for row in table["rows"]]
        nominals = result["nominal"]["tables"]
        if contracts and nominals:
            if len(nominals) != 1:
                raise ValueError("Multiple current-period nominal schedules")
            nominal_value = nominals[0]["rows"][-1]["contract_nominal_cny"]
            market = sum(Decimal(row["reported_market_value_cny"]) for row in contracts)
            pnl = sum(Decimal(row["reported_fair_value_change_cny"]) for row in contracts)
            difference = market - pnl - Decimal(nominal_value)
            reconciliations.append(
                {
                    "source": result["source"],
                    "market_value_cny": str(market),
                    "fair_value_change_cny": str(pnl),
                    "contract_nominal_cny": nominal_value,
                    "difference_cny": str(difference),
                }
            )
    target = OUTPUT / "financial_fields_reviewed.json.gz"
    dump_gzip(target, results)
    summary = {
        "completed_at": now(),
        "source_sha256": sha(Path(__file__)),
        "output": target.relative_to(ROOT).as_posix(),
        "output_sha256": sha(target),
        "reports": len(results),
        "replaced_failure_reports": len(scope),
        "unchanged_other_reports": sum(a == b for a, b in zip(initial, results)),
        "contract_tables": sum(len(r["contracts"]["tables"]) for r in results),
        "contract_rows": sum(len(t["rows"]) for r in results for t in r["contracts"]["tables"]),
        "contract_kind_counts": dict(
            Counter(
                row["kind"] for r in results for t in r["contracts"]["tables"] for row in t["rows"]
            )
        ),
        "empty_dash_schedules": sum(
            not t["rows"] for r in results for t in r["contracts"]["tables"]
        ),
        "nominal_tables": sum(len(r["nominal"]["tables"]) for r in results),
        "nominal_market_reconciliations": reconciliations,
        "reconciliation_failures": sum(Decimal(r["difference_cny"]) != 0 for r in reconciliations),
        "status": "LAYOUTS_RESOLVED_INDEPENDENT_REVIEW_PENDING",
        "complete_economic_exposure_certified": False,
        "activated": False,
    }
    save(CHECKS / "financial_followup.json", summary, immutable=True)
    print({k: v for k, v in summary.items() if k != "nominal_market_reconciliations"})


if __name__ == "__main__":
    main()
