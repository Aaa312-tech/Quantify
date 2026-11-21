"""Record reviewed bond companions and distinct-period distribution context."""

from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = root / "artifacts/checks/cash_entitlements_2026-11-22"
inventory = read(base / "related_source_inventory.json")
observations = {
    read(path)["source_id"]: read(path) for path in (base / "observations").glob("*.json")
}
notes = {
    "1219793060": (
        "BOND_CONVERSION_COMPANION",
        "2024-029 suspends bond conversion from 2024-04-26 through the equity record day; the bond continues trading. Its generic conversion-price formulas do not declare new equity payment/listing dates or account rounding. No shareholder-term amendment.",
        ["债券代码:127056", "暂停转股时间:2024年4月26日起", "公司可转换公司债券正常交易"],
    ),
    "1223215712": (
        "BOND_CONVERSION_COMPANION",
        "2025-038 suspends bond conversion from 2025-04-24, repeats the 2024 annual fixed distribution of 25.6亿元 and the ratio-adjustment principle. Bond price 22.94 and generic cent rounding are not shareholder cash terms. No equity suspension is inferred.",
        [
            "债券代码:127056",
            "暂停转股时间:2025年4月24日起",
            "派发现金股利25.6亿元(含税)",
            "公司可转换公司债券正常交易",
        ],
    ),
    "1224688568": (
        "DISTINCT_EVENT",
        "Distinct 2025 interim scheme, gross 2.00 per ten, record/ex/payment 2025-10-14/15/15. It keeps the per-share ratio fixed, unlike the three scoped fixed-total schemes. Referenced bond conversion-price companion was not received in this inventory.",
        [
            "2025年半年度权益分派实施公告",
            "每10股派2.00元人民币现金",
            "股权登记日为:2025年10月14日",
            "除权除息日为:2025年10月15日",
        ],
    ),
    "1225291706": (
        "BOND_CONVERSION_COMPANION",
        "2026-043 suspends bond conversion from 2026-05-13, repeats the scoped 2025 second-half fixed amount of 22.5亿元. Bond price 22.23 and generic adjustment formulas are separate from shareholder entitlement and settlement rounding; no new equity date is accepted.",
        [
            "债券代码:127056",
            "暂停转股时间:2026年5月13日起",
            "派发现金股利22.5亿元(含税)",
            "公司可转换公司债券正常交易",
        ],
    ),
    "1225555340": (
        "DISTINCT_EVENT",
        "Distinct 2026 interim scheme: gross 2.00 per ten, record/ex/payment 2026-09-15/16/16. Its fixed-ratio capital adjustment is not a change to a scoped prior distribution; referenced bond-price notice is outside the received inventory.",
        [
            "2026年半年度权益分派实施公告",
            "每10股派2.00元人民币现金",
            "股权登记日为:2026年9月15日",
            "除权除息日为:2026年9月16日",
        ],
    ),
    "1220043072": (
        "DISTINCT_EVENT",
        "Distinct 2023 annual cash distribution: gross 5.000000 per ten, record/ex/payment 2024-05-20/21/21. The primary reference is 0.4987882; retain the conflicting later buyback-cap reference separately. Neither is a revision of the scoped 2025 annual distribution.",
        [
            "2023年年度权益分派实施公告",
            "每10股派5.000000元人民币现金",
            "股权登记日为:2024年5月20日",
            "收盘价-0.4987882元",
        ],
    ),
    "1221508399": (
        "DISTINCT_EVENT_BUYBACK_CAP",
        "2024-140 refers to the already completed 2023 annual distribution, repeats gross 5.000000 per ten and ex-date 2024-05-21, and changes only the buyback cap 58.00 to 57.50. It prints 0.4987788, different from the primary reference 0.4987882. Preserve both and do not infer an earlier cash amendment from this later notice.",
        [
            "2023年年度权益分派",
            "每10股派5.000000元人民币现金",
            "除权除息日为2024年5月21日",
            "58.00-0.4987788=57.50",
        ],
    ),
    "1223527100": (
        "DISTINCT_EVENT",
        "Distinct 2024 annual distribution: gross 5.000000 per ten, record/ex/payment 2025-05-16/19/19 and reference 0.4983203. No amendment to the scoped 2025 annual adjusted cash.",
        [
            "2024年年度权益分派实施公告",
            "每10股派5.000000元人民币现金",
            "股权登记日为:2025年5月16日",
            "除权除息日为:2025年5月19日",
        ],
    ),
    "1224646786": (
        "DISTINCT_EVENT",
        "Distinct 2025 interim distribution: gross 2.450000 per ten, record/ex/payment 2025-09-16/17/17, reference 0.2441769. The retained template instruction in its tax section is not evidence about investor eligibility or a scoped amendment.",
        [
            "2025年半年度权益分派实施公告",
            "每10股派2.450000元人民币现金",
            "股权登记日为:2025年9月16日",
            "除权除息日为:2025年9月17日",
        ],
    ),
    "1225560567": (
        "DISTINCT_EVENT",
        "Distinct 2026 interim cash-only distribution: gross 2.300000 per ten, record/ex/payment 2026-09-16/17/17 and reference 0.2300000. No scoped amendment.",
        [
            "2026年半年度权益分派实施公告",
            "每10股派2.300000元人民币现金",
            "股权登记日为:2026年9月16日",
            "除权除息日为:2026年9月17日",
        ],
    ),
    "1220108763": (
        "DISTINCT_EVENT_BOND_CONVERSION",
        "2024-059 concerns conversion suspension for the distinct 2023 annual distribution. The bond remains tradable; its two-place conversion-price rounding is not account cash precision, an equity suspension or a listing fact.",
        ["债券代码:123132", "2024年5月21日", "回盛转债”正常交易"],
    ),
    "1220132945": (
        "DISTINCT_EVENT",
        "Distinct 2023 annual distribution: gross 2.700000 per ten, record/ex/payment 2024-05-28/29/29, reference 0.2687734. It identifies bond-price change 27.71 to 27.44, separate from shareholder cash and the scoped 2024 annual distribution.",
        [
            "2023年年度权益分派实施公告",
            "每10股派2.700000元人民币现金",
            "股权登记日为:2024年5月28日",
            "除权除息日为:2024年5月29日",
        ],
    ),
    "1224502066": (
        "DISTINCT_EVENT",
        "Distinct 2025 interim distribution: gross 1.000000 per ten, record/ex/payment 2025-08-25/26/26, reference 0.0996275. Same excluded shares do not make it a scoped adjustment.",
        [
            "2025年半年度权益分派实施公告",
            "每10股派1.000000元人民币现金",
            "股权登记日为:2025年8月25日",
            "除权除息日为:2025年8月26日",
        ],
    ),
    "1225392451": (
        "DISTINCT_EVENT",
        "Distinct 2025 annual distribution: gross 2.000000 per ten, record/ex/payment 2026-07-03/06/06, reference 0.1986684. Its separate 2026-056 buyback-cap notice is referenced but not received in the bound inventory; no scoped amendment.",
        [
            "2025年年度权益分派实施公告",
            "每10股派2.000000元人民币现金",
            "股权登记日为:2026年7月3日",
            "除权除息日为:2026年7月6日",
        ],
    ),
    "1225536385": (
        "DISTINCT_EVENT",
        "Distinct 2026 interim distribution: gross 3.000000 per ten, record/ex/payment 2026-09-07/08/08, reference 0.2979618. Its 2026-073 cap notice is referenced but not received; no scoped amendment.",
        [
            "2026年半年度权益分派实施公告",
            "每10股派3.000000元人民币现金",
            "股权登记日为:2026年9月7日",
            "除权除息日为:2026年9月8日",
        ],
    ),
}
visual = []
for source_id, printed in (("1220043072", "0.4987882"), ("1221508399", "0.4987788")):
    rendering_path = base / "renderings" / f"{source_id}.json"
    page = next(item for item in read(rendering_path)["pages"] if item["page"] == 2)
    if sha(root / page["image"]) != page["sha256"]:
        raise ValueError("Inspected image changed")
    visual.append(
        {
            "source_id": source_id,
            "page": 2,
            "image": page["image"],
            "image_sha256": page["sha256"],
            "whole_page_visually_inspected": True,
            "rendering_sha256": sha(rendering_path),
            "observation": f"The whole page explicitly prints {printed}; original source retained without correction.",
        }
    )
issuers = []
for symbol in ("sz000708", "sz300014", "sz300871"):
    documents = []
    for row in inventory["records"]:
        if symbol not in row["symbols"]:
            continue
        if row["id"] in observations:
            observation = observations[row["id"]]
            role, note, phrases = "SCOPED_PRIMARY", observation["interpretation"], []
        else:
            role, note, phrases = notes[row["id"]]
        documents.append(
            {
                "source_id": row["id"],
                "full_text_pages_read": len(row["pages"]),
                "role": role,
                "decision": note,
                "evidence_phrases": phrases,
            }
        )
    anomalies = []
    if symbol == "sz300014":
        anomalies.append(
            {
                "classification": "UNSCOPED_PRIMARY_COMPANION_REFERENCE_CONFLICT",
                "primary_source_id": "1220043072",
                "primary_reference": "0.4987882",
                "companion_source_id": "1221508399",
                "companion_reference": "0.4987788",
                "action": "Preserve both source values and all unscoped decisions; neither changes the current scoped event.",
                "visual_review": visual,
            }
        )
    issuers.append({"symbol": symbol, "documents": documents, "source_anomalies": anomalies})
save(
    base / "bond_companion_context.json",
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
