"""Inventory remaining source failures without approving facts or changing inputs."""

from collections import Counter
from pathlib import Path

import joblib
from pypdf import PdfReader

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
review_file = root / "artifacts/checks/payout_scope_2026-11-21/source_review.json"
bundle_file = root / "data/processed/2026-11-20-declared-payments/bundle.joblib"
review = read(review_file)
bundle = joblib.load(bundle_file)
actions = {(row["symbol"], row["ex_date"]): row for row in bundle["actions"]}
assert len(actions) == len(bundle["actions"]) == 1839
destination = root / ".git/distribution_frontier_2026_11_22"
destination.mkdir()
records = []
for row in review["records"]:
    if row["status"] == "VERIFIED_FIELDS":
        continue
    action = actions[row["symbol"], row["ex_date"]]
    record = {
        "symbol": row["symbol"], "ex_date": row["ex_date"], "status": row["status"],
        "missing_payment": not action.get("cash_pay_date"),
        "missing_listing": action.get("share_multiplier", 1) > 1 and not action.get("share_list_date"),
        "current_action": action,
        "source_review_record": row,
    }
    if row["status"] == "PRIMARY_GROSS_AMOUNT_MISMATCH":
        source = row["source"]
        pdf = root / source["path"]
        assert sha(pdf) == source["sha256"]
        pages = [page.extract_text() for page in PdfReader(pdf).pages]
        text_file = destination / (pdf.stem + "_pages.json")
        save(text_file, {"source": source, "pages": pages, "accepted": False}, immutable=True)
        record["extracted_pages"] = {
            "path": text_file.relative_to(root).as_posix(), "sha256": sha(text_file),
        }
    records.append(record)
counts = dict(Counter(row["status"] for row in records))
assert len(records) == 418
assert counts == {key: value for key, value in review["counts"].items() if key != "VERIFIED_FIELDS"}
observations = {
    "sz300750/2024-04-30": {
        "issue": "Primary implemented cash statement differs from both current/provider amounts; inspect any related correction notices and distribution scope.",
        "current_per_share": "2.011", "primary_candidate_per_share": "5.028",
        "primary_candidate_ex_reference": "5.0147304", "pages_to_review": [1, 2, 3, 4],
    },
    "sh688025/2024-06-13": {
        "issue": "Extractor retained the original proposal. Primary text states an adjusted final entitlement consistent with the current/provider amount; independently verify the adjustment and reference amount.",
        "extracted_proposal_per_share": "0.35", "primary_candidate_per_share": "0.35057",
        "pages_to_review": [1, 2, 3, 4],
    },
    "sh688627/2024-07-01": {
        "issue": "Extractor retained the original proposal. Primary text states an adjusted final entitlement consistent with the current/provider amount; distinguish entitlement from virtual ex-reference.",
        "extracted_proposal_per_share": "0.372", "primary_candidate_per_share": "0.37345",
        "primary_candidate_ex_reference": "0.37200", "pages_to_review": [1, 2, 3],
    },
    "sh688627/2025-07-10": {
        "issue": "Extractor retained the original proposal. Primary text states an adjusted final entitlement consistent with the current/provider amount; distinguish entitlement from virtual ex-reference.",
        "extracted_proposal_per_share": "0.323", "primary_candidate_per_share": "0.31910",
        "primary_candidate_ex_reference": "0.31788", "pages_to_review": [1, 2, 3],
    },
}
save(destination / "initial_inventory.json", {
    "status": "DIAGNOSTIC_ONLY", "created_at": now(),
    "review": {"path": review_file.relative_to(root).as_posix(), "sha256": sha(review_file)},
    "bundle": {"path": bundle_file.relative_to(root).as_posix(), "sha256": sha(bundle_file)},
    "script_sha256": sha(Path(__file__)), "unresolved_events": len(records), "counts": counts,
    "records": records, "text_observations": observations,
    "facts_accepted": 0, "inputs_changed": False, "source_modules_changed": False,
    "scope": "Initial inventory and unaccepted text observations. Whole-page visual review, independent field checks, revision inventory and separately registered source/integration acceptance remain required. No return-based selection.",
}, immutable=True)
print("DIAGNOSTIC INVENTORY", len(records), counts, "NO FACTS ACCEPTED", flush=True)
