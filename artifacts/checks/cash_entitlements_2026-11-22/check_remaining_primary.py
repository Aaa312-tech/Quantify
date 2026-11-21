"""Independently read and reconcile the eighteen remaining primary cash schemes."""

import re
import unicodedata
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


def require(condition, context):
    if not condition:
        raise ValueError(context)


root = Path.cwd()
checks_dir = Path(__file__).resolve().parent
protocol_file = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_file)
scope_file = root / protocol["scope"]["path"]
require(sha(scope_file) == protocol["scope"]["sha256"], "Registered scope hash changed")
scope = {(r["symbol"], r["ex_date"]): r for r in read(scope_file)["records"]}
inventory_file = checks_dir / "related_source_inventory.json"
inventory = read(inventory_file)
sources = {r["id"]: r for r in inventory["records"]}
excluded_issuers = {
    "sz002475",
    "sz002714",
    "sz300418",
    "sz000988",
    "sz300223",
    "sz300014",
    "sz300972",
}
cases = sorted(key for key in scope if key[0] not in excluded_issuers)
require(len(cases) == 18, "Independent case inventory must contain eighteen events")
expected_corrections = {
    ("sz300750", "2024-04-30"): Decimal("5.028"),
    ("sh600989", "2024-07-24"): Decimal("0.3158"),
    ("sh600989", "2025-05-13"): Decimal("0.4598"),
}


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).replace(",", "")


def without_counters(pages):
    result, removals = [], []
    for number, page in enumerate(pages, 1):
        lines = page.strip().splitlines()
        for edge in (0, -1):
            if lines and lines[edge].strip() == str(number):
                removals.append({"physical_page": number, "edge": edge, "line": lines.pop(edge)})
        result.append("\n".join(lines))
    return compact("\n".join(result)), removals


def iso(parts):
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


