"""Recheck the complete cash inventory with explicit tax and heading boundaries."""

import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from read_primary_financials import CASH as INITIAL_CASH
from read_primary_financials import cash_scope, document_text, evidence, require

from quantfolio.storage import load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/residual_financial_scope.json")
CASH = re.compile(INITIAL_CASH.pattern.replace(r"\(含税\)", r"\(含税(?=[,，;；)])"))
CURRENT_HEADING = re.compile(
    r"[一二三四五六七八九十]{1,3}[、.]"
    r"(?:本次实施的)?(?:分配、转增股本|权益分派|利润分配及资本公积金转增股本|"
    r"利润分配|分配)方案(?:情况)?"
)


def inventory(pages):
    text, locations, sections, counters = document_text(pages)
    expanded = []
    for section in sections:
        heading = CURRENT_HEADING.match(text, section["start"])
        expanded.append({**section, "text": heading[0] if heading else section["text"]})
    statements = []
    for match in CASH.finditer(text):
        scope, section = cash_scope(text, locations, expanded, match)
        unit = 10 if match["ten"] else 1
        statements.append(
            {
                **evidence(text, locations, match.start(), match.end()),
                "amount": match["amount"],
                "per_shares": unit,
                "per_share": str(Decimal(match["amount"]) / unit),
                "currency": match["currency"],
                "gross_tax_qualification": "含税",
                "scope": scope,
                "section": section,
            }
        )
    return {"statements": statements, "sections": expanded, "removed_counters": counters}


def fixtures():
    cases = [
        ("二、权益分派方案\n每10股派2.5元人民币现金（含税；扣税后另计）", "0.25"),
        ("二、\n分配方案\n每股派发现金红利0.25元（含税）", "0.25"),
        ("二、分配方案\n每股派发现金红利0.\n25元（含税）", None),
        ("二、分配方案\n每股派发现金红利0.25元（含税率估算）", None),
        ("二、分配方案\n每股派发现金红利0.25元（不含税）", None),
    ]
    results = []
    for raw, expected in cases:
        found = inventory([raw])["statements"]
        value = found[0]["per_share"] if found else None
        require(value == expected, "Cash qualification fixture failed")
        if found:
            require(found[0]["scope"] == "CURRENT_IMPLEMENTATION_SECTION", "Heading fixture failed")
        results.append({"text": raw, "expected": expected, "actual": value})
    return results


def main():
    protocol = read(PROTOCOL)
    for key in ("parent_protocol", "initial_checker", "initial_review", "initial_text"):
        source = protocol[key]
        require(sha(source["path"]) == source["sha256"], "Frozen source changed: " + key)
    output = BASE / "financial_scope_review.json"
    require(not output.exists(), "Refusing to overwrite financial scope review")
    fixture_results = fixtures()
    sources = load_gzip(protocol["initial_text"]["path"])["documents"]
    field_protocol = read("configs/protocols/residual_field_review.json")
    initial = read(protocol["initial_review"]["path"])
    require(len(sources) == 136 and len(field_protocol["records"]) == 95, "Scope changed")
    documents = {}
    for path, source in sources.items():
        require(sha(path) == source["source"]["sha256"], "PDF changed")
        documents[path] = {reader: inventory(pages) for reader, pages in source["readers"].items()}
    records, totals, changed = [], Counter(), []
    original = {(r["symbol"], r["ex_date"]): r for r in initial["records"]}
    for event in field_protocol["records"]:
        key = event["symbol"], event["ex_date"]
        wanted = Decimal(event["source_identity_fields"]["cash_per_share"])
        provider = Decimal(event["provider"]["cash_per_share"])
        results = {}
        for reader, result in documents[event["source"]["path"]].items():
            current = [
                row
                for row in result["statements"]
                if row["scope"].startswith("CURRENT_")
                or row["scope"] == "EXPLICIT_CURRENT_DISTRIBUTION"
            ]
            conflicting_unscoped = [
                row
                for row in result["statements"]
                if row["scope"] == "SCOPE_REQUIRES_REVIEW"
                and abs(Decimal(row["per_share"]) - wanted) > Decimal("1e-12")
            ]
            agrees = (
                bool(current)
                and not conflicting_unscoped
                and all(
                    abs(Decimal(row["per_share"]) - wanted) <= Decimal("1e-12")
                    and abs(Decimal(row["per_share"]) - provider) <= Decimal("1e-12")
                    for row in current
                )
            )
            status = (
                "CURRENT_GROSS_CASH_AGREES" if agrees else "CASH_SCOPE_OR_VALUE_REQUIRES_REVIEW"
            )
            totals[status] += 1
            results[reader] = {
                "status": status,
                "current_statement_count": len(current),
                "all_statements": result["statements"],
                "conflicting_unscoped": conflicting_unscoped,
            }
            previous = original[key]["cash_checks"][reader]["status"]
            if previous != status:
                changed.append(
                    {
                        "symbol": key[0],
                        "ex_date": key[1],
                        "reader": reader,
                        "before": previous,
                        "after": status,
                    }
                )
        records.append(
            {
                "symbol": key[0],
                "ex_date": key[1],
                "source": event["source"],
                "stored_cash": str(wanted),
                "provider_cash": str(provider),
                "readers": results,
            }
        )
    save(
        output,
        {
            "status": "CASH_SCOPE_DIAGNOSTIC_COMPLETE_FULL_SOURCE_REVIEW_PENDING",
            "completed_at": now(),
            "checker_sha256": sha(__file__),
            "protocol_sha256": sha(PROTOCOL),
            "frozen_dependencies": {
                key: protocol[key] for key in ("initial_checker", "initial_review", "initial_text")
            },
            "fixture_checks": fixture_results,
            "documents": documents,
            "records": records,
            "check_counts": dict(totals),
            "changed_checks": changed,
            "facts_accepted": 0,
            "limitations": "Machine cash scope only. The original narrative-date exception, catalogue/role differences, actual page inspections and related-amendment review still require their own evidence. Original failed checks remain unchanged.",
        },
        immutable=True,
    )
    print("CASH_SCOPE_COUNTS", dict(totals), "changed", len(changed), flush=True)


if __name__ == "__main__":
    main()
