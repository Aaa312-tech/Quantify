"""Record the eight full pages actually inspected; accept no runtime facts."""

from pathlib import Path

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
rendering_file = base / "shareholder_rendering.json"
rendering = read(rendering_file)
notes = {
    ("1220664181", 1): "600989 / 2024-040 is the 2023 annual implementation. Summary separates minority 0.3158 from controlling-holder 0.2650 per share. The role-labelled table gives record 2024-07-23 and ex/payment 2024-07-24. The shareholder-meeting date is 2024-06-21; the final paragraph continues the references to board and distribution adjustment notices on page 2.",
    ("1220664181", 2): "The continuation identifies 2024-011 and 2024-038. The compensation is 77178848; explicitly gross minority cash is 0.3158 with aggregate 686198642.10, and controlling-holder cash is 0.2650 with aggregate 1367524566.70. Total 2053723208.80 and the separately stated average/reference 0.28 must not replace ordinary minority entitlement. Total shares 7333360000 and zero share-count change appear in the reference calculation. The repeated date table agrees with page 1.",
    ("1220664181", 3): "Three control-group holders are self-paid. The ordinary unrestricted public-holder tax passage repeats 0.3158 at initial payment; restricted and other identified taxable holder categories have 0.28422 after-tax amounts. These are holder/tax distinctions, not alternative gross amounts or an account-cent rounding instruction.",
    ("1220664181", 4): "The tax continuation retains 0.28422 for the identified cross-border category and expressly repeats 0.3158 before tax for other institutions/legal persons. The document closes with the issuer signature dated 2024-07-18. Contact details add no economic terms. This page was inspected in full.",
    ("1223476469", 1): "600989 / 2025-016 is the 2024 annual implementation. Summary explicitly states gross minority 0.4598 and controlling-holder 0.3891 per share. The role-labelled table gives record 2025-05-12 and ex/payment 2025-05-13. The shareholder meeting is 2025-04-02; the board-reference sentence crosses to page 2.",
    ("1223476469", 2): "The continuation identifies 2025-002. Compensation is 107958432.98. Gross minority 0.4598 and aggregate 996454986.05 are separate from controlling-holder 0.3891 and aggregate 2010172805.16. Total 3006627791.21 is followed by declared average/reference 0.41 and the 7333360000-share reference formula. No share-count change is stated. The repeated date table agrees with page 1. Provider agreement around 0.4099932 cannot establish the minority cash entitlement.",
    ("1223476469", 3): "Three control-group holders are self-paid. Ordinary unrestricted public-holder tax wording repeats initial cash 0.4598, while identified restricted/cross-border categories use after-tax 0.41382. These passages confirm recipient scope without changing the project's unconfirmed competition-tax treatment.",
    ("1223476469", 4): "The continuation retains 0.41382 for the identified cross-border category; other institutions/legal persons explicitly receive 0.4598 before tax. The issuer signature is dated 2025-05-07. No account-cent rounding instruction or changed implementation date was observed on this whole page.",
}
assert len(notes) == len(rendering["records"]) == 8
records = []
for row in rendering["records"]:
    assert sha(root / row["image"]) == row["image_sha256"]
    assert sha(root / row["source"]["path"]) == row["source"]["sha256"]
    assert sha(root / row["receipt"]["path"]) == row["receipt"]["sha256"]
    records.append({**row, "whole_page_visually_inspected": True,
                    "observation": notes[(row["id"], row["page"])]})
save(base / "shareholder_visual_observations.json", {
    "status": "PRELIMINARY_SHAREHOLDER_PAGES_REVIEWED", "reviewed_at": now(),
    "rendering_sha256": sha(rendering_file), "script_sha256": sha(Path(__file__)),
    "pages": 8, "documents": 2, "records": records, "facts_accepted": 0,
    "scope": "Full-page observations only. Related-notice closure, independent arithmetic and registered source acceptance remain required. No active input, training source, historical result or competition tax rule changed.",
}, immutable=True)
print("RECORDED 8 SHAREHOLDER PAGES; NO FACTS APPLIED", flush=True)