date_pattern = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
records, failures = [], []
for symbol, event_date in cases:
    observation_path = checks_dir / "observations" / f"{symbol}_{event_date}.json"
    observation = read(observation_path)
    f = observation["observed_fields"]
    source = sources[observation["source_id"]]
    require(sha(root / source["source"]["path"]) == source["source"]["sha256"], "PDF hash changed")
    require(
        source["source"]["sha256"] == observation["source"]["sha256"],
        "PDF observation identity differs",
    )
    require(
        sha(root / observation["receipt"]["path"]) == observation["receipt"]["sha256"],
        "Source receipt changed",
    )
    with fitz.open(root / source["source"]["path"]) as document:
        pages = [page.get_text(sort=True) for page in document]
    text, counters = without_counters(pages)
    gross = (
        Decimal(f["cash_per_ten_shares"]) / 10
        if f["cash_unit_shares"] == 10
        else Decimal(f["cash_per_share"])
    )
    total, eligible = (Decimal(f[k]) for k in ("total_shares", "participating_shares"))
    flags = {
        "source_pages_equal_independent_inventory": pages == source["independent_pages"],
        "complete_primary_text_page_count": len(pages) == observation["full_text_pages_read"],
        "issuer_security_code_in_first_page": symbol[2:] in compact(pages[0]),
        "eligible_capital_identity": total - Decimal(f.get("excluded_shares", "0")) == eligible,
        "current_multiplier_unchanged": Decimal(
            str(scope[(symbol, event_date)]["current_action"]["share_multiplier"])
        )
        == Decimal(f["share_multiplier"]),
    }
    if symbol.startswith("sz"):
        section = re.search(r"二、(?:本次实施的|本次)?(?:权益分派|利润分配)方案(.*?)三、", text)
        scheme = section.group(1) if section else ""
        gross_matches = re.findall(
            r"每10股派(?:发)?(?:现金(?:分红|红利|股利)(?:人民币)?)?([\d.]+)元(?:人民币现金|现金)?\(含税",
            scheme,
        )
        # The implementation, rather than a proposal elsewhere in the notice, supplies gross cash.
        flags["one_gross_in_implemented_scheme"] = gross_matches == [f["cash_per_ten_shares"]]
        flags["participating_capital_in_implemented_scheme"] = f["participating_shares"] in scheme
        record_dates = re.findall(r"(?:A股)?股权登记日(?:为)?:" + date_pattern, text)
        ex_dates = re.findall(r"(?:A股)?除权除息日(?:为)?:" + date_pattern, text)
        payments = re.findall(
            r"代派的A股股东现金(?:红利|分红)将于" + date_pattern + r"通过股东托管证券公司", text
        )
        flags["explicit_record_date"] = bool(record_dates) and {iso(d) for d in record_dates} == {
            f["record_date"]
        }
        flags["explicit_ex_date"] = bool(ex_dates) and {iso(d) for d in ex_dates} == {event_date}
        flags["explicit_public_payment_date"] = bool(payments) and {iso(d) for d in payments} == {
            f["cash_pay_date"]
        }
    else:
        dates_section = re.search(r"三、相关日期(.*?)四、", text)
        table = dates_section.group(1) if dates_section else ""
        dates = [iso(d) for d in re.findall(r"(\d{4})/(\d{1,2})/(\d{1,2})", table)]
        flags["labelled_date_table"] = all(
            label in table for label in ("股权登记日", "除权(息)日", "现金红利发放日")
        )
        flags["record_ex_payment_table_values"] = dates == [
            f["record_date"],
            event_date,
            f["cash_pay_date"],
        ]
        if symbol == "sh600989":
            minority = re.findall(r"中小股东每股(?:应)?派发现金红利(?:人民币)?([\d.]+)元", text)
            major = re.findall(r"大股东每股(?:应)?派发现金红利(?:人民币)?([\d.]+)元", text)
            flags["minority_gross_repeated_by_recipient"] = len(minority) >= 2 and {
                Decimal(v) for v in minority
            } == {gross}
            flags["control_gross_repeated_by_recipient"] = len(major) >= 2 and {
                Decimal(v) for v in major
            } == {Decimal(f["controlling_holder_cash_per_share"])}
            ordinary = re.findall(r"每股实际派发现金红利人民币([\d.]+)元", text)
            flags["ordinary_public_holder_gross_repeated"] = (
                ordinary.count(f["cash_per_share"]) >= 2
            )
            flags["minority_and_control_aggregate_sum"] = Decimal(
                f["minority_aggregate"]
            ) + Decimal(f["controlling_holder_aggregate"]) == Decimal(
                f["printed_implementation_aggregate"]
            )
            flags["cash_only_zero_share_change"] = (
                "本次分红全部为现金分红" in text and "流通股份变动比例为零" in text
            )
        else:
            initial_cash = re.findall(r"每股现金红利([\d.]+)元", compact(pages[0]))
            flags["summary_gross_amount"] = initial_cash == [f["cash_per_share"]]
            flags["gross_in_institutional_tax_clause"] = bool(
                re.search(r"税前每股人民币" + re.escape(f["cash_per_share"]) + "元", text)
            )
            flags["zero_bonus_and_capitalisation"] = (
                "不送红股" in text or "无资本公积转增股本和送红股" in text
            ) and "流通股份变动比例为0" in text
    signature_dates = re.findall(date_pattern, compact(pages[-1]))
    flags["signature_date"] = (
        bool(signature_dates) and iso(signature_dates[-1]) == f["signature_date"]
    )
    old_cash = Decimal(str(scope[(symbol, event_date)]["current_action"]["cash_per_share"]))
    if (symbol, event_date) in expected_corrections:
        flags["explicit_recipient_or_gross_correction"] = gross == expected_corrections[
            (symbol, event_date)
        ] and abs(old_cash - gross) > Decimal("1e-12")
    else:
        flags["current_cash_agrees_at_unchanged_threshold"] = abs(old_cash - gross) <= Decimal(
            "1e-12"
        )

    diagnostics = {
        "gross_per_share": str(gross),
        "eligible_capital_times_gross": str(eligible * gross),
    }
    if "printed_implementation_aggregate" in f and symbol != "sh600989":
        rounding = ROUND_DOWN if symbol == "sz300871" else ROUND_HALF_UP
        flags["printed_aggregate_at_stated_precision"] = (eligible * gross).quantize(
            Decimal("0.01"), rounding=rounding
        ) == Decimal(f["printed_implementation_aggregate"])
        diagnostics["aggregate_difference_before_rounding"] = str(
            Decimal(f["printed_implementation_aggregate"]) - eligible * gross
        )
    if "fixed_proposal_aggregate" in f:
        ratio = Decimal(f["fixed_proposal_aggregate"]) / eligible
        digits = 5 if symbol.startswith("sh") else 7
        step = Decimal(1).scaleb(-digits)
        diagnostics["fixed_total_ratio"] = str(ratio)
        diagnostics["ratio_at_printed_precision_round_half_up"] = str(
            ratio.quantize(step, rounding=ROUND_HALF_UP)
        )
        diagnostics["ratio_at_printed_precision_truncated"] = str(
            ratio.quantize(step, rounding=ROUND_DOWN)
        )
        diagnostics["ratio_difference_from_declared_gross"] = str(ratio - gross)
        # Only explicitly stated ratio rules become exact checks; the other figures remain diagnostics.
        if symbol == "sz000858":
            flags["explicit_gross_rounding_and_arithmetic"] = (
                "四舍五入保留小数点后七位" in text
                and ratio.quantize(step, rounding=ROUND_HALF_UP) == gross
            )
        if symbol == "sz300871":
            flags["explicit_gross_truncation_and_arithmetic"] = (
                "保留六位小数最后一位直接截取不四舍五入" in text
                and ratio.quantize(step, rounding=ROUND_DOWN) == gross
            )
            flags["aggregate_only_two_place_truncation"] = (
                "由于保留两位小数最后一位直接截取不四舍五入" in text
            )
    if "earlier_total_shares" in f:
        increase = f.get(
            "new_incentive_shares",
            f.get(
                "incentive_shares_added",
                f.get("converted_shares_added", f.get("new_conversion_shares")),
            ),
        )
        cancellation = f.get("cancelled_a_shares", f.get("cancelled_shares"))
        if increase:
            flags["capital_increase_identity"] = (
                Decimal(f["earlier_total_shares"]) + Decimal(increase) == total
            )
        if cancellation:
            flags["capital_cancellation_identity"] = (
                Decimal(f["earlier_total_shares"]) - Decimal(cancellation) == total
            )
    if "a_shares" in f:
        flags["a_h_capital_partition"] = Decimal(f["a_shares"]) + Decimal(f["h_shares"]) == total
        flags["h_share_operations_are_separate"] = "H股股东的权益分派事宜另见" in text
        approximate = Decimal(f["printed_approximate_all_class_aggregate"]) * 1000
        flags["all_class_aggregate_at_explicit_approximate_thousand_precision"] = (
            total * gross
        ).quantize(Decimal("1E3"), rounding=ROUND_HALF_UP) == approximate
        diagnostics["approximate_all_class_total_rmb"] = str(approximate)
    if "ex_reference_cash_per_share" in f:
        reference = f["ex_reference_cash_per_share"]
        formula_text = re.sub(r"[−﹣－]", "-", text)
        flags["declared_reference_in_equity_price_formula"] = bool(
            re.search(
                r"(?:收盘价|收盘价格|现金分红|现金红利)-" + re.escape(reference) + r"(?:元|\))",
                formula_text,
            )
        )
        base_amount = (
            Decimal(f["printed_implementation_aggregate"])
            if "printed_implementation_aggregate" in f
            else eligible * gross
        )
        if symbol == "sz301362":
            base_amount = Decimal(f["fixed_proposal_aggregate"])
            flags["reference_explicit_fixed_total_denominator"] = (
                "199919700.00元÷104670000股×10=19.100000元" in text
            )
        diagnostics["reference_ratio_before_precision"] = str(base_amount / total)
        if symbol in {"sz000858", "sz300871", "sz301313", "sz300750", "sz301362"}:
            flags["reference_truncation_arithmetic"] = (base_amount / total).quantize(
                Decimal("0.0000001"), rounding=ROUND_DOWN
            ) == Decimal(reference)
    if symbol == "sz301052":
        flags["capitalisation_multiplier"] = Decimal(1) + Decimal(
            f["capitalization_per_ten_shares"]
        ) / 10 == Decimal(f["share_multiplier"])
        flags["no_bonus_shares"] = "不送红股" in text and f["bonus_per_ten_shares"] == "0"
        flags["post_distribution_capital_identity"] = total + Decimal(
            f["implemented_capitalization_total"]
        ) == Decimal(f["post_distribution_capital"])
        listing = re.findall(r"(?:起始交易日|上市日)(?:为)?:?" + date_pattern, text)
        flags["explicit_unrestricted_capitalisation_listing"] = bool(listing) and {
            iso(d) for d in listing
        } == {f["share_list_date"]}
        diagnostics["capitalisation_aggregate_before_fractional_allocation"] = str(
            eligible * Decimal(f["capitalization_per_ten_shares"]) / 10
        )

    for page in observation["page_observations"]:
        require(page["whole_page_visually_inspected"] is True, "Page lacks actual visual review")
        require(sha(root / page["image"]) == page["image_sha256"], "Reviewed page image changed")
    failures.extend(
        {"symbol": symbol, "ex_date": event_date, "check": name}
        for name, passed in flags.items()
        if not passed
    )
    records.append(
        {
            "symbol": symbol,
            "ex_date": event_date,
            "source": source["source"],
            "observation_sha256": sha(observation_path),
            "independent_pages": pages,
            "standalone_physical_counter_lines_removed": counters,
            "checks": flags,
            "diagnostics": diagnostics,
            "related_source_ids": [r["id"] for r in inventory["records"] if symbol in r["symbols"]],
            "related_review": "PENDING_CONTEXTUAL_CLOSURE",
        }
    )

save(
    checks_dir / "remaining_primary_checks.json",
    {
        "status": "INDEPENDENT_PRIMARY_CHECKS_PASS"
        if not failures
        else "INDEPENDENT_PRIMARY_CHECKS_FAILED",
        "checked_at": now(),
        "script_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(protocol_file),
        "inventory_sha256": sha(inventory_file),
        "reader_version": fitz.VersionBind,
        "records": records,
        "failures": failures,
        "facts_accepted": 0,
        "runtime_inputs_changed": False,
        "boundary": "Primary-only checks do not close related-notice review, whole-inventory preservation, source acceptance or runtime integration. Arithmetic at printed precision does not establish account rounding; diagnostic ratios are not tolerance changes. All previous failures remain preserved.",
    },
    immutable=True,
)
print(
    "REMAINING PRIMARY",
    len(records),
    "events",
    sum(len(r["checks"]) for r in records),
    "checks",
    "failures",
    failures,
)
if failures:
    raise SystemExit(1)
