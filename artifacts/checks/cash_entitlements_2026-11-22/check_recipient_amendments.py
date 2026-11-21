"""Check recipient terms and amendment succession for three reviewed issuers."""

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


def copy_exact(source, destination):
    require(not destination.exists(), f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    require(sha(source) == sha(destination), "Copied evidence differs")


root = Path.cwd()
base = Path(__file__).resolve().parent
inventory_path = base / "related_source_inventory.json"
inventory = read(inventory_path)
protocol_path = root / "configs/protocols/cash_entitlement_review.json"
primary_path = base / "remaining_primary_checks.json"
closure_path = base / "remaining_primary_closure.json"
primary = read(primary_path)
closure = read(closure_path)
require(closure["initial_receipt"]["sha256"] == sha(primary_path), "Initial check changed")
closed_failures = {
    (row["symbol"], row["ex_date"], row["initial_failed_check"]) for row in closure["records"]
}

notes = {
    "sh600989": {
        "1220519887": "2024-038 adjusts only the 2023 annual aggregate after employee-plan transfer. Ordinary minority gross 0.3158 and controlling-holder gross 0.2650 remain. Three cent-scale source discrepancies are retained separately; no account rounding is inferred.",
        "1220664181": "Scoped 2023 annual final implementation: ordinary minority gross 0.3158, controlling gross 0.2650, reference 0.28, record/ex/payment 2024-07-23/24/24. Final terms agree with the preceding adjustment's recipient amounts.",
        "1223476469": "Scoped 2024 annual final implementation: ordinary minority gross 0.4598, controlling gross 0.3891 and reference 0.41; record/ex/payment 2025-05-12/13/13. A different recipient scheme, not a revision of the 2023 dividend.",
        "1224631685": "Distinct 2025 interim distribution: uniform gross 0.28, record/ex/payment 2025-09-05/08/08. Excluded repurchase shares do not establish different minority/control recipient rates. No scoped amendment.",
        "1225136155": "Distinct 2025 final distribution, ex/payment 2026-04-28: minority gross 0.4921, control gross 0.3906 and reference 0.42. Preserve its existing accepted per-share facts; the printed subtotals and aggregate differ by one cent, recorded without correcting either source number.",
        "1225506148": "Distinct 2026 interim distribution: uniform gross 0.42, record/ex/payment 2026-08-31/09-01/09-01. No amendment of either scoped event.",
    },
    "sh688025": {
        "1220090735": "2024-034 adjusts the 2023 annual proposal from 0.35 to 0.35057 per share, reflecting incentive issuance and additional repurchases. Eligible capital 94611453, final aggregate 33167937.08; implementation dates remain for the later notice.",
        "1220273317": "Scoped final 2023 annual implementation repeats gross 0.35057 and aggregate 33167937.08, with reference 0.34895 and record/ex/payment 2024-06-12/13/13. The proposal amount 0.35 is superseded.",
        "1220377013": "2024-045 repeats final recipient gross, eligible capital, aggregate and reference, adjusting only the buyback cap from 100 to 99.65. Published 2024-06-18 after the 2024-06-13 ex-date; retrospective corroboration only.",
        "1223888804": "Distinct 2024 annual implementation, record/ex/payment 2025-06-24/25/25, gross 0.42. All five text pages read; no amendment of the scoped 2023 annual event.",
        "1225360657": "Distinct 2025 annual implementation, record/ex/payment 2026-06-15/16/16, gross 0.89. All five text pages read; no amendment of the scoped event.",
    },
    "sh688627": {
        "1220290237": "2024-023 changes 2023 annual cash from 0.372 to 0.37345 per share after repurchases; eligible capital 93646846, aggregate 34972414.64. Both physical pages carry printed page counter 2; physical order is preserved.",
        "1220443864": "Scoped 2023 annual final implementation repeats 0.37345, aggregate 34972414.64 and reference 0.37200; record/ex/payment 2024-06-28/07-01/07-01.",
        "1220513214": "2024-025 adjusts only the buyback cap from 83.57 to 83.20 and repeats final gross/reference and record/ex dates. Published 2024-07-02 after ex-date; retrospective corroboration only.",
        "1223900960": "2025-053 is an intermediate 2024 annual adjustment: 0.323 to 0.32351, eligible capital 92375277 and aggregate 29884325.86. It is superseded by 2025-055 before implementation.",
        "1224017643": "2025-055 supersedes the intermediate gross: 1278000 employee-plan shares become eligible, exclusion falls from 1636477 to 358477, eligible capital reaches 93653277 and gross becomes 0.31910; aggregate 29884760.69.",
        "1224071706": "2025-059 repeats final gross 0.31910, aggregate 29884760.69 and reference 0.31788; only the buyback cap changes from 120.34 to 120.02. Preserve the page-1 2024-07-10 versus page-2 2025-07-10 year conflict; do not source an event date from this inconsistent companion.",
        "1224071707": "Scoped final 2024 annual implementation confirms 0.31910 and reference 0.31788, with record/ex/payment 2025-07-09/10/10. It agrees with the final adjustment rather than the intermediate 0.32351.",
        "1225329934": "Distinct 2025 annual proposal adjustment: incentive shares increase capital by 116400; gross remains 0.224 and proposed aggregate becomes 21037335.65. No amendment of the two scoped prior-year events.",
        "1225363091": "Distinct 2025 annual final implementation repeats the later capital adjustment and gross 0.224; reference 0.223 and record/ex/payment 2026-06-16/17/17. All five text pages read; no scoped amendment.",
    },
}

page_notes = {
    "1220377013": {
        1: "688025 / 2024-045 changes buyback cap 100 to 99.65 effective 2024-06-13.",
        2: "Proposal 0.35 is distinct from final 0.35057, eligible 94611453 and aggregate 33167937.08; the adjustment and implementation notices are explicitly cited.",
        3: "Gross 0.35057 is distinct from virtual/reference 0.34895; zero share change, cap 99.65 and signature 2024-06-18. No account settlement rounding is specified.",
    },
    "1220513214": {
        1: "688627 / 2024-025 changes buyback cap 83.57 to 83.20 effective 2024-07-01.",
        2: "Proposal 0.372 is superseded by gross 0.37345. Record/ex 2024-06-28/07-01 repeat with citations to the final implementation.",
        3: "Total 94011754 minus excluded 364908 gives 93646846. Gross 0.37345, reference 0.37200 and cap 83.20 remain different roles; signature 2024-07-02.",
    },
    "1224071706": {
        1: "688627 / 2025-059 changes cap 120.34 to 120.02. The printed effective year is 2024, despite the 2025 heading and transaction history; preserve this source conflict.",
        2: "Final gross 0.31910, aggregate 29884760.69, record/ex 2025-07-09/10, eligible 93653277 and reference 0.31788 repeat. The ex-year conflicts with page 1.",
        3: "Issuer signature is 2025-07-04. The entire tail page contains no correction resolving the page-1 year.",
    },
    "1225136155": {
        2: "Distinct 2026 ex-event: minority/control subtotals 1036635877.89 and 2017922122.06 sum to 3054557999.95, while printed aggregate is 3054557999.96. Gross 0.4921/0.3906 and reference 0.42 have separate roles; no old field is changed.",
    },
}

prior_dir = root / ".git/distribution_frontier_2026_11_22"
imports, imported_pages = [], []
for name in (
    "related_visual_observations.json",
    "shareholder_related_observations.json",
    "related_rendering.json",
    "shareholder_related_rendering.json",
):
    source_path = prior_dir / name
    destination = base / "prior_reviews" / name
    copy_exact(source_path, destination)
    imports.append(
        {
            "original_path": source_path.relative_to(root).as_posix(),
            "path": destination.relative_to(root).as_posix(),
            "sha256": sha(destination),
        }
    )
    if "observations" not in name:
        continue
    old = read(source_path)
    for record in old["records"]:
        require(record["whole_page_visually_inspected"] is True, "Old visual review missing")
        require(sha(root / record["image"]) == record["image_sha256"], "Old image changed")
        require(
            sha(root / record["source"]["path"]) == record["source"]["sha256"], "Old source changed"
        )
        image_path = base / "related_pages" / record["id"] / f"page_{record['page']:02d}.png"
        copy_exact(root / record["image"], image_path)
        imported_pages.append(
            {
                **record,
                "source_id": record["id"],
                "original_image": record["image"],
                "image": image_path.relative_to(root).as_posix(),
                "original_review": imports[-1],
                "original_reviewed_at": old["reviewed_at"],
            }
        )
import_path = base / "prior_reviews" / "import.json"
save(
    import_path,
    {
        "status": "EXACT_PRIOR_VISUAL_EVIDENCE_COPIED",
        "copied_at": now(),
        "script_sha256": sha(Path(__file__)),
        "files": imports,
        "pages": imported_pages,
        "new_visual_inspections": 0,
        "facts_accepted": 0,
    },
    immutable=True,
)

for symbol, decisions in notes.items():
    rows = [row for row in inventory["records"] if symbol in row["symbols"]]
    require({row["id"] for row in rows} == set(decisions), "Received inventory changed")
    dates = {row["id"]: row["catalog_date"] for row in rows}
    texts, pages_by_id, recorded, visual = {}, {}, [], []
    for row in rows:
        for key in ("source", "receipt"):
            require(sha(root / row[key]["path"]) == row[key]["sha256"], "Bound source changed")
        with fitz.open(root / row["source"]["path"]) as document:
            pages = [page.get_text(sort=True) for page in document]
        require(pages == row["independent_pages"], "Independent reader differs from inventory")
        pages_by_id[row["id"]] = [compact(page) for page in pages]
        texts[row["id"]] = compact("\n".join(pages))
        recorded.append(
            {
                "source_id": row["id"],
                "source": row["source"],
                "receipt": row["receipt"],
                "catalog_date": row["catalog_date"],
                "title": row["title"],
                "complete_text_pages_actually_read": len(pages),
                "independent_reader_matches": True,
                "decision": decisions[row["id"]],
            }
        )
        visual.extend(page for page in imported_pages if page["source_id"] == row["id"])
        if row["id"] in page_notes:
            render_path = base / "renderings" / f"{row['id']}.json"
            rendering = read(render_path)
            for page in rendering["pages"]:
                if page["page"] not in page_notes[row["id"]]:
                    continue
                require(sha(root / page["image"]) == page["sha256"], "New image changed")
                visual.append(
                    {
                        "source_id": row["id"],
                        "page": page["page"],
                        "image": page["image"],
                        "image_sha256": page["sha256"],
                        "whole_page_visually_inspected": True,
                        "observation": page_notes[row["id"]][page["page"]],
                        "rendering_sha256": sha(render_path),
                    }
                )

    anomalies = []
    if symbol == "sh600989":
        text = texts["1220519887"]
        old = read(prior_dir / "shareholder_related_observations.json")
        discrepancies = {
            "stated_increment_minus_printed_minority_total_change": str(
                Decimal("6171839.20") - (Decimal("686198642.10") - Decimal("680026802.91"))
            ),
            "stated_increment_minus_rounded_incremental_share_cash": str(
                Decimal("6171839.20")
                - (Decimal("19543506") * Decimal("0.3158")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            ),
            "printed_old_subtotals_minus_stated_old_aggregate": str(
                Decimal("680026802.91") + Decimal("1367524566.70") - Decimal("2047551369.60")
            ),
        }
        flags = {
            "minority_and_control_gross_repeated": "0.3158元(含税)" in text
            and "0.2650元(含税)" in text,
            "employee_transfer_arithmetic": 2153346431 + 19543506 == 2172889937,
            "participant_total_arithmetic": 7313816494 + 19543506 == 7333360000,
            "recipient_capital_partition": 2172889937 + 5160470063 == 7333360000,
            "final_aggregate_sum": Decimal("686198642.10") + Decimal("1367524566.70")
            == Decimal("2053723208.80"),
            "source_numbers_present": all(
                value in text
                for value in (
                    "19543506",
                    "6171839.20",
                    "680026802.91",
                    "686198642.10",
                    "1367524566.70",
                    "2047551369.60",
                    "2053723208.80",
                )
            ),
            "three_old_discrepancies_preserved": discrepancies
            == old["source_arithmetic_discrepancies"],
            "adjustment_precedes_final_notice": dates["1220519887"] < dates["1220664181"],
            "distinct_2026_aggregate_conflict_present": all(
                value in texts["1225136155"]
                for value in ("1036635877.89", "2017922122.06", "3054557999.96")
            ),
        }
        anomalies = [
            {
                "source_id": "1220519887",
                "classification": "SOURCE_AGGREGATE_CONFLICTS_UNRESOLVED",
                "differences_yuan": discrepancies,
                "action": "Preserve printed amounts; no inferred account rounding.",
            },
            {
                "source_id": "1225136155",
                "classification": "UNSCOPED_SOURCE_AGGREGATE_CONFLICT",
                "printed_subtotal_sum": str(Decimal("1036635877.89") + Decimal("2017922122.06")),
                "printed_aggregate": "3054557999.96",
                "printed_aggregate_minus_subtotal_sum": "0.01",
                "action": "Retain prior per-share decisions and all unscoped source fields unchanged.",
            },
        ]
    elif symbol == "sh688025":
        adjustment, cap = texts["1220090735"], texts["1220377013"]
        flags = {
            "issuance_arithmetic": 94990945 + 58478 == 95049423,
            "eligible_capital_arithmetic": 95049423 - 437970 == 94611453,
            "adjustment_numbers_present": all(
                value in adjustment
                for value in (
                    "58478",
                    "95049423",
                    "437970",
                    "94611453",
                    "33168128.35",
                    "0.35057",
                    "33167937.08",
                )
            ),
            "proposal_to_final_gross": "0.35元(含税)调整为0.35057元(含税)" in adjustment,
            "gross_ratio_at_printed_precision": (
                Decimal("33168128.35") / Decimal("94611453")
            ).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
            == Decimal("0.35057"),
            "final_aggregate_at_printed_precision": (
                Decimal("94611453") * Decimal("0.35057")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            == Decimal("33167937.08"),
            "cap_repeats_final_gross_and_reference": all(
                value in cap for value in ("0.35057", "0.34895", "33167937.08", "94611453")
            ),
            "reference_at_printed_precision": (
                Decimal("94611453") * Decimal("0.35057") / Decimal("95049423")
            ).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
            == Decimal("0.34895"),
            "cap_arithmetic": (Decimal("100") - Decimal("0.34895")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            == Decimal("99.65"),
            "amendment_succession": dates["1220090735"]
            < dates["1220273317"]
            < "2024-06-13"
            < dates["1220377013"],
        }
    else:
        first, intermediate, final = (
            texts[key] for key in ("1220290237", "1223900960", "1224017643")
        )
        cap = texts["1224071706"]
        flags = {
            "2023_eligible_arithmetic": 94011754 - 364908 == 93646846,
            "2023_gross_ratio": (Decimal("34972372.49") / Decimal("93646846")).quantize(
                Decimal("0.00001"), rounding=ROUND_HALF_UP
            )
            == Decimal("0.37345"),
            "2023_aggregate": (Decimal("93646846") * Decimal("0.37345")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            == Decimal("34972414.64"),
            "2023_numbers_present": all(
                value in first
                for value in ("364908", "93646846", "34972372.49", "0.37345", "34972414.64")
            ),
            "intermediate_eligible_arithmetic": 94011754 - 1636477 == 92375277,
            "intermediate_gross_ratio": (Decimal("29884722.28") / Decimal("92375277")).quantize(
                Decimal("0.00001"), rounding=ROUND_HALF_UP
            )
            == Decimal("0.32351"),
            "intermediate_aggregate": (Decimal("92375277") * Decimal("0.32351")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            == Decimal("29884325.86"),
            "intermediate_numbers_present": all(
                value in intermediate
                for value in ("1636477", "92375277", "29884722.28", "0.32351", "29884325.86")
            ),
            "employee_transfer_arithmetic": 1636477 - 1278000 == 358477,
            "final_eligible_arithmetic": 94011754 - 358477 == 93653277,
            "final_gross_ratio": (Decimal("29884722.28") / Decimal("93653277")).quantize(
                Decimal("0.00001"), rounding=ROUND_HALF_UP
            )
            == Decimal("0.31910"),
            "final_aggregate": (Decimal("93653277") * Decimal("0.31910")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            == Decimal("29884760.69"),
            "final_numbers_present": all(
                value in final
                for value in (
                    "1278000",
                    "358477",
                    "93653277",
                    "29884722.28",
                    "0.31910",
                    "29884760.69",
                )
            ),
            "cap_repeats_final_terms": all(
                value in cap for value in ("0.31910", "29884760.69", "0.31788", "93653277")
            ),
            "cap_arithmetic": (Decimal("120.34") - Decimal("0.31788")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            == Decimal("120.02"),
            "cap_year_conflict_preserved": "调整起始日:2024年7月10日"
            in pages_by_id["1224071706"][0]
            and "除权除息日为2025年7月10日" in pages_by_id["1224071706"][1],
            "intermediate_is_superseded": dates["1223900960"]
            < dates["1224017643"]
            < dates["1224071707"]
            < "2025-07-10",
            "later_scheme_distinct": "2025年度利润分配" in texts["1225329934"]
            and "0.224元" in texts["1225363091"]
            and "2026/6/17" in texts["1225363091"],
        }
        anomalies = [
            {
                "source_id": "1224071706",
                "classification": "COMPANION_EFFECTIVE_YEAR_CONFLICT",
                "physical_page_1": "2024-07-10",
                "physical_page_2": "2025-07-10",
                "signature": "2025-07-04",
                "primary_implementation": "1224071707",
                "action": "Do not accept dates from this companion; retain its consistent financial corroboration separately.",
            }
        ]

    scoped = [row for row in primary["records"] if row["symbol"] == symbol]
    require(bool(scoped), "Primary review missing")
    for row in scoped:
        require(
            all(
                passed or (symbol, row["ex_date"], name) in closed_failures
                for name, passed in row["checks"].items()
            ),
            "Unclosed primary checker failure",
        )
    failures = [name for name, passed in flags.items() if not passed]
    save(
        base / "issuer_checks" / f"{symbol}.json",
        {
            "status": "INDEPENDENT_ISSUER_CHECKS_PASS"
            if not failures
            else "INDEPENDENT_ISSUER_CHECKS_FAILED",
            "checked_at": now(),
            "script_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(protocol_path),
            "inventory_sha256": sha(inventory_path),
            "primary_checks_sha256": sha(primary_path),
            "primary_closure_sha256": sha(closure_path),
            "prior_visual_import_sha256": sha(import_path),
            "primary_events": [row["ex_date"] for row in scoped],
            "received_documents": len(rows),
            "related_decisions": recorded,
            "checks": flags,
            "failures": failures,
            "source_anomalies": anomalies,
            "related_visual_review": visual,
            "facts_accepted": 0,
            "runtime_inputs_changed": False,
            "archive_boundary": "Every received notice for this issuer in the bound inventory was read in full. Archive completeness and exact historical publication times remain uncertified.",
            "acceptance_boundary": "Issuer amendment relationships are reviewed, not batch-accepted or integrated. Preserve the explicit source conflicts, per-share/reference distinctions and unscoped decisions. Display or cap rounding does not certify account cash settlement rounding.",
        },
        immutable=True,
    )
    print(
        symbol,
        "documents",
        len(rows),
        "checks",
        len(flags),
        "failures",
        failures,
        "source anomaly records",
        len(anomalies),
        "visual pages",
        len(visual),
    )
    require(not failures, f"Issuer checks failed: {symbol}")
