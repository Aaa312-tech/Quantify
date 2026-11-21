"""Explain one cent-precision check and retain one source aggregate discrepancy."""

import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import now, read, save, sha


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).replace(",", "")


root = Path.cwd()
checks = Path(__file__).resolve().parent
previous_path = checks / "adjusted_cash_primary_checks.json"
previous = read(previous_path)
expected = {
    ("sz300223", "2025-06-24", "printed_final_aggregate_at_cents"),
    ("sz300014", "2026-05-14", "original_proposal_arithmetic"),
}
assert {(r["symbol"], r["event_date"], r["check"]) for r in previous["failures"]} == expected
records = []
for symbol, event_date in (("sz300223", "2025-06-24"), ("sz300014", "2026-05-14")):
    observation_path = checks / "observations" / f"{symbol}_{event_date}.json"
    observation = read(observation_path)
    source = observation["source"]
    pdf = root / source["path"]
    assert sha(pdf) == source["sha256"]
    pypdf_pages = [page.extract_text() or "" for page in PdfReader(pdf).pages]
    with fitz.open(pdf) as document:
        independent_pages = [page.get_text(sort=True) for page in document]
    both_texts = [compact("".join(p)) for p in (pypdf_pages, independent_pages)]
    f = observation["observed_fields"]
    if symbol == "sz300014":
        product = Decimal(f["earlier_total_shares"]) * Decimal(f["proposal_cash_per_ten"]) / 10
        printed = Decimal(f["fixed_proposal_aggregate"])
        assert all("每10股派发现金红利2.45元(含税)" in t and "合计派发现金股利人民币508159998.05元(含税)" in t for t in both_texts)
        assert product == Decimal("508159998.045")
        assert product.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == printed
        decision = {
            "status": "SOURCE_CENT_PRECISION_EXPLAINS_CHECK_FAILURE",
            "failed_check": "original_proposal_arithmetic",
            "scope": "The first check compared an unrounded product with a printed two-decimal aggregate. Half-up cent representation gives exactly the printed amount; no tolerance is added and no source amount changes. This observation does not establish account-settlement rounding.",
            "raw_product": str(product), "printed_total": str(printed),
            "cent_representation": str(product.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "difference": str(printed - product),
        }
    else:
        product = Decimal(f["participating_shares"]) * Decimal(f["cash_per_ten_shares"]) / 10
        printed = Decimal(f["printed_implementation_aggregate"])
        assert all("每10股派发现金红利0.997988元(含税)" in t and "共分配现金股利48156985.09元(含税)" in t and "每10股派0.997988元人民币现金(含税" in t for t in both_texts)
        assert product == Decimal("48156985.1065324")
        assert printed - product == Decimal("-0.0165324")
        assert product.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal("48156985.11") != printed
        decision = {
            "status": "SOURCE_AGGREGATE_DISCREPANCY_UNRESOLVED",
            "failed_check": "printed_final_aggregate_at_cents",
            "scope": "Both readers and the previously viewed original page retain gross0.997988 per ten, base482540723 and aggregate48156985.09. The product rounds to48156985.11. The discrepancy is real in the source; no account rounding, transcription cause or revised entitlement is inferred. Gross cash is explicitly repeated, but its acceptance remains a separate batch field decision. Do not mark this aggregate check passed.",
            "raw_product": str(product), "printed_total": str(printed),
            "cent_representation": str(product.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "difference": str(printed - product),
            "source_anomaly_addendum_to": observation_path.relative_to(root).as_posix(),
        }
    records.append({
        "symbol": symbol, "event_date": event_date, "source": source,
        "observation_sha256": sha(observation_path), "decision": decision,
        "pypdf_pages": pypdf_pages, "independent_pages": independent_pages,
    })
save(checks / "adjusted_cash_arithmetic_review.json", {
    "status": "ONE_CHECK_EXPLAINED_ONE_SOURCE_AGGREGATE_UNRESOLVED",
    "reviewed_at": now(), "script_sha256": sha(Path(__file__)),
    "initial_check_sha256": sha(previous_path),
    "initial_script_sha256": sha(checks / "check_adjusted_cash_primary.py"),
    "records": records, "explained_check_failures": 1, "unresolved_source_aggregates": 1,
    "prior_failure_preserved": True, "facts_accepted": 0, "runtime_inputs_changed": False,
}, immutable=True)
print("EVE cent precision explained; Ingenic printed aggregate remains unresolved. No financial facts changed.")
