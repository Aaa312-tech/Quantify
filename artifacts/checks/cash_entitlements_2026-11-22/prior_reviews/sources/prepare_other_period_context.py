"""Record the full-text context observations made for five issuers."""

from pathlib import Path

from quantfolio.storage import now, save, sha


def entry(source_id, pages, role, note, phrases=()):
    return {
        "source_id": source_id,
        "full_text_pages_read": pages,
        "role": role,
        "decision": note,
        "evidence_phrases": list(phrases),
    }


root = Path.cwd()
base = root / "artifacts/checks/cash_entitlements_2026-11-22"
issuers = [
    {
        "symbol": "sz000988",
        "documents": [
            entry(
                "1220244957",
                2,
                "DISTINCT_EVENT",
                "Distinct 2023 annual distribution, gross 1.50 per ten, record/ex/payment 2024-06-11/12/12. No amendment of the scoped 2024 annual scheme.",
                (
                    "2023年年度权益分派实施公告",
                    "每10股派1.50元人民币现金",
                    "股权登记日为:2024年6月11日",
                    "现金红利将于2024年6月12日",
                ),
            ),
            entry(
                "1223879401",
                3,
                "SCOPED_PRIMARY",
                "Scoped 2024 annual final scheme, ex/payment 2025-06-20, gross 2.019684 per ten and reference 0.1999999. Primary observation and independent checks remain separate.",
            ),
            entry(
                "1225352679",
                3,
                "DISTINCT_EVENT",
                "Distinct 2025 annual scheme, gross 2.500000 per ten, reference 0.2475634, record/ex/payment 2026-06-11/12/12. Same excluded capital does not make it a revision of the scoped event.",
                (
                    "2025年年度权益分派实施公告",
                    "每10股派2.500000元人民币现金",
                    "股权登记日为:2026年6月11日",
                    "现金红利将于2026年6月12日",
                ),
            ),
        ],
        "source_anomalies": [],
    },
    {
        "symbol": "sz300223",
        "documents": [
            entry(
                "1220122060",
                3,
                "DISTINCT_EVENT",
                "Distinct 2023 profit-period distribution paid in 2024; gross 2.00 per ten, record/ex/payment 2024-05-27/28/28. The stated payment year 2024 is retained separately from the 2023 profit period.",
                (
                    "2023年度分红派息实施公告",
                    "发放年度:2024年度",
                    "每10股派2.00元人民币现金",
                    "股权登记日为:2024年5月27日",
                    "现金红利将于2024年5月28日",
                ),
            ),
            entry(
                "1223887927",
                3,
                "SCOPED_PRIMARY",
                "Scoped 2024 profit-period distribution paid in 2025, gross 0.997988 per ten. The printed final aggregate 48156985.09 remains inconsistent with the stated eligible shares times gross. No received notice resolves it; no account rounding is inferred.",
            ),
            entry(
                "1225302163",
                3,
                "DISTINCT_EVENT",
                "Distinct 2025 profit-period distribution paid in 2026; gross 1.500000 per ten, record/ex/payment 2026-05-19/20/20. No correction of the scoped aggregate discrepancy.",
                (
                    "2025年度分红派息实施公告",
                    "发放年度:2026年度",
                    "每10股派1.500000元人民币现金",
                    "股权登记日为:2026年5月19日",
                    "现金红利将于2026年5月20日",
                ),
            ),
        ],
        "source_anomalies": [
            {
                "source_id": "1223887927",
                "classification": "SOURCE_AGGREGATE_CONFLICT_UNRESOLVED",
                "printed_aggregate": "48156985.09",
                "exact_share_gross_product": "48156985.1065324",
                "action": "Preserve initial failed check and arithmetic review; related notices provide no resolution.",
            }
        ],
    },
    {
        "symbol": "sz301052",
        "documents": [
            entry(
                "1219401936",
                4,
                "SCOPED_PRIMARY",
                "Scoped 2023 annual scheme, ex/payment and explicitly declared unrestricted capitalisation listing 2024-04-01. Gross 0.980816 and capitalisation 3.467532 per ten remain separate from proposal values; primary application-year anomaly is retained.",
            ),
            entry(
                "1223645164",
                3,
                "DISTINCT_EVENT",
                "Distinct 2024 annual cash-only scheme, gross 0.530000 per ten, record/ex/payment 2025-05-28/29/29. Its generic incentive-price adjustments do not amend the earlier capitalisation event.",
                (
                    "2024年年度权益分派实施公告",
                    "每10股派0.530000元人民币现金",
                    "股权登记日为:2025年5月28日",
                    "现金红利将于2025年5月29日",
                ),
            ),
            entry(
                "1225148375",
                3,
                "DISTINCT_EVENT",
                "Distinct 2025 annual cash-only scheme, gross 0.180000 per ten, record/ex/payment 2026-04-28/29/29. Preserve the source signature date 2026-04-23 separately from catalogue date 2026-04-22; not evidence of exact publication time.",
                (
                    "2025年年度权益分派实施公告",
                    "每10股派0.180000元人民币现金",
                    "股权登记日为2026年4月28日",
                    "现金红利将于2026年4月29日",
                    "2026年4月23日",
                ),
            ),
        ],
        "source_anomalies": [
            {
                "source_id": "1225148375",
                "classification": "CATALOGUE_PRECEDES_SIGNATURE",
                "catalog_date": "2026-04-22",
                "signature_date": "2026-04-23",
                "action": "Preserve both timestamps; no backdated source availability or scoped data change.",
            }
        ],
    },
    {
        "symbol": "sz002594",
        "documents": [
            entry(
                "1220689427",
                4,
                "SCOPED_PRIMARY",
                "Scoped 2023 annual A-share scheme, ex/payment 2024-07-29, final gross 30.977722 per ten. H-share procedures and approximate all-class aggregate remain distinct.",
            ),
            entry(
                "1224237000",
                5,
                "DISTINCT_EVENT",
                "Distinct 2024 annual A-share scheme: gross 39.74 per ten, bonus 8 plus capitalisation 12 per ten, record 2025-07-28 and explicit ex/payment/unrestricted listing 2025-07-29. Do not apply these later share changes to the scoped 2024 ex-event. H-share procedures are separate.",
                (
                    "2024年度利润分配及资本公积金转增股本实施公告",
                    "每10股派发现金红利人民币39.74元",
                    "每10股送红股8股",
                    "以资本公积金每10股转增12股",
                    "A股股权登记日为:2025年7月28日",
                    "A股新增可流通股份上市日:2025年7月29日",
                ),
            ),
            entry(
                "1225438683",
                4,
                "DISTINCT_EVENT",
                "Distinct 2025 annual A-share cash-only scheme, gross 3.58 per ten, record/ex/payment 2026-07-30/31/31. All-class aggregate is approximate thousand-yuan disclosure, not a scoped amendment or account precision instruction.",
                (
                    "2025年度利润分配实施公告",
                    "每10股派发现金红利人民币3.58元",
                    "A股股权登记日为:2026年7月30日",
                    "A股除权除息日为:2026年7月31日",
                ),
            ),
        ],
        "source_anomalies": [],
    },
    {
        "symbol": "sz300418",
        "documents": [
            entry(
                "1220416399",
                3,
                "SCOPED_PRIMARY",
                "The only received issuer notice is the scoped 2023 annual implementation, ex/payment 2024-06-28. Gross 0.493305 per ten, eligible capital and aggregate are independently checked. No additional received amendment exists in this bound archive; this does not prove archive completeness.",
            ),
        ],
        "source_anomalies": [],
    },
]
save(
    base / "other_period_context.json",
    {
        "status": "ENTERED_FULL_TEXT_CONTEXT_OBSERVATIONS",
        "recorded_at": now(),
        "protocol_sha256": sha(root / "configs/protocols/cash_entitlement_review.json"),
        "inventory_sha256": sha(base / "related_source_inventory.json"),
        "writer_sha256": sha(Path(__file__)),
        "issuers": issuers,
        "facts_accepted": 0,
        "runtime_inputs_changed": False,
    },
    immutable=True,
)
print("RECORDED", len(issuers), "issuer contexts")
