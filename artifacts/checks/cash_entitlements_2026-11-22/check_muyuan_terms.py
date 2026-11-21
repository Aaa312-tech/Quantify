"""Independently verify the three registered Muyuan implementation notices."""

import re
import unicodedata
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
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
scope = {r["ex_date"]: r for r in read(scope_path)["records"] if r["symbol"] == "sz002714"}
inventory_path = checks_dir / "related_source_inventory.json"
archived = {
    r["id"]: r for r in read(inventory_path)["records"] if "sz002714" in r["symbols"]
}
primary_ids = {"1223934704", "1224703353", "1225321488"}
bond_ids = {"1223889234", "1224687102", "1225307828"}
prior_ids = {"1222113967", "1222180064"}
cap_id = "1223934705"
assert set(archived) == primary_ids | bond_ids | prior_ids | {cap_id}
date_pattern = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
records, failures = [], []
for event_date, registered in scope.items():
    observation_path = checks_dir / "observations" / f"sz002714_{event_date}.json"
    observation = read(observation_path)
    f = observation["observed_fields"]
    row = archived[observation["source_id"]]
    pdf = root / row["source"]["path"]
    assert sha(pdf) == row["source"]["sha256"] == observation["source"]["sha256"]
    with fitz.open(pdf) as document:
        pages = [page.get_text(sort=True) for page in document]
    text = compact("".join(pages))
    scheme = text.split("二、本次实施的权益分派方案", 1)[1].split("三、股权登记日", 1)[0]
    gross_values = re.findall(r"向全体(?:A股)?股东每10股派([\d.]+)元人民币现金\(含税", scheme)
    dates = re.findall(r"本次权益分派股权登记日为:" + date_pattern + r"除权除息日为:" + date_pattern, text)
    payments = re.findall(r"代派的A股股东现金红利将于" + date_pattern + r"通过股东托管证券公司", text)
    gross = Decimal(f["cash_per_ten_shares"]) / 10
    reference = Decimal(f["ex_reference_cash_per_share"])
    total, excluded, participants = (Decimal(f[key]) for key in ("total_shares", "repurchased_shares", "participating_shares"))
    a_total = Decimal(f.get("a_total_shares", f["total_shares"]))
    actual_total = Decimal(f["printed_implementation_aggregate"])
    unrounded_gross = Decimal(f["fixed_proposal_aggregate"]) / (total - excluded)
    unrounded_reference = actual_total / a_total
    flags = {
        "independent_reader_pages_match": pages == row["independent_pages"],
        "one_implemented_gross": gross_values == [f["cash_per_ten_shares"]],
        "explicit_record_and_ex_dates": len(dates) == 1 and iso_date(dates[0][:3]) == f["record_date"] and iso_date(dates[0][3:]) == event_date,
        "explicit_public_cash_payment": len(payments) == 1 and iso_date(payments[0]) == f["cash_pay_date"],
        "participant_subtraction": a_total - excluded == participants,
        "participant_base_present_in_scheme": f["participating_shares"] in scheme and f["repurchased_shares"] in scheme,
        "gross_quotient_truncation": unrounded_gross.quantize(Decimal("0.0000001"), rounding=ROUND_DOWN) == gross,
        "reference_quotient_truncation": unrounded_reference.quantize(Decimal("0.0000001"), rounding=ROUND_DOWN) == reference,
        "two_quotient_rules_explicit": text.count("计算结果不四舍五入保留小数点后七位") >= 2,
        "final_aggregate_at_printed_cent_precision": (participants * gross).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == actual_total,
        "reference_in_equity_price_formula": "股权登记日收盘价-" + str(reference) + "元/股" in text,
        "stored_cash_matches_at_unchanged_threshold": abs(Decimal(str(registered["current_action"]["cash_per_share"])) - gross) <= Decimal("1e-12"),
        "no_bonus_or_capitalisation": "不送红股不进行资本公积转增股本" in text,
        "stored_share_multiplier_unchanged": Decimal(str(registered["current_action"]["share_multiplier"])) == Decimal(f["share_multiplier"]),
        "self_paid_controls_distinct": "以下A股股东的现金红利由本公司自行派发" in text and "牧原实业集团有限公司" in text,
    }
    if "h_total_shares" in f:
        flags["A_plus_H_equals_total"] = a_total + Decimal(f["h_total_shares"]) == total
        flags["H_share_operations_excluded"] = "本公司H股股东的权益分派事宜不适用本公告" in text
    if "employee_plan_transfer_shares" in f:
        flags["employee_transfer_unit"] = "完成非交易过户至员工持股计划4187.01万股" in text and Decimal("4187.01") * 10000 == Decimal(f["employee_plan_transfer_shares"])
    image_hashes = {}
    for page in observation["page_observations"]:
        assert page["whole_page_visually_inspected"]
        assert sha(root / page["image"]) == page["image_sha256"]
        image_hashes[page["image"]] = page["image_sha256"]
    failures.extend({"event_date": event_date, "check": name} for name, passed in flags.items() if not passed)
    records.append({
        "event_date": event_date, "source_id": observation["source_id"], "source": row["source"],
        "observation_sha256": sha(observation_path), "checks": flags, "page_images": image_hashes,
        "independent_pages": pages, "unrounded_gross": str(unrounded_gross),
        "unrounded_reference": str(unrounded_reference), "gross_per_share": str(gross),
        "aggregate_product": str(participants * gross),
        "rounding_boundary": "The seven-place quotient rules determine announced per-share terms. No per-account cent rule is established by these notices.",
    })
