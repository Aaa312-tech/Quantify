"""Close the received amendment inventories for three fully reviewed issuers."""

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).replace(",", "")


root = Path.cwd()
checks_dir = Path(__file__).resolve().parent
inventory_file = checks_dir / "related_source_inventory.json"
inventory = read(inventory_file)
primary_file = checks_dir / "remaining_primary_checks.json"
primary = read(primary_file)
closure_file = checks_dir / "remaining_primary_closure.json"
closure = read(closure_file)
require(
    closure["initial_receipt"]["sha256"] == sha(primary_file), "Primary closure identity changed"
)
closed = {(r["symbol"], r["ex_date"], r["initial_failed_check"]) for r in closure["records"]}
protocol_file = root / "configs/protocols/cash_entitlement_review.json"
notes = {
    "1219677643": "2024-019 corrects only the 2023 annual proposal aggregate: 1632643358.20 becomes 163264335.82; the 1.90 per-ten ratio and 859285978 eligible shares are unchanged. This is not an amendment to the 2024 or 2025 profit-period distributions scoped here.",
    "1219677644": "2024-021 restates the corrected 2023 annual proposal, still subject to its shareholder vote. It reports eligible 859285978, gross 1.90 per ten and total 163264335.82; no current-scope payment change.",
    "1220211046": "2024-026 implements that distinct 2023 annual scheme: record 2024-06-06, ex/payment 2024-06-07, gross 1.900000 per ten, total 163264335.82 and reference 0.1892505. No amendment to either scoped event.",
    "1223239368": "Scoped 2024 annual implementation, ex/payment 2025-04-30. Complete primary observation and independent checks are bound separately.",
    "1225091227": "Scoped 2025 annual implementation, ex/payment 2026-04-15. Complete primary observation and independent checks are bound separately.",
    "1220541984": "Distinct 2023 annual scheme, ex/payment 2024-07-12, gross 46.70 per ten. Its entire three-page text contains no amendment of the scoped 2025 annual event.",
    "1222343328": "Distinct 2024 interim scheme, ex/payment 2025-01-23, gross 25.76 per ten. Entire three-page text read; not the scoped 2025 annual implementation.",
    "1224145916": "Distinct 2024 annual scheme, ex/payment 2025-07-18, gross 31.69 per ten. Entire three-page text read; not a correction of the scoped event.",
    "1224864581": "Distinct 2025 interim scheme, ex/payment 2025-12-18, gross 25.78 per ten. Its fixed total equals the later proposal total, but its explicit period and dates identify a separate distribution; do not merge the events.",
    "1225417855": "Scoped 2025 annual implementation, ex/payment 2026-07-16, gross 25.796852 per ten and reference 2.5779999. Primary observation and independent checks are bound separately.",
    "1225426598": "2026-033 repeats the scoped final eligible capital, gross, aggregate, record/ex dates and reference. It adjusts only the buyback cap from 153.59 to 151.01, effective 2026-07-16. This ex-date publication is retrospective corroboration, not earlier signal-time evidence.",
    "1220160066": "Scoped 2023 annual implementation, ex/payment 2024-05-31. Primary observation and the preserved aggregate-rounding limitation are bound separately.",
    "1220160067": "2024-065 repeats original and final distribution amounts, final aggregate 15631435.79, reference 0.1493067 and record/ex dates. Only the buyback cap changes from 30.00 to 29.85; cent rounding of that cap does not specify dividend-account settlement.",
}
page_notes = {
    "1219677643": [
        "The explicit before/after correction changes the 2023 proposal total by a factor of ten; gross per ten and eligible shares are unchanged. Signature 2024-04-19."
    ],
    "1219677644": [
        "Corrected 2023 proposal: eligible 859285978 at 1.90 per ten, total 163264335.82. It remains a proposal subject to shareholder approval.",
        "Governance approvals and risk section still require a shareholder vote. Signature 2024-04-19; no scoped implementation date or amount change.",
    ],
    "1220160067": [
        "301313 / 2024-065 identifies a buyback cap adjustment: 30.00 to 29.85, effective 2024-05-31.",
        "The distinct proposal and implementation repeat eligible 104166100, gross 1.500626 per ten, final aggregate 15631435.79 and reference 0.1493067. Reference truncation is separate from gross and account cash.",
        "Record 2024-05-30 and ex 2024-05-31. Buyback-cap subtraction and two-place rounding are explicit; signature 2024-05-24. No shareholder-term amendment.",
    ],
    "1225426598": [
        "000858 / 2026-033 identifies a buyback cap change from 153.59 to 151.01 effective 2026-07-16, distinct from shareholder cash.",
        "Final total 3881608005 minus repurchased 2535759 leaves 3879072246; gross 25.796852 per ten and aggregate 10006785262.74 match the primary implementation. Record/ex dates 2026-07-15/16 repeat.",
        "Reference 2.5779999 and seven-place truncation repeat. Cap rounds to 151.01; signature and effective date 2026-07-16. This is retrospective corroboration.",
    ],
}
expected_ids = {
    "sz300476": {"1219677643", "1219677644", "1220211046", "1223239368", "1225091227"},
    "sz000858": {
        "1220541984",
        "1222343328",
        "1224145916",
        "1224864581",
        "1225417855",
        "1225426598",
    },
    "sz301313": {"1220160066", "1220160067"},
}
for symbol, ids in expected_ids.items():
    issuer_rows = [r for r in inventory["records"] if symbol in r["symbols"]]
    require({r["id"] for r in issuer_rows} == ids, "Received issuer inventory changed")
    texts, decisions, visual = {}, [], []
    for row in issuer_rows:
        require(sha(root / row["source"]["path"]) == row["source"]["sha256"], "Source PDF changed")
        require(
            sha(root / row["receipt"]["path"]) == row["receipt"]["sha256"], "Source receipt changed"
        )
        with fitz.open(root / row["source"]["path"]) as document:
            pages = [page.get_text(sort=True) for page in document]
        require(pages == row["independent_pages"], "Independent text no longer matches inventory")
        texts[row["id"]] = compact("\n".join(pages))
        decisions.append(
            {
                "source_id": row["id"],
                "source": row["source"],
                "catalog_date": row["catalog_date"],
                "title": row["title"],
                "complete_text_pages_actually_read": len(row["pages"]),
                "independent_reader_matches": True,
                "decision": notes[row["id"]],
            }
        )
        if row["id"] in page_notes:
            rendering_file = checks_dir / "renderings" / f"{row['id']}.json"
            rendering = read(rendering_file)
            require(
                len(rendering["pages"]) == len(page_notes[row["id"]]), "Image inventory changed"
            )
            for page, note in zip(rendering["pages"], page_notes[row["id"]], strict=True):
                require(sha(root / page["image"]) == page["sha256"], "Reviewed image changed")
                visual.append(
                    {
                        "source_id": row["id"],
                        "page": page["page"],
                        "image": page["image"],
                        "image_sha256": page["sha256"],
                        "whole_page_visually_inspected": True,
                        "observation": note,
                        "rendering_sha256": sha(rendering_file),
                    }
                )
    if symbol == "sz300476":
        flags = {
            "correction_contains_before_and_after": "更正前:" in texts["1219677643"]
            and "更正后:" in texts["1219677643"],
            "corrected_total_exact": Decimal("859285978") * Decimal("1.90") / 10
            == Decimal("163264335.82"),
            "original_typo_is_ten_times_corrected_total": Decimal("1632643358.20")
            == Decimal("163264335.82") * 10,
            "three_notices_repeat_corrected_total": all(
                "163264335.82" in texts[key] for key in ("1219677643", "1219677644", "1220211046")
            ),
            "correction_period_is_2023": all(
                "2023年度利润分配预案" in texts[key] for key in ("1219677643", "1219677644")
            ),
            "distinct_earlier_ex_date": "除权除息日为:2024年6月7日" in texts["1220211046"],
        }
    elif symbol == "sz000858":
        text = texts["1225426598"]
        flags = {
            "eligible_capital": Decimal("3881608005") - Decimal("2535759") == Decimal("3879072246"),
            "gross_repeated": "每10股派发现金股利人民币25.796852元(含税)" in text,
            "aggregate_repeated": "共计派发现金10006785262.74元" in text,
            "record_date_repeated": "股权登记日为2026年7月15日" in text,
            "ex_date_repeated": "除权除息日为2026年7月16日" in text,
            "reference_repeated": "=2.5779999元/股" in text,
            "buyback_cap_arithmetic": (Decimal("153.59") - Decimal("2.5779999")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            == Decimal("151.01"),
            "publication_not_used_as_earlier_information": next(
                r for r in issuer_rows if r["id"] == "1225426598"
            )["catalog_date"]
            == "2026-07-16",
        }
    else:
        text = texts["1220160067"]
        flags = {
            "eligible_capital": Decimal("104693400") - Decimal("527300") == Decimal("104166100"),
            "gross_repeated": "每10股派发现金红利1.500626元(含税)" in text,
            "aggregate_repeated": "共计派发现金15631435.79元" in text,
            "record_date_repeated": "股权登记日为:2024年5月30日" in text,
            "ex_date_repeated": "除权除息日为:2024年5月31日" in text,
            "reference_repeated": "收盘价-0.1493067元" in text,
            "buyback_cap_arithmetic": (Decimal("30.00") - Decimal("0.1493067")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            == Decimal("29.85"),
        }
    primary_records = [r for r in primary["records"] if r["symbol"] == symbol]
    require(bool(primary_records), "Missing primary checks")
    for row in primary_records:
        require(
            all(
                passed or (symbol, row["ex_date"], name) in closed
                for name, passed in row["checks"].items()
            ),
            "Primary failure not covered by closure",
        )
    failures = [name for name, passed in flags.items() if not passed]
    save(
        checks_dir / "issuer_checks" / f"{symbol}.json",
        {
            "status": "INDEPENDENT_ISSUER_CHECKS_PASS"
            if not failures
            else "INDEPENDENT_ISSUER_CHECKS_FAILED",
            "checked_at": now(),
            "script_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(protocol_file),
            "inventory_sha256": sha(inventory_file),
            "primary_checks_sha256": sha(primary_file),
            "primary_closure_sha256": sha(closure_file),
            "primary_events": [r["ex_date"] for r in primary_records],
            "received_documents": len(issuer_rows),
            "related_decisions": decisions,
            "checks": flags,
            "failures": failures,
            "related_visual_review": visual,
            "facts_accepted": 0,
            "runtime_inputs_changed": False,
            "archive_boundary": "Every received notice for this issuer in the bound inventory was read in full. No current-scope shareholder-term amendment was found beyond the reviewed primary schemes. Archive completeness and exact publication time remain uncertified.",
            "acceptance_boundary": "Issuer closure is not whole-batch source acceptance or economic integration. Preserve all source precision limits, source-year inconsistencies and initial failed checks.",
        },
        immutable=True,
    )
    print(symbol, "documents", len(issuer_rows), "new checks", len(flags), "failures", failures)
    require(not failures, f"Companion terms failed for {symbol}")
