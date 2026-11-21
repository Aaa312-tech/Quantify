"""Bind Jinjiang's proposal correction to the already supported A-share event."""

import re
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

import joblib

from quantfolio.storage import digest, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/supplemental_amendment_sources.json")
REVIEW = Path("artifacts/checks/cash_entitlements_2026-11-22/source_review.json")
BUNDLE = Path("data/processed/2026-11-20-recipient-entitlements/bundle.joblib")
BUNDLE_SHA = "c8d4fef3dfacac24c7321edc5a095716d47931096db25f80c61d5e43e0627df2"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def prose(value):
    value = unicodedata.normalize("NFKC", value)
    # Join wrapped Chinese prose only; never join separated digits or remove page counters.
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)


def main():
    protocol = read(PROTOCOL)
    for item in protocol["inputs"]:
        require(sha(item["path"]) == item["sha256"], "Registered input changed")
    acquisition = read(BASE / "supplemental_acquisition.json")
    require(acquisition["protocol_sha256"] == sha(PROTOCOL), "Protocol differs")
    require(
        acquisition["script_sha256"] == sha(BASE / "archive_supplemental_notice.py"),
        "Executed acquisition reader changed",
    )
    for path, expected in acquisition["outputs"].items():
        require(sha(path) == expected, "Archived supplement differs")
    docs = load_gzip(BASE / "supplemental_source_text.json.gz")["documents"]
    notes = read(BASE / "supplemental_page_notes.json")
    expected_pages = {"1221086914": 2, "1221114875": 2, "1221258694": 5}
    require(set(docs) == set(expected_pages), "Comparison scope differs")
    observed = {(r["id"], r["page"]) for r in notes["records"]}
    require(
        observed
        == {(key, p) for key, count in expected_pages.items() for p in range(1, count + 1)},
        "Whole-page observation scope differs",
    )
    for note in notes["records"]:
        require(note["full_page_actually_inspected"], "Unobserved page")
        require(sha(note["image"]) == note["image_sha256"], "Observed image changed")
    numeric_pages, numeric_tokens = [], 0
    for key, doc in docs.items():
        source = doc["source"]
        require(sha(source["path"]) == source["sha256"], "PDF changed")
        require(sha(source["receipt"]) == source["receipt_sha256"], "Receipt changed")
        readers = doc["readers"]
        require(all(len(p) == expected_pages[key] for p in readers.values()), "Page count")
        for page, pair in enumerate(zip(readers["pypdf"], readers["mupdf_unsorted"]), 1):
            inventories = [
                Counter(re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", unicodedata.normalize("NFKC", t)))
                for t in pair
            ]
            require(inventories[0] == inventories[1], f"Numeric reader difference: {key}:{page}")
            numeric_tokens += sum(inventories[0].values())
            numeric_pages.append({"id": key, "page": page, "tokens": dict(inventories[0])})
    checks = []
    for reader in ["pypdf", "mupdf_unsorted"]:
        original = [prose(p) for p in docs["1221086914"]["readers"][reader]]
        corrected = [prose(p) for p in docs["1221114875"]["readers"][reader]]
        implemented = [prose(p) for p in docs["1221258694"]["readers"][reader]]
        for pages, notice in [
            (original, "2024-043"),
            (corrected, "2024-048"),
            (implemented, "2024-053"),
        ]:
            require(re.search(r"公告编号:\s*" + notice, pages[0]), "Notice identity differs")
            require("600754" in pages[0] and "900934" in pages[0], "A/B identity differs")
            require(re.search(r"2024\s*年半年度", pages[0]), "Financial period differs")
        cash_pattern = r"每\s*(10)?\s*股派发现金红利\s*(\d+\.\d+)\s*元"
        old_cash = re.findall(cash_pattern, original[0])
        new_cash = re.findall(cash_pattern, corrected[0])
        require(old_cash == [("", "1.2"), ("10", "1.2")], "Original cash conflict differs")
        require(new_cash == [("", "0.12"), ("10", "1.2")], "Corrected cash differs")
        require("127,445,287.56亿元" in original[0], "Original unit conflict differs")
        require(re.search(r"127,445,287\.56\s*元", corrected[0]), "Corrected aggregate differs")
        require("拟维持分配总额不变,相应调整每股分配比例" in original[0], "Original policy")
        policy = "维持每股分配金额不变,相应调整分配总额"
        require(policy in original[1], "Original body policy")
        require(policy in corrected[0] and policy in corrected[1], "Corrected policy")
        require("股份基数发生变动" in corrected[0], "Corrected base trigger")
        for p in [original[0], corrected[0]]:
            require("具体日期将在权益分派实施公告中明确" in p, "Proposal date deferral")
        require(re.search(r"A\s*股每股现金红利\s*0\.12\s*元", implemented[0]), "A cash")
        require(re.search(r"B\s*股每股现金红利\s*0\.016872\s*美元", implemented[0]), "B cash")
        require(re.search(r"每\s*10\s*股派发现金红利\s*1\.20\s*元", implemented[1]), "Gross cash")
        for amount in ["1,070,044,063", "8,000,000", "1,062,044,063", "127,445,287.56"]:
            require(amount in corrected[0] and amount in implemented[1], "Base/aggregate differs")
        require("无资本公积金转增股本和送红股" in implemented[1], "Cash-only scheme")
        for page in [1, 3]:
            text = implemented[page - 1]
            require(
                re.search(r"A\s*股\s+2024/9/26\s+-\s+2024/9/27\s+2024/9/27", text),
                "A-share labelled dates differ",
            )
            require(
                re.search(r"B\s*股\s+2024/10/8\s+2024-09-26\s+2024/10/18", text),
                "B-share labelled dates differ",
            )
        checks.append({"reader": reader, "status": "MATCHED_OBSERVED_SCHEME_AND_CORRECTION"})
    require(Decimal("1070044063") - Decimal("8000000") == Decimal("1062044063"), "Eligible base")
    require(Decimal("1062044063") * Decimal("0.12") == Decimal("127445287.56"), "Aggregate")
    records = read(REVIEW)["records"]
    accepted = [r for r in records if r.get("verified_fields")]
    require(len(records) == 1839 and len(accepted) == 1452, "Prior source scope changed")
    prior = next(r for r in records if (r["symbol"], r["ex_date"]) == ("sh600754", "2024-09-27"))
    require(prior["verified_fields"] == {"cash_pay_date": "2024-09-27"}, "Accepted scope changed")
    require(prior["known_at"] == "2024-09-22T00:00:00+08:00", "Known-at changed")
    require(prior["source"]["sha256"] == docs["1221258694"]["source"]["sha256"], "Prior primary")
    require(sha(BUNDLE) == BUNDLE_SHA, "Active bundle changed")
    actions = joblib.load(BUNDLE)["actions"]
    action = next(a for a in actions if (a["symbol"], a["ex_date"]) == ("sh600754", "2024-09-27"))
    expected = {
        "record_date": "2024-09-26",
        "cash_pay_date": "2024-09-27",
        "cash_available_date": "2024-09-30",
        "cash_per_share": 0.12,
        "bonus_per_share": 0.0,
        "capitalization_per_share": 0.0,
        "share_list_date": None,
    }
    require(all(action[k] == v for k, v in expected.items()), "Existing action differs")
    require(action["distribution_review"]["known_at"] == prior["known_at"], "Runtime known-at")
    unresolved = sorted(
        f"{r['symbol']}/{r['ex_date']}"
        for r in records
        if r["symbol"] == "sh600754" and not r.get("verified_fields")
    )
    require(unresolved == ["sh600754/2025-08-01", "sh600754/2026-05-21"], "Residual scope differs")
    # Correct a provisional manual-note date explicitly; retain that note's original bytes.
    require(
        notes["unrelated_residual_events"] == ["sh600754/2025-06-23", "sh600754/2026-05-21"],
        "Note changed",
    )
    inputs = [
        PROTOCOL,
        REVIEW,
        BASE / "supplemental_acquisition.json",
        BASE / "supplemental_source_text.json.gz",
        BASE / "supplemental_page_notes.json",
    ]
    result = {
        "status": "PASS_SCOPED_SUPPLEMENTAL_RELATION",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "dependencies": {p.as_posix(): sha(p) for p in inputs},
        "documents_read_in_full": 3,
        "pages_read_in_full": 9,
        "whole_pages_observed": 9,
        "numeric_inventory": {"page_pairs": numeric_pages, "tokens_per_reader": numeric_tokens},
        "reader_checks": checks,
        "eligible_base": "1062044063",
        "gross_aggregate_cny": "127445287.56",
        "a_share": {
            "symbol": "sh600754",
            "gross_cash_cny": "0.12",
            "record_date": "2024-09-26",
            "ex_date": "2024-09-27",
            "cash_pay_date": "2024-09-27",
        },
        "b_share_context_only": {
            "code": "900934",
            "gross_cash_usd": "0.016872",
            "record_date": "2024-10-08",
            "last_trade_date": "2024-09-26",
            "ex_date": "2024-09-27",
            "ex_date_basis": "Observed merged cell on pages 1 and 3",
            "cash_pay_date": "2024-10-18",
        },
        "prior_record_sha256": digest(prior),
        "all_prior_records_sha256": digest(records),
        "prior_accepted_events_preserved": len(accepted),
        "active_action_sha256": digest(action),
        "active_bundle_sha256": sha(BUNDLE),
        "prior_known_at_preserved": prior["known_at"],
        "manual_note_erratum": {
            "field": "unrelated_residual_events",
            "prior": notes["unrelated_residual_events"],
            "corrected": unresolved,
            "basis": REVIEW.as_posix(),
            "effect": "Unrelated period metadata only; both remain unresolved",
        },
        "prior_notice_number_erratum": notes["prior_note_correction"],
        "relation": "ORIGINAL_CONFLICTS_CORRECTED_BEFORE_IMPLEMENTATION",
        "new_financial_facts_accepted": 0,
        "active_changes": False,
        "all_residual_events_still_unverified": 387,
        "archive_completeness_certified": False,
        "whole_goal_complete": False,
    }
    require(numeric_tokens == 236, "Numeric inventory scope differs")
    save(BASE / "supplemental_relation_review.json", result, immutable=True)
    print(
        result["status"],
        "9 pages; 236 numeric tokens per reader; existing A-share action preserved",
    )


if __name__ == "__main__":
    main()
