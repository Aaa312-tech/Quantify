"""Record the final four received issuer inventories without accepting fields."""

from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = root / "artifacts/checks/cash_entitlements_2026-11-22"
inventory = read(base / "related_source_inventory.json")
observations = {
    read(path)["source_id"]: read(path) for path in (base / "observations").glob("*.json")
}
notes = {
    "1220881700": (
        "DISTINCT_EVENT",
        "2023 annual distribution, distinct from scoped 2025 annual terms: cancellation of 21,266 shares changes 54.00 to 54.000780 per ten while the proposed total stays 7,948,733,952.60. Record/ex/payment 2024-08-22/23/23. No scoped amendment.",
        ["2023年度分红派息实施公告", "54.000780", "2024年8月22日", "2024年8月23日"],
    ),
    "1222354531": (
        "DISTINCT_EVENT",
        "2024 interim cash distribution, 13.58 per ten on 1,471,951,503 shares, record/ex/payment 2025-01-23/24/24. No bonus or capitalisation and no scoped amendment.",
        ["2024年中期分红派息实施公告", "13.58", "2025年1月23日", "2025年1月24日"],
    ),
    "1224368537": (
        "DISTINCT_EVENT",
        "2024 annual cash distribution, 45.92 per ten on 1,471,951,503 shares, record/ex/payment 2025-08-07/08/08. Separate distribution period, with no scoped amendment.",
        ["2024年度分红派息实施公告", "45.92", "2025年8月7日", "2025年8月8日"],
    ),
    "1224947188": (
        "DISTINCT_EVENT",
        "2025 interim cash distribution, 13.58 per ten on 1,471,941,963 shares, record/ex/payment 2026-01-29/30/30. It is not the scoped annual distribution despite the shared profit year.",
        ["2025年中期分红派息实施公告", "13.58", "2026年1月29日", "2026年1月30日"],
    ),
    "1222366609": (
        "DISTINCT_EVENT",
        "2024 special distribution: gross 12.30 per ten, eligible 4,387,474,934, reference 1.2255331 per share. Record/ex/payment 2025-01-23/24/24. Option exercise suspension does not establish equity suspension; no revision of scoped 2023 annual cash.",
        ["2024年特别分红权益分派实施公告", "12.30", "1.2255331", "2025年1月24日"],
    ),
    "1223099780": (
        "DISTINCT_EVENT",
        "2024 annual distribution: gross 45.53 per ten, eligible 4,387,403,387 and reference 4.5364651. Record/ex/payment 2025-04-21/22/22. Buyback cap 392.32 to 387.78 and gross-per-ten precision are separate from account settlement. No scoped amendment.",
        ["2024年年度权益分派实施公告", "45.53", "4.5364651", "2025年4月22日"],
    ),
    "1224474223": (
        "DISTINCT_EVENT",
        "2025 interim A-share distribution: gross 10.07 per ten, eligible A shares 4,380,762,521, A reference 1.0018242; record/ex/payment 2025-08-19/20/20. Separate H capital is not the A-reference denominator. No scoped amendment.",
        ["2025年中期分红A股实施公告", "10.07", "1.0018242", "2025年8月20日"],
    ),
    "1225107879": (
        "DISTINCT_EVENT",
        "2025 annual A-share distribution: gross 69.57 per ten, eligible A shares 4,376,182,522 and A reference 6.9065252; record/ex/payment 2026-04-21/22/22. A/H and actual entitlement/reference remain distinct. No scoped amendment.",
        ["2025年度A股权益分派实施公告", "69.57", "6.9065252", "2026年4月22日"],
    ),
    "1225456175": (
        "DISTINCT_EVENT",
        "2026 interim A-share distribution: gross 14.11 per ten, eligible A shares 4,380,035,575 and A reference 1.4019358; record/ex/payment 2026-08-07/10/10. Signature 2026-08-03 and catalogue 2026-08-04 are retained as different date roles, neither an exact publication timestamp. No scoped amendment.",
        ["2026年中期分红A股实施公告", "14.11", "1.4019358", "2026年8月10日"],
    ),
    "1222320497": (
        "DISTINCT_EVENT",
        "2024 first-three-quarter distribution, gross 2.000000 per ten on 179,989,761 shares; record/ex/payment 2025-01-20/21/21. Signature 2025-01-13 differs from catalogue 2025-01-14; no exact availability time or scoped amendment inferred.",
        ["2024年前三季度权益分派实施公告", "2.000000", "2025年1月20日", "2025年1月21日"],
    ),
    "1223526308": (
        "DISTINCT_EVENT",
        "2024 annual distribution, gross 4.000000 per ten on 179,989,761 shares; record/ex/payment 2025-05-19/20/20. Retain the source's guarantee exception naming Wang Jiankun without inference. Signature 2025-05-12 and catalogue 2025-05-13 remain distinct; no scoped amendment.",
        ["2024年度利润分配实施公告", "4.000000", "2025年5月19日", "2025年5月20日"],
    ),
    "1224792763": (
        "DISTINCT_EVENT",
        "2025 first-three-quarter distribution, gross 1.500000 per ten on 188,891,422 shares; record/ex/payment 2025-11-14/17/17. Separate from the scoped 2025 annual distribution, with no amendment.",
        ["2025年前三季度利润分配实施公告", "1.500000", "2025年11月14日", "2025年11月17日"],
    ),
    "1225569420": (
        "DISTINCT_EVENT",
        "2026 interim distribution under issuer name 福建万辰食品集团股份有限公司 and unchanged code 300972. Incentive shares raise capital from 197,333,568 to 197,401,782; gross stays 1.500000 per ten. Record/ex/payment 2026-09-23/24/24; incentive listing 2026-09-15 is not a distribution listing date. No scoped amendment.",
        ["2026年半年度利润分配实施公告", "1.500000", "2026年9月23日", "2026年9月24日"],
    ),
    "1225658972": (
        "DISTINCT_EVENT",
        "2026 first-three-quarter cash distribution, gross 3.00 per ten on 198,518,799 shares; record/ex/payment 2026-11-12/13/13. Signature 2026-11-05 and catalogue 2026-11-06 are distinct roles. No scoped amendment.",
        ["2026年前三季度利润分配实施公告", "3.00", "2026年11月12日", "2026年11月13日"],
    ),
    "1223211361": (
        "DISTINCT_EVENT",
        "2024 annual distribution, gross 18.950000 per ten on 103,186,691 eligible shares, total 195,538,779.45 and reference 1.8681454. Record/ex/payment 2025-04-28/29/29. Its unchanged-capital statement does not amend the scoped 2023 annual adjusted cash.",
        ["2024年度权益分派实施公告", "18.950000", "1.8681454", "2025年4月29日"],
    ),
    "1225324197": (
        "DISTINCT_EVENT",
        "2025 annual cash/capitalisation distribution: gross 14.500000 per ten and four new shares per ten for eligible holders; reference cash 1.4294516 and full-capital reference ratio 0.3943314 remain distinct. Record 2026-05-27; ex/payment/unrestricted listing 2026-05-28. Fractional allocation wording belongs to this unscoped event, not the scoped cash-only 2023 annual distribution. Preserve existing unscoped facts.",
        ["2025年度权益分派实施公告", "14.500000", "1.4294516", "0.3943314", "2026年5月28日"],
    ),
    "1224938462": (
        "UNRELATED_EQUITY_STATUS",
        "2026-001 concerns an acquisition, with equity suspension from 2026-01-19 opening and conditional latest resumption 2026-02-02. Signature 2026-01-17, catalogue 2026-01-19. Its purchase cash is not shareholder dividend entitlement; no scoped financial amendment. Historical status acceptance requires its own review and is not performed here.",
        ["公告编号:2026-001", "2026年1月19日", "2026年2月2日", "2026年1月17日"],
    ),
    "1224947506": (
        "UNRELATED_EQUITY_STATUS_PROGRESS",
        "2026-002 continues the acquisition-related suspension begun 2026-01-19, with no fixed resumption declaration in this notice. It does not amend any scoped dividend field or establish an exact historical publication time.",
        ["公告编号:2026-002", "2026年1月19日", "公司股票继续停牌", "2026年1月24日"],
    ),
    "1224959739": (
        "UNRELATED_EQUITY_RESUMPTION",
        "2026-006 declares equity resumption at 2026-02-02 opening after acquisition discussions and 2026-01-30 board approval. Acquisition structure/related-party descriptions have changed from the initial proposal; those are not dividend amendments. No historical runtime status is applied in this source-only phase.",
        ["公告编号:2026-006", "2026年2月2日", "上午开市起复牌", "2026年1月31日"],
    ),
    "1224959743": (
        "UNRELATED_SHAREHOLDER_INVENTORY",
        "2026-004 lists top-ten holders at 2026-01-16 before the acquisition suspension. Percentages retain repurchase shares in their denominators; the 1,483,309-share repurchase account is neither a scoped 2024 dividend record-date balance nor a cash entitlement. No scoped amendment.",
        [
            "公告编号:2026-004",
            "2026年1月16日",
            "1,483,309.00",
            "未剔除公司回购专用账户中股份数量的影响",
        ],
    ),
}
issuers = []
for symbol in ("sz000568", "sz300750", "sz300972", "sz301362"):
    documents = []
    for row in inventory["records"]:
        if symbol not in row["symbols"]:
            continue
        if row["id"] in observations:
            role = "SCOPED_PRIMARY"
            note = observations[row["id"]]["interpretation"]
            phrases = []
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
    issuers.append({"symbol": symbol, "documents": documents, "source_anomalies": []})
save(
    base / "final_issuer_context.json",
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