related_decisions = []
for source_id, row in archived.items():
    pdf = root / row["source"]["path"]
    assert sha(pdf) == row["source"]["sha256"]
    text = compact("".join(row["independent_pages"]))
    if source_id in primary_ids:
        decision = "PRIMARY_IMPLEMENTATION; separately reviewed full text and relevant whole pages"
    elif source_id in bond_ids:
        valid = "暂停转股" in text and "可转换公司债券正常交易" in text
        if not valid:
            failures.append({"source_id": source_id, "check": "bond_conversion_role"})
        decision = "CONVERSION_SUSPENSION; full text read, including any bond-formula appendix; no shareholder-term amendment or equity-trading suspension"
    elif source_id in prior_ids:
        valid = "2024年前三季度" in text and "2024年12月30日" in text
        if not valid:
            failures.append({"source_id": source_id, "check": "distinct_prior_period"})
        decision = "DISTINCT_PRIOR_EVENT; full text read; December2024 implementation or its cap adjustment, not an amendment of the three scoped events"
    else:
        valid = all(fragment in text for fragment in (
            "每10股派5.742689元人民币现金", "分红总额3082880032.18元",
            "0.5643439元/股", "2025年6月26日",
        ))
        if not valid:
            failures.append({"source_id": source_id, "check": "cap_notice_agrees"})
        decision = "REPURCHASE_CAP_ADJUSTMENT; all three pages read and visually inspected; repeats2025 annual gross, aggregate, reference and ex-date without altering shareholder terms"
    related_decisions.append({"source_id": source_id, "source": row["source"], "receipt": row["receipt"], "decision": decision})
rendering_path = checks_dir / "renderings" / f"{cap_id}.json"
for page in read(rendering_path)["pages"]:
    assert sha(root / page["image"]) == page["sha256"]
save(checks_dir / "issuer_checks" / "sz002714.json", {
    "status": "INDEPENDENT_ISSUER_CHECKS_PASS" if not failures else "INDEPENDENT_ISSUER_CHECKS_FAILED",
    "checked_at": now(), "script_sha256": sha(Path(__file__)),
    "protocol_sha256": sha(protocol_path), "inventory_sha256": sha(inventory_path),
    "reader_version": fitz.VersionBind, "records": records, "related_decisions": related_decisions,
    "related_visual_review": {"source_id": cap_id, "physical_pages": [1, 2, 3], "rendering_sha256": sha(rendering_path)},
    "failures": failures, "facts_accepted": 0, "runtime_inputs_changed": False,
    "archive_boundary": "All nine received issuer notices were read. No amendment of scoped shareholder terms was identified within this archive. Referenced conversion-price notices are absent; archive completeness and exact publication time are not certified. Other-period notices are not new accepted facts.",
    "acceptance_boundary": "Issuer checks only; full31-event decisions and preservation across1839 events are still required before source acceptance and any separately registered economic integration.",
}, immutable=True)
print("MUYUAN", len(records), "events", sum(len(r["checks"]) for r in records), "checks", "failures", failures)
assert not failures
