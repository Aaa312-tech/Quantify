"""Locate unresolved source text; these matches are navigation, not accepted facts."""

from collections import Counter
from pathlib import Path
import re

from pypdf import PdfReader

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
initial_file = base / "initial_inventory.json"
initial = read(initial_file)
records = []
patterns = {
    "per_share_cash": r"每股.{0,8}(?:现金|派发)",
    "per_ten_shares": r"每\s*10\s*股",
    "adjusted_amount": r"(?:调整为|调整后|调整.{0,12}每股|每股.{0,18}调整)",
    "virtual_reference": r"(?:虚拟分派|折算的每股|摊薄调整)",
    "payment_method": r"(?:现金红利|现金分红).{0,20}(?:将于|派发|发放)",
    "gross_writing": r"含税",
}
for row in initial["records"]:
    if row["status"] not in {
        "PRIMARY_GROSS_AMOUNT_UNSUPPORTED_OR_CONFLICTING",
        "PRIMARY_GROSS_AMOUNT_MISMATCH",
        "PRIMARY_DATE_UNSUPPORTED_OR_CONFLICTING",
        "PRIMARY_PAYMENT_UNSUPPORTED_OR_CONFLICTING",
    }:
        continue
    source = row["source_review_record"]["source"]
    pdf = root / source["path"]
    assert sha(pdf) == source["sha256"]
    pages = [page.extract_text() or "" for page in PdfReader(pdf).pages]
    matches = []
    for number, page in enumerate(pages, 1):
        text = re.sub(r"\s+", "", page)
        for category, expression in patterns.items():
            for hit in re.finditer(expression, text):
                matches.append({
                    "category": category, "page": number,
                    "text": text[max(0, hit.start() - 65):min(len(text), hit.end() + 100)],
                })
    records.append({
        "symbol": row["symbol"], "ex_date": row["ex_date"], "status": row["status"],
        "source": source, "pages": len(pages), "navigation_matches": matches,
        "parsed_cash": row["source_review_record"].get("evidence", {}).get("cash_per_share", []),
        "unscoped_cash": row["source_review_record"].get("evidence", {}).get("unscoped_cash_per_share", []),
    })
counts = Counter(category for row in records for category in {x["category"] for x in row["navigation_matches"]})
null_dates = {}
for row in initial["records"]:
    status = row["status"]
    counters = null_dates.setdefault(status, {"events": 0, "positive_cash_missing_payment": 0, "share_increase_missing_listing": 0})
    action = row["current_action"]
    counters["events"] += 1
    counters["positive_cash_missing_payment"] += bool(action["cash_per_share"] > 0 and not action.get("cash_pay_date"))
    counters["share_increase_missing_listing"] += bool(action["share_multiplier"] > 1 and not action.get("share_list_date"))
save(base / "layout_inventory.json", {
    "status": "DIAGNOSTIC_ONLY", "created_at": now(), "script_sha256": sha(Path(__file__)),
    "initial_inventory_sha256": sha(initial_file), "records": records,
    "events_by_navigation_match": dict(counts), "economically_required_date_counts": null_dates,
    "scope": "Navigation expressions can include proposals, tax examples and hypothetical ex-reference formulas. No match is an accepted financial fact. Initial missing_payment flags described null values, including cash-free actions; required-date counts here separately require positive cash or increased shares.",
    "facts_accepted": 0, "inputs_changed": False,
}, immutable=True)
print("LAYOUT INVENTORY", len(records), "events", dict(counts), flush=True)
