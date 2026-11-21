"""Record the nine related adjustment pages inspected during computation."""

from pathlib import Path

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
rendering_file = base / "related_rendering.json"
rendering = read(rendering_file)
notes = {
    ("1220090735", 1): "688025 / 2024-034 changes gross per-share cash from 0.35 to 0.35057. The original proposal is RMB 3.50 per ten shares and aggregate 33168128.35; the initial base used total shares 94990945 less 224864 repurchases. Employee-plan issuance and later repurchases are explicitly the reasons for adjustment.",
    ("1220090735", 2): "Additional 58478 issued shares raise total capital to 95049423; repurchases increase to 437970. Participating shares become 94611453. Fixed original aggregate divided by those shares gives 0.35057 at five-place precision; resulting total is 33167937.08. The adjusted-scheme sentence continues on the next physical page.",
    ("1220090735", 3): "The continuation repeats excluded repurchases 437970, gross per-share cash 0.35057 and aggregate 33167937.08. It explicitly attributes the total difference to rounding of per-share cash. Signature date is 2024-05-18, before the 2024-06-06 implementation notice. This is scheme-level precision, not an account-level settlement rounding instruction.",
    ("1220290237", 1): "688627 / 2024-023 changes gross per-ten-share cash from 3.72 to 3.7345 because 364908 repurchased shares do not participate. Original total capital 94011754 and original aggregate 34972372.49 are stated. The physical first page visibly carries printed footer 2; preserve that source label without renumbering or stripping arbitrary digits.",
    ("1220290237", 2): "Participation is 93646846; original aggregate divided by participation yields per-share 0.37345 and adjusted total 34972414.64. Future implementation will specify the registration date. Signature is 2024-06-07, preceding the 2024-06-25 implementation. This second physical page also prints footer 2; physical image indexes remain authoritative for this diagnostic.",
    ("1223900960", 1): "688627 / 2025-053 first changes the 2024 annual proposal from gross RMB 3.23 to 3.2351 per ten shares. Repurchases reach 1636477. The original proposal used 94011754 total shares less 1489394 repurchases, or 92522360 participants, with aggregate 29884722.28.",
    ("1223900960", 2): "This intermediate scheme has 92375277 participants and gross per-share 0.32351, with resulting aggregate 29884325.86. It leaves the registration date to the later implementation notice. Signature date is 2025-06-18. Its amount is an intermediate revision, not the final July entitlement.",
    ("1224017643", 1): "688627 / 2025-055 expressly supersedes the 2025-053 amount: gross per-ten-share cash changes from 3.2351 to 3.1910. Non-trading transfer of 1278000 repurchased shares to the employee plan reduces excluded repurchases from 1636477 to 358477. The earlier 0.32351 scheme and its total are retained as history.",
    ("1224017643", 2): "Participation increases to 93653277, and the unchanged original aggregate 29884722.28 is divided by that new base. Final gross per-share cash becomes 0.31910 and aggregate 29884760.69. Signature date is 2025-06-28, before the 2025-07-04 implementation notice. The dated succession distinguishes original, intermediate and final amounts without selecting by a provider's expected value.",
}
assert len(notes) == len(rendering["records"]) == 9
records = []
for row in rendering["records"]:
    key = row["id"], row["page"]
    assert sha(root / row["image"]) == row["image_sha256"]
    assert sha(root / row["source"]["path"]) == row["source"]["sha256"]
    records.append({**row, "whole_page_visually_inspected": True, "observation": notes[key]})
save(base / "related_visual_observations.json", {
    "status": "PRELIMINARY_RELATED_PAGES_REVIEWED", "reviewed_at": now(),
    "rendering_sha256": sha(rendering_file), "script_sha256": sha(Path(__file__)),
    "pages": 9, "documents": 4, "records": records, "facts_accepted": 0,
    "scope": "The four archived adjustment notices explicitly linked to the diagnostic schemes were visually read in full. Other archive notices are inventoried but this does not certify archive completeness. No source acceptance, data change or new return claim follows.",
}, immutable=True)
print("RECORDED 9 RELATED WHOLE PAGES; NO FACTS APPLIED", flush=True)
