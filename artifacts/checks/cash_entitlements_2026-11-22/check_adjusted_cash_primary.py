"""Check five observed cash schemes with independent text and decimal arithmetic."""

import re
import unicodedata
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).replace(",", "")


def iso_date(parts):
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


root = Path.cwd()
checks_dir = Path(__file__).resolve().parent
protocol_path = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_path)
scope_path = root / protocol["scope"]["path"]
assert sha(scope_path) == protocol["scope"]["sha256"]
scope = {(r["symbol"], r["ex_date"]): r for r in read(scope_path)["records"]}
inventory_path = checks_dir / "related_source_inventory.json"
inventory = read(inventory_path)
cases = {
    ("sz300418", "2024-06-28"): ("调整后本公司2023年年度权益分派方案为:", "二、股权登记日"),
    ("sz000988", "2025-06-20"): ("二、本次实施的利润分配方案", "三、分红派息日期"),
    ("sz300223", "2025-06-24"): ("二、本次实施的利润分配方案", "三、分红派息日期"),
    ("sz300014", "2026-05-14"): ("二、权益分派方案", "三、股权登记日"),
    ("sz300972", "2026-05-06"): ("二、本次实施的利润分配方案", "三、股权登记日"),
}
date_pattern = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
records, failures = [], []
for (symbol, event_date), (start, stop) in cases.items():
    observation_path = checks_dir / "observations" / f"{symbol}_{event_date}.json"
    observation = read(observation_path)
    f = observation["observed_fields"]
    registered = scope[(symbol, event_date)]
    row = next(r for r in inventory["records"] if r["id"] == observation["source_id"])
    pdf = root / row["source"]["path"]
    assert sha(pdf) == row["source"]["sha256"] == observation["source"]["sha256"]
    with fitz.open(pdf) as document:
        pages = [page.get_text(sort=True) for page in document]
    text = compact("".join(pages))
    scheme = text.split(start, 1)[1].split(stop, 1)[0]
    gross_values = re.findall(r"每10股派(?:送现金股利|发现金股利)?([\d.]+)元(?:人民币现金)?\(含税", scheme)
    records_dates = re.findall(r"股权登记日为:" + date_pattern, text)
    ex_dates = re.findall(r"除权除息日为:" + date_pattern, text)
    payments = re.findall(r"代派的A股股东现金红利将于" + date_pattern + r"通过股东托管证券公司", text)
    gross10 = Decimal(f["cash_per_ten_shares"])
    gross = gross10 / 10
    total, participants = (Decimal(f[k]) for k in ("total_shares", "participating_shares"))
    ratio = Fraction(f["fixed_proposal_aggregate"]) * 10 / Fraction(f["participating_shares"])
    displayed = Fraction(f["cash_per_ten_shares"])
    flags = {
        "independent_reader_pages_match": pages == row["independent_pages"],
        "one_implemented_gross_statement": gross_values == [f["cash_per_ten_shares"]],
        "explicit_record_date": len(records_dates) == 1 and iso_date(records_dates[0]) == f["record_date"],
        "explicit_ex_date": len(ex_dates) == 1 and iso_date(ex_dates[0]) == event_date,
        "explicit_public_payment_date": len(payments) == 1 and iso_date(payments[0]) == f["cash_pay_date"],
        "participants_in_implemented_scheme": f["participating_shares"] in scheme,
        "eligible_share_base": total - Decimal(f.get("repurchased_shares", "0")) == participants,
        "fixed_proposal_ratio_in_displayed_six_place_cell": displayed <= ratio < displayed + Fraction(1, 10**6),
        "current_cash_agrees_at_existing_threshold": abs(Decimal(str(registered["current_action"]["cash_per_share"])) - gross) <= Decimal("1e-12"),
        "current_share_multiplier_agrees": Decimal(str(registered["current_action"]["share_multiplier"])) == Decimal(f["share_multiplier"]),
    }
    if "earlier_total_shares" in f:
        flags["original_proposal_arithmetic"] = Decimal(f["earlier_total_shares"]) * Decimal(f["proposal_cash_per_ten"]) / 10 == Decimal(f["fixed_proposal_aggregate"])
    added = f.get("new_incentive_shares", f.get("new_conversion_shares"))
    if added is not None:
        flags["capital_increase"] = Decimal(f["earlier_total_shares"]) + Decimal(added) == total
    if "printed_implementation_aggregate" in f:
        flags["printed_final_aggregate_at_cents"] = (participants * gross).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(f["printed_implementation_aggregate"])
    if symbol == "sz300418":
        flags["explicit_per_ten_truncation"] = "每10股派发金额采用舍尾处理小数点后保留六位" in text
        flags["declared_reference_equals_gross"] = Decimal(f["ex_reference_cash_per_share"]) == gross
        flags["reference_in_equity_formula"] = "除权除息日前一交易日收盘价-0.0493305" in text
    if symbol == "sz000988":
        flags["declared_reference_in_equity_formula"] = "股权登记日收盘价-0.1999999元" in text
        flags["reference_consistent_with_truncated_product_ratio"] = (participants * gross / total).quantize(Decimal("0.0000001"), rounding=ROUND_DOWN) == Decimal(f["ex_reference_cash_per_share"])
    if symbol == "sz300014":
        flags["explicit_per_ten_truncation"] = "保留六位小数最后一位直接截取不四舍五入" in text
    images = {}
    for page in observation["page_observations"]:
        assert page["whole_page_visually_inspected"]
        assert sha(root / page["image"]) == page["image_sha256"]
        images[page["image"]] = page["image_sha256"]
    failures.extend({"symbol": symbol, "event_date": event_date, "check": k} for k, passed in flags.items() if not passed)
    issuer_rows = [r for r in inventory["records"] if symbol in r["symbols"]]
    if symbol == "sz300418":
        assert len(issuer_rows) == 1 and issuer_rows[0]["id"] == observation["source_id"]
    records.append({
        "symbol": symbol, "event_date": event_date, "source": row["source"],
        "observation_sha256": sha(observation_path), "page_images": images,
        "checks": flags, "independent_pages": pages, "gross_per_share": str(gross),
        "unrounded_per_ten_ratio_numerator": ratio.numerator,
        "unrounded_per_ten_ratio_denominator": ratio.denominator,
        "cash_product": str(participants * gross),
        "related_review": "Only this primary notice exists for the issuer in the bound archive; no other received amendment" if symbol == "sz300418" else "PENDING_CONTEXTUAL_REVIEW_OF_OTHER_ARCHIVED_NOTICES",
        "related_source_ids": [r["id"] for r in issuer_rows],
        "rounding_boundary": "Finite-precision arithmetic is corroboration, not a new global tolerance or account-settlement rule. Huagong's reference arithmetic is consistent with truncation, but the notice does not declare that convention. Wanchen's April23 incentive listing is not a distribution listing date.",
    })
save(checks_dir / "adjusted_cash_primary_checks.json", {
    "status": "INDEPENDENT_PRIMARY_CHECKS_PASS" if not failures else "INDEPENDENT_PRIMARY_CHECKS_FAILED",
    "checked_at": now(), "script_sha256": sha(Path(__file__)),
    "protocol_sha256": sha(protocol_path), "inventory_sha256": sha(inventory_path),
    "reader_version": fitz.VersionBind, "records": records, "failures": failures,
    "facts_accepted": 0, "runtime_inputs_changed": False,
    "boundary": "Primary facts checked independently for five events. Other archived notices remain pending for four issuers; archive completeness and exact publication time remain uncertified. Full batch acceptance and1839-event preservation have not occurred.",
}, immutable=True)
print("ADJUSTED CASH", len(records), "events", sum(len(r["checks"]) for r in records), "checks", "failures", failures)
assert not failures
