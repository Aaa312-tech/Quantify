"""Check recipient-specific cash and declared reference amounts with a second reader."""

import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
visual_file = base / "shareholder_visual_observations.json"
visual = read(visual_file)
assert visual["pages"] == 8 and visual["facts_accepted"] == 0
cases = {
    "1220664181": {
        "event": "sh600989/2024-07-24", "minority_gross": "0.3158",
        "controlling_gross": "0.2650", "minority_total": "686198642.10",
        "controlling_total": "1367524566.70", "aggregate": "2053723208.80",
        "reference": "0.28", "record": "2024/7/23", "payment": "2024/7/24",
        "board": "2024-011", "adjustment": "2024-038",
    },
    "1223476469": {
        "event": "sh600989/2025-05-13", "minority_gross": "0.4598",
        "controlling_gross": "0.3891", "minority_total": "996454986.05",
        "controlling_total": "2010172805.16", "aggregate": "3006627791.21",
        "reference": "0.41", "record": "2025/5/12", "payment": "2025/5/13",
        "board": "2025-002",
    },
}
records, failures = [], []
for identifier, case in cases.items():
    visual_rows = [row for row in visual["records"] if row["id"] == identifier]
    assert len(visual_rows) == 4
    source = visual_rows[0]["source"]
    file = root / source["path"]
    assert sha(file) == source["sha256"]
    with fitz.open(file) as document:
        raw_pages = [page.get_text(sort=True) for page in document]
    assert len(raw_pages) == 4
    pages = [re.sub(r"\s+", "", unicodedata.normalize("NFKC", page)) for page in raw_pages]
    text = pages[1]
    matches = re.findall(
        r"(中小股东|大股东)每股应派发现金红利人民币([\d.]+)元\(含税\),合计派发现金红利([\d.,]+)元",
        text,
    )
    observed = {role: [str(Decimal(value)), str(Decimal(total.replace(",", "")))]
                for role, value, total in matches}
    total = Decimal(case["aggregate"])
    unrounded_average = total / Decimal("7333360000")
    date_fragment = "A股" + case["record"] + "-" + case["payment"] + case["payment"]
    checks = {
        "two_recipient_scoped_gross_statements": len(matches) == len(observed) == 2,
        "minority_statement": observed.get("中小股东") == [case["minority_gross"], case["minority_total"]],
        "controlling_statement": observed.get("大股东") == [case["controlling_gross"], case["controlling_total"]],
        "aggregate_sum": Decimal(case["minority_total"]) + Decimal(case["controlling_total"]) == total,
        "aggregate_printed": format(total, ",.2f") + "元。平均每股分配金额为" + case["reference"] + "元" in text,
        "declared_reference_formula": "(7,333,360,000×" + case["reference"] + ")÷7,333,360,000=" + case["reference"] + "(元/股)" in text,
        "reference_precision_consistent": unrounded_average.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(case["reference"]),
        "reference_context": "除权(息)参考价格=前一交易日收盘价-" + case["reference"] + "元" in text,
        "two_role_labelled_date_tables": all("股权登记日最后交易日除权(息)日现金红利发放日" in page and date_fragment in page for page in pages[:2]),
        "institutional_gross_repeated": "每股实际派发现金红利人民币" + case["minority_gross"] + "元。(税前)" in pages[3],
        "no_share_count_change": "流通股份变动比例为零" in text,
        "minority_is_not_reference": Decimal(case["minority_gross"]) != Decimal(case["reference"]),
    }
    failures.extend({"event": case["event"], "check": key} for key, passed in checks.items() if not passed)
    records.append({"source": source, "case": case, "checks": checks,
                    "observed_recipient_statements": observed, "independent_pages": raw_pages,
                    "aggregate_divided_by_total_shares": str(unrounded_average),
                    "reference_minus_unrounded_average": str(Decimal(case["reference"]) - unrounded_average),
                    "reference_precision_note": "Rounded aggregate average agrees at the declared two-place precision. This does not infer account settlement rounding, minority share counts, or replace the explicitly declared reference with the unrounded average."})
result = {
    "status": "DIAGNOSTIC_CHECKS_FAILED" if failures else "DIAGNOSTIC_CHECKS_PASSED",
    "checked_at": now(), "script_sha256": sha(Path(__file__)),
    "reader_version": fitz.VersionBind, "reader_sha256": sha(Path(fitz.__file__)),
    "visual_observations_sha256": sha(visual_file), "records": records,
    "failures": failures, "facts_accepted": 0,
    "scope": "Independent reading and decimal arithmetic for two observed recipient-specific distributions. Related amendments and formal registered source acceptance remain open; runtime data and tax rules are unchanged.",
}
save(base / "shareholder_independent_arithmetic.json", result, immutable=True)
print(result["status"], "events", len(records), "checks", sum(len(row["checks"]) for row in records), "failures", failures, flush=True)
assert not failures
