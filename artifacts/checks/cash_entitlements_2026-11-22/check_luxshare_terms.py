"""Read Luxshare notices independently and reconcile recipient-specific amounts."""

import re
import unicodedata
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).replace(",", "")


def iso_date(parts):
    return "-".join(f"{int(value):0{4 if index == 0 else 2}d}" for index, value in enumerate(parts))


def decimal(value):
    return Decimal(str(value))


root = Path.cwd()
checks_dir = Path(__file__).resolve().parent
protocol_path = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_path)
scope_path = root / protocol["scope"]["path"]
assert sha(scope_path) == protocol["scope"]["sha256"]
scope = {row["ex_date"]: row for row in read(scope_path)["records"] if row["symbol"] == "sz002475"}
inventory_path = checks_dir / "related_source_inventory.json"
inventory = read(inventory_path)
archived = {row["id"]: row for row in inventory["records"] if "sz002475" in row["symbols"]}
primary_ids = {"1220535439", "1224091515", "1224969419", "1225419586", "1225605405"}
bond_ids = {"1220513263", "1224068908", "1224952716", "1225412387", "1225597925"}
cap_id = "1225432952"
assert set(archived) == primary_ids | bond_ids | {cap_id}
date_pattern = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
records, failures = [], []
for event_date, registered in scope.items():
    observation_path = checks_dir / "observations" / f"sz002475_{event_date}.json"
    observation = read(observation_path)
    fields = observation["observed_fields"]
    source_id = observation["source_id"]
    row = archived[source_id]
    pdf = root / row["source"]["path"]
    assert sha(pdf) == row["source"]["sha256"] == observation["source"]["sha256"]
    with fitz.open(pdf) as document:
        pages = [page.get_text(sort=True) for page in document]
    text = compact("".join(pages))
    scheme = text.split("二、权益分派方案", 1)[1].split("三、股权登记日", 1)[0]
    amounts = re.findall(
        r"向(?:全体股东|A股股东)每10股派(?:发现金红利)?([\d.]+)元(?:人民币现金)?\(含税", scheme
    )
    dates = re.findall(
        r"本次权益分派股权登记日为:" + date_pattern + r"除权除息日为:" + date_pattern, text
    )
    payments = re.findall(
        r"代派的A股股东现金红利将于" + date_pattern + r"通过股东托管证券公司", text
    )
    gross = decimal(fields["cash_per_ten_shares"]) / 10
    participating = decimal(fields["participating_shares"])
    aggregate = decimal(fields["printed_implementation_aggregate"])
    calculated_aggregate = participating * gross
    flags = {
        "independent_reader_pages_match_inventory": pages == row["independent_pages"],
        "one_implemented_gross_statement": amounts == [fields["cash_per_ten_shares"]],
        "explicit_record_and_ex_dates": len(dates) == 1 and iso_date(dates[0][:3]) == fields["record_date"] and iso_date(dates[0][3:]) == event_date,
        "explicit_public_cash_payment_date": len(payments) == 1 and iso_date(payments[0]) == fields["cash_pay_date"],
        "cash_matches_current_at_existing_agreement_threshold": abs(decimal(registered["current_action"]["cash_per_share"]) - gross) <= Decimal("1e-12"),
        "original_share_multiplier_matches": decimal(registered["current_action"]["share_multiplier"]) == decimal(fields["share_multiplier"]),
        "implemented_capital_and_cash_present_in_scheme": fields["participating_shares"] in scheme and fields["cash_per_ten_shares"] in scheme,
    }
    added = fields.get("new_conversion_and_option_shares", fields.get("new_a_conversion_and_option_shares"))
    flags["capital_increase"] = decimal(fields["earlier_total_shares"]) + decimal(added) == decimal(fields["total_shares"])
    base = decimal(fields.get("a_total_shares", fields["total_shares"]))
    flags["participant_base"] = base - decimal(fields.get("repurchased_shares", "0")) == participating
    if event_date in {"2024-07-11", "2026-02-12"}:
        # A fixed aggregate is repeated next to a finite-precision per-ten quote.
        ratio = Fraction(fields["fixed_proposal_aggregate"]) * 10 / Fraction(fields["participating_shares"])
        displayed = Fraction(fields["cash_per_ten_shares"])
        flags["fixed_aggregate_repeated"] = fields["fixed_proposal_aggregate"] == fields["printed_implementation_aggregate"]
        flags["unrounded_ratio_within_displayed_six_place_cell"] = displayed <= ratio < displayed + Fraction(1, 10**6)
    else:
        flags["final_aggregate_at_printed_cent_precision"] = calculated_aggregate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == aggregate
    reference_raw = None
    if "ex_reference_cash_per_share" in fields:
        reference = decimal(fields["ex_reference_cash_per_share"])
        reference_raw = aggregate / base
        flags["reference_in_equity_price_formula"] = "股权登记日收盘价-" + str(reference) + "元/股" in text
        if event_date == "2026-07-20":
            flags["explicit_reference_truncation"] = "计算结果不四舍五入保留小数点后七位" in text
            flags["declared_reference_arithmetic"] = reference_raw.quantize(Decimal("0.0000001"), rounding=ROUND_DOWN) == reference
        else:
            flags["reference_consistent_at_printed_precision"] = reference_raw.quantize(Decimal("0.0000001"), rounding=ROUND_HALF_UP) == reference
            flags["A_and_H_capital_sum"] = decimal(fields["a_total_shares"]) + decimal(fields["h_total_shares"]) == decimal(fields["total_shares"])
            flags["H_share_operations_excluded"] = "H股股东(包含通过港股通持有H股的投资者)现金红利发放的具体操作不适用本公告" in text
            flags["original_all_class_participation"] = decimal(fields["earlier_total_shares"]) - decimal(fields["repurchased_shares"]) == decimal(fields["earlier_all_class_participating_shares"])
            flags["H_proposal_currency_arithmetic"] = (decimal(fields["h_total_shares"]) * decimal(fields["h_proposal_cash_per_ten_hkd"]) / 10).quantize(Decimal("0.01")) == decimal(fields["h_proposal_aggregate_hkd"])
    image_hashes = {}
    for page in observation["page_observations"]:
        assert page["whole_page_visually_inspected"]
        assert sha(root / page["image"]) == page["image_sha256"]
        image_hashes[page["image"]] = page["image_sha256"]
    failures.extend({"event_date": event_date, "check": name} for name, passed in flags.items() if not passed)
    records.append({
        "event_date": event_date, "source_id": source_id, "source": row["source"],
        "observation_sha256": sha(observation_path), "page_images": image_hashes,
        "checks": flags, "independent_pages": pages,
        "gross_per_share": str(gross), "calculated_aggregate": str(calculated_aggregate),
        "printed_aggregate_minus_product": str(aggregate - calculated_aggregate),
        "unrounded_reference": None if reference_raw is None else str(reference_raw),
        "limitations": "Six-place ratio consistency does not establish account rounding. Announced gross and announced reference remain separate; no global precision gate is relaxed.",
    })

