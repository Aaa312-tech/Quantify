"""Independently read the four diagnostic schemes and check stated arithmetic."""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
import re
import fitz
import unicodedata

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
initial_file = base / "initial_inventory.json"
visual_file = base / "preliminary_visual_observations.json"
initial, visual = read(initial_file), read(visual_file)
assert visual["pages"] == 14 and visual["facts_accepted"] == 0
reader = Path(fitz.__file__)
cases = {
    "sz300750/2024-04-30": {
        "gross": "5.028", "gross_page": 2,
        "gross_pattern": r"每10股派发现金分红人民币([\d.]+)元\(含税", "unit": "10",
        "total_shares": "4399041236", "repurchased": "11609630", "participating": "4387431606",
        "final_total": "22060006114.97", "ex_reference": "5.0147304", "reference_digits": 7,
        "reference_rounding": "EXPLICIT_TRUNCATION", "record_date": "2024-04-29", "payment_date": "2024-04-30",
        "reference_page": 3, "reference_fragment": "=5.0147304元/股(保留七位小数,最后一位直接截取,不四舍五入)",
    },
    "sh688025/2024-06-13": {
        "gross": "0.35057", "gross_page": 3,
        "gross_pattern": r"确定每股派发现金红利为([\d.]+)元\(含税\)", "unit": "1",
        "total_shares": "95049423", "repurchased": "437970", "participating": "94611453",
        "proposal_total": "33168128.35", "final_total": "33167937.08", "ex_reference": "0.34895",
        "reference_digits": 5, "reference_rounding": "PRINTED_PRECISION_CONSISTENCY",
        "record_date": "2024-06-12", "payment_date": "2024-06-13", "reference_page": 3,
        "reference_fragment": "前收盘价格-0.34895元/股",
    },
    "sh688627/2024-07-01": {
        "gross": "0.37345", "gross_page": 2,
        "gross_pattern": r"每股派发现金红利由0\.372元\(含税\)调整为([\d.]+)元\(含税\)", "unit": "1",
        "total_shares": "94011754", "repurchased": "364908", "participating": "93646846",
        "proposal_total": "34972372.49", "final_total": "34972414.64", "ex_reference": "0.37200",
        "reference_digits": 5, "reference_rounding": "PRINTED_PRECISION_CONSISTENCY",
        "record_date": "2024-06-28", "payment_date": "2024-07-01", "reference_page": 2,
        "reference_fragment": "前收盘价格-0.37200元/股",
    },
    "sh688627/2025-07-10": {
        "gross": "0.31910", "gross_page": 2,
        "gross_pattern": r"每股派发现金红利调整为([\d.]+)元\(含税\)", "unit": "1",
        "total_shares": "94011754", "repurchased": "358477", "participating": "93653277",
        "proposal_total": "29884722.28", "final_total": "29884760.69", "ex_reference": "0.31788",
        "reference_digits": 5, "reference_rounding": "PRINTED_PRECISION_CONSISTENCY",
        "record_date": "2025-07-09", "payment_date": "2025-07-10", "reference_page": 2,
        "reference_fragment": "前收盘价格-0.31788元/股",
    },
}
records = []
failures = []
for row in initial["records"]:
    key = row["symbol"] + "/" + row["ex_date"]
    if key not in cases:
        continue
    case = cases[key]
    source = row["source_review_record"]["source"]
    pdf = root / source["path"]
    assert sha(pdf) == source["sha256"]
    with fitz.open(pdf) as document:
        raw_pages = [page.get_text(sort=True) for page in document]
    pages = [re.sub(r"\s+", "", unicodedata.normalize("NFKC", page)) for page in raw_pages]
    numbers = re.findall(case["gross_pattern"], pages[case["gross_page"]-1])
    observed = [Decimal(number) / Decimal(case["unit"]) for number in numbers]
    gross, capital, participating = [Decimal(case[name]) for name in ("gross", "total_shares", "participating")]
    reference_raw = Decimal(case["final_total"]) / capital if case["reference_rounding"] == "EXPLICIT_TRUNCATION" else participating * gross / capital
    reference = reference_raw.quantize(Decimal(1).scaleb(-case["reference_digits"]), rounding=ROUND_DOWN if case["reference_rounding"] == "EXPLICIT_TRUNCATION" else ROUND_HALF_UP)
    checks = {
        "one_independent_gross_statement": len(observed) == 1,
        "gross_statement_matches_visual_observation": observed == [gross],
        "participating_share_subtraction": capital - Decimal(case["repurchased"]) == participating,
        "stated_final_total_at_cent_precision": (participating * gross).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(case["final_total"]),
        "ex_reference_arithmetic": reference == Decimal(case["ex_reference"]),
        "reference_value_in_financial_context": case["reference_fragment"] in pages[case["reference_page"]-1],
    }
    if "proposal_total" in case:
        checks["adjusted_cash_from_fixed_proposal_total"] = (Decimal(case["proposal_total"]) / participating).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP) == gross
        year, month, day = (int(part) for part in case["record_date"].split("-"))
        date1 = f"{year}/{month}/{day}"
        year, month, day = (int(part) for part in case["payment_date"].split("-"))
        date2 = f"{year}/{month}/{day}"
        checks["dated_table_header_and_three_values"] = any("股权登记日除权(息)日现金红利发放日" + date1 + date2 + date2 in page for page in pages)
    else:
        checks["registration_and_ex_date_sentence"] = "股权登记日为:2024年4月29日,除权除息日为:2024年4月30日" in pages[1]
        checks["CSDC_payment_sentence"] = "代派的A股股东现金分红将于2024年4月30日通过股东托管证券公司" in pages[2]
    failed = [name for name, passed in checks.items() if not passed]
    failures.extend({"event": key, "check": name} for name in failed)
    records.append({"event": key, "source": source, "case": case, "checks": checks,
                    "observed_gross": [str(number) for number in observed], "reference_unrounded": str(reference_raw),
                    "independent_pages": raw_pages, "reader_version": fitz.VersionBind})
result = {
    "status": "DIAGNOSTIC_CHECKS_PASSED" if not failures else "DIAGNOSTIC_CHECKS_FAILED",
    "checked_at": now(), "script_sha256": sha(Path(__file__)), "reader": str(reader), "reader_sha256": sha(reader),
    "initial_inventory_sha256": sha(initial_file), "visual_observations_sha256": sha(visual_file),
    "initial_reader_failure_sha256": sha(base / "initial_reader_failure.json"), "reader_version": fitz.VersionBind,
    "records": records, "failures": failures, "facts_accepted": 0,
    "scope": "Independent MuPDF reading and exact decimal checks of four preliminary whole-page observations. Printed-precision consistency does not establish a universal rounding rule. Source registration and revision closure remain required; current inputs and runtime are unchanged.",
}
save(base / "independent_arithmetic.json", result, immutable=True)
print(result["status"], "events", len(records), "checks", sum(len(row["checks"]) for row in records), "failures", failures, flush=True)
assert not failures
