"""Explain two date-token joins and an unsupported aggregate-rounding assumption."""

import re
import unicodedata
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd()
checks = Path(__file__).resolve().parent
initial_file = checks / "remaining_primary_checks.json"
initial = read(initial_file)
initial_sha = sha(initial_file)
expected_failures = {
    ("sh688627", "2024-07-01", "record_ex_payment_table_values"),
    ("sh688627", "2025-07-10", "record_ex_payment_table_values"),
    ("sz301313", "2024-05-31", "printed_aggregate_at_stated_precision"),
}
require(
    {(r["symbol"], r["ex_date"], r["check"]) for r in initial["failures"]} == expected_failures,
    "Initial failures differ from the three reviewed cases",
)
records = []
for symbol, ex_date in (("sh688627", "2024-07-01"), ("sh688627", "2025-07-10")):
    observation_path = checks / "observations" / f"{symbol}_{ex_date}.json"
    observation = read(observation_path)
    pdf = root / observation["source"]["path"]
    require(sha(pdf) == observation["source"]["sha256"], "Primary source changed")
    with fitz.open(pdf) as document:
        mupdf_pages = [page.get_text(sort=True) for page in document]
    pypdf_pages = [page.extract_text() or "" for page in PdfReader(pdf).pages]
    f = observation["observed_fields"]
    expected_dates = [f["record_date"], ex_date, f["cash_pay_date"]]
    reader_results = {}
    for reader, pages in (("mupdf", mupdf_pages), ("pypdf", pypdf_pages)):
        text = unicodedata.normalize("NFKC", "\n".join(pages))
        section = re.search(r"三、\s*相关日期(.*?)四、", text, re.S)
        require(section is not None, f"Missing role-labelled date section: {reader}")
        table = section.group(1)
        tokens = re.findall(r"(?<!\d)(\d{4})/(\d{1,2})/(\d{1,2})(?!\d)", table)
        dates = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in tokens]
        flags = {
            "record_ex_payment_values": dates == expected_dates,
            "three_preserved_date_tokens": len(tokens) == 3,
            "role_headers": all(
                heading in re.sub(r"\s+", "", table)
                for heading in ("股权登记日", "除权(息)日", "现金红利发放日")
            ),
        }
        require(all(flags.values()), f"Date closure failed for {ex_date}, {reader}")
        reader_results[reader] = {"table_text": table, "dates": dates, "checks": flags}
    records.append(
        {
            "symbol": symbol,
            "ex_date": ex_date,
            "initial_failed_check": "record_ex_payment_table_values",
            "classification": "INDEPENDENT_CHECKER_REMOVED_DATE_TOKEN_DELIMITERS",
            "explanation": "Removing all whitespace joined adjacent date cells. A one-digit day absorbed the next year's first digit under a greedy pattern. Both fresh readers recover the three labelled cells while preserving delimiters; source dates and acceptance rules do not change.",
            "observation_sha256": sha(observation_path),
            "readers": reader_results,
        }
    )

observation_path = checks / "observations/sz301313_2024-05-31.json"
observation = read(observation_path)
f = observation["observed_fields"]
product = Decimal(f["participating_shares"]) * Decimal(f["cash_per_ten_shares"]) / 10
fraction_product = Fraction(f["participating_shares"]) * Fraction(f["cash_per_ten_shares"]) / 10
reported = Decimal(f["printed_implementation_aggregate"])
floor_cents = Fraction(
    (fraction_product * 100).numerator // (fraction_product * 100).denominator, 100
)
flags = {
    "exact_decimal_fraction_product_agrees": Fraction(product) == fraction_product,
    "reported_value_equals_aggregate_cent_truncation": product.quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    == reported,
    "independent_rational_cent_truncation": floor_cents == Fraction(reported),
    "initial_round_half_up_expectation_does_not_match": product.quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    != reported,
}
require(all(flags.values()), "Aggregate-precision diagnostic differs")
records.append(
    {
        "symbol": "sz301313",
        "ex_date": "2024-05-31",
        "initial_failed_check": "printed_aggregate_at_stated_precision",
        "classification": "CHECKER_ASSUMED_UNDECLARED_HALF_UP_AGGREGATE_ROUNDING",
        "observation_sha256": sha(observation_path),
        "checks": flags,
        "exact_product": str(product),
        "reported_aggregate": str(reported),
        "reported_minus_exact_product": str(reported - product),
        "explanation": "The source's 15631435.79 equals cent truncation of 15631435.7978600. The initial checker incorrectly assumed half-up rounding for an unstated convention. This explains the check failure but does not certify issuer settlement mechanics or a per-account rounding rule. Preserve the printed total and the exact product separately.",
        "aggregate_rounding_rule_explicitly_certified": False,
        "account_rounding_rule_certified": False,
    }
)
require(sha(initial_file) == initial_sha, "Initial failure receipt changed")
save(
    checks / "remaining_primary_closure.json",
    {
        "status": "PRIMARY_CHECKS_CLOSED_WITH_AGGREGATE_ROUNDING_LIMITATION",
        "checked_at": now(),
        "script_sha256": sha(Path(__file__)),
        "initial_receipt": {
            "path": initial_file.relative_to(root).as_posix(),
            "sha256": initial_sha,
        },
        "initial_checks": sum(len(r["checks"]) for r in initial["records"]),
        "initial_passed": sum(sum(r["checks"].values()) for r in initial["records"]),
        "initial_failed": len(initial["failures"]),
        "explained_failures": len(records),
        "records": records,
        "facts_accepted": 0,
        "runtime_inputs_changed": False,
        "unresolved_other_source_aggregate": "Beijing Ingenic 2025-06-24 remains unresolved in adjusted_cash_arithmetic_review.json; this closure does not alter it.",
        "boundary": "Only the three recorded checker failures are explained. All source facts remain pending batch decisions and related-notice closure. No numerical threshold, provider value, source total or account-rounding convention has been changed.",
    },
    immutable=True,
)
print(
    "Closed two date-token joins and one undeclared-rounding assumption; source limitations retained"
)