related_decisions = []
for source_id, row in archived.items():
    pdf = root / row["source"]["path"]
    assert sha(pdf) == row["source"]["sha256"]
    if source_id in primary_ids:
        decision = "PRIMARY_IMPLEMENTATION_REVIEWED_IN_SEPARATE_OBSERVATION"
    elif source_id in bond_ids:
        body = compact("".join(row["independent_pages"]))
        valid = "暂停转股" in body and "可转换公司债券正常交易" in body
        if not valid:
            failures.append({"source_id": source_id, "check": "bond_conversion_role"})
        decision = "CONVERSION_SUSPENSION_AND_GENERAL_BOND_FORMULAS; complete text read; not a shareholder cash amendment or equity suspension"
    else:
        rendering_path = checks_dir / "renderings" / f"{source_id}.json"
        rendering = read(rendering_path)
        for page in rendering["pages"]:
            assert sha(root / page["image"]) == page["sha256"]
        body = compact("".join(row["independent_pages"]))
        valid = all(fragment in body for fragment in (
            "每10股派1.390814元(含税)", "即每股0.1387467元",
            "不送红股不以公积金转增股本", "2026年7月17日", "2026年7月20日",
        ))
        if not valid:
            failures.append({"source_id": source_id, "check": "cap_notice_repeats_implemented_terms"})
        decision = "REPURCHASE_PRICE_CAP_ADJUSTMENT; all three pages read and visually inspected; repeats July gross/reference/record/ex terms without changing shareholder entitlement; catalogue date is after ex-date, so retrospective corroboration only"
    related_decisions.append({"source_id": source_id, "source": row["source"], "receipt": row["receipt"], "decision": decision})

save(checks_dir / "issuer_checks" / "sz002475.json", {
    "status": "INDEPENDENT_ISSUER_CHECKS_PASS" if not failures else "INDEPENDENT_ISSUER_CHECKS_FAILED",
    "checked_at": now(), "script_sha256": sha(Path(__file__)),
    "protocol_sha256": sha(protocol_path), "inventory_sha256": sha(inventory_path),
    "reader_version": fitz.VersionBind, "records": records,
    "related_decisions": related_decisions, "failures": failures,
    "related_visual_review": {"source_id": cap_id, "physical_pages": [1, 2, 3], "rendering_sha256": sha(checks_dir / "renderings" / f"{cap_id}.json")},
    "archive_boundary": "All 11 received issuer notices were read. No shareholder-term amendment was identified among these notices. Referenced conversion-price companions are absent from the bound archive; completeness and exact publication time remain uncertified. Primary implementation notices supply the cash and dates; later cap corroboration is not an earlier input source.",
    "facts_accepted": 0, "runtime_inputs_changed": False,
    "acceptance_boundary": "Issuer-level evidence only. Full31-event decisions and preservation of the1839-event source inventory remain required before batch acceptance; no live dataset changes.",
}, immutable=True)
print("LUXSHARE", len(records), "events", sum(len(r["checks"]) for r in records), "checks", "failures", failures)
assert not failures
