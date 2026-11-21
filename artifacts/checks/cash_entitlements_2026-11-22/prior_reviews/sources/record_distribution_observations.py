"""Record the fourteen actually inspected full pages as preliminary observations."""

from pathlib import Path

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
rendering_file = base / "preliminary_rendering.json"
rendering = read(rendering_file)
notes = {
    ("sz300750/2024-04-30", 1): "The 300750 / 2024-030 issuer and 2023 annual implementation title are visible. The special notice states gross RMB 50.28 per ten participating shares, total capital 4399041236, excluded repurchases 11609630, participating shares 4387431606, aggregate RMB 22060006114.97 and ex-reference cash 5.0147304 per total share. These are distinct denominators.",
    ("sz300750/2024-04-30", 2): "Section two is the implemented scheme, repeating gross RMB 50.28 per ten shares. It is distinct from RMB 45.252 after-tax examples and holding-period tax amounts. The registration date is 2024-04-29 and ex-date 2024-04-30. The text says the approved and implemented schemes agree; no capital/repurchase change occurred in that interval.",
    ("sz300750/2024-04-30", 3): "The A-share CSDC payment sentence specifies 2024-04-30. Self-paid incentive shares and listed self-paying holders are separate. Section six repeats the aggregate and total-share denominator; ex-reference cash is explicitly truncated at seven decimal places without rounding. That truncation applies to the reference amount, not a generic account dividend rounding rule.",
    ("sz300750/2024-04-30", 4): "Continuation repeats previous close less 5.0147304. Repurchase price cap changes from 294.45 to 289.44 using that reference and two-place rounding. Option/award adjustments are separate parameters, not alternative ordinary shareholder cash dividends.",
    ("sh688025/2024-06-13", 1): "The 688025 / 2024-043 issuer and annual implementation title are visible. Summary says gross cash 0.35057 per share. Three labeled date columns identify registration 2024-06-12, ex-date and cash payment 2024-06-13. Repurchase shares are excluded from recipients.",
    ("sh688025/2024-06-13", 2): "The RMB 3.50 per ten shares is a historical proposal. Subsequent employee-plan issuance raises total shares from 94990945 to 95049423; repurchases rise from 224864 to 437970. Actual participating shares become 94611453. The final paragraph starts a total-fixed adjustment and continues on the next full page.",
    ("sh688025/2024-06-13", 3): "The continuation sets adjusted gross cash to 0.35057 per share, using original aggregate 33168128.35 / 94611453 and five decimal places. Final aggregate 33167937.08 uses the adjusted cash. The different ex-reference amount is 0.34895 with total capital 95049423. No stock bonus/capitalization is distributed. The date table repeats 2024-06-12 / 2024-06-13 / 2024-06-13.",
    ("sh688025/2024-06-13", 4): "Distribution method describes ordinary tradable-share CSDC payments and excluded repurchase shares. Tax discussion repeats actual per-share cash 0.35057, with delayed individual tax depending on holding duration. It corroborates the adjusted entitlement, not the old 0.35 proposal.",
    ("sh688627/2024-07-01", 1): "The 688627 / 2024-024 issuer and 2023 annual implementation title are visible. Summary states gross cash 0.37345 per share; labeled table gives registration 2024-06-28, ex-date/payment 2024-07-01. Repurchase shares are excluded.",
    ("sh688627/2024-07-01", 2): "Original proposal 3.72 per ten shares and aggregate 34972372.49 are followed by the explicit 2024-023 adjustment notice. Final entitlement changes from 0.372 to 0.37345, total shares 94011754 less repurchases 364908 gives 93646846 participants and final aggregate 34972414.64. The virtual ex-reference is 0.37200 per total share. No stock bonus/capitalization applies.",
    ("sh688627/2024-07-01", 3): "Three-column date table repeats registration 2024-06-28 and ex/payment 2024-07-01. CSDC pays all share types except repurchases; no self-paying recipients. The individual tax passage repeats actual cash 0.37345 rather than the old proposal or virtual reference.",
    ("sh688627/2025-07-10", 1): "The 688627 / 2025-058 issuer and 2024 annual implementation title are visible. Summary cash is 0.31910 per share, while the gross-tax qualifier is supplied in the detailed scheme. Registration is 2025-07-09, ex-date and cash payment 2025-07-10. Repurchase exclusion continues to the next page.",
    ("sh688627/2025-07-10", 2): "Original proposal 3.23 per ten shares and aggregate 29884722.28 precede the explicit 2025-055 adjustment. With 94011754 total shares less 358477 repurchases, 93653277 participate. Adjusted gross cash is 0.31910 and final aggregate 29884760.69. Virtual ex-reference 0.31788 uses all shares and five decimal places. No stock bonus/capitalization applies.",
    ("sh688627/2025-07-10", 3): "Date table repeats 2025-07-09 / 2025-07-10 / 2025-07-10. Payment excludes repurchased shares, covers other share types and has no self-paying recipients. The settlement-result caveat is preserved; the tax passage repeats actual cash 0.31910. No account-level cent-rounding convention is inferred.",
}
assert len(notes) == len(rendering["records"]) == 14
records = []
for row in rendering["records"]:
    key = row["event"], row["page"]
    assert key in notes
    assert sha(root / row["image"]) == row["image_sha256"]
    assert sha(root / row["source"]["path"]) == row["source"]["sha256"]
    records.append({**row, "whole_page_visually_inspected": True, "observation": notes[key]})
save(base / "preliminary_visual_observations.json", {
    "status": "PRELIMINARY_PAGES_REVIEWED", "reviewed_at": now(),
    "rendering_sha256": sha(rendering_file), "script_sha256": sha(Path(__file__)),
    "pages": 14, "events": 4, "records": records, "facts_accepted": 0,
    "scope": "Actual full-page visual observations, not final source acceptance. Separate registration, independent field/arithmetic checks and related-notice/revision inventory remain required before applying any fact. The current implemented-payments integration is unchanged.",
}, immutable=True)
print("RECORDED 14 OBSERVED WHOLE PAGES; NO FACTS APPLIED", flush=True)
