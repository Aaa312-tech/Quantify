"""Read primary candidates for every unresolved provider precision difference."""

from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from quantfolio.distributions import extract_terms
from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
initial_file = base / "initial_inventory.json"
initial = read(initial_file)
index_file = root / "artifacts/checks/disclosure_sources_2026-11-21/primary_event_index.json"
structured_file = root / "artifacts/checks/disclosure_sources_2026-11-21/all_structured_actions.json"
index = {(row["symbol"], row["ex_date"]): row for row in read(index_file)["records"]}
structured = {(row["symbol"], row["ex_date"]): row for row in read(structured_file)["records"]}
records = []
for row in initial["records"]:
    if row["status"] != "VALUE_OR_DATE_MISMATCH":
        continue
    key = row["symbol"], row["ex_date"]
    primary, provider = index[key], structured[key]
    original = row["source_review_record"]
    if primary["status"] == "ONE_TEXT_CANDIDATE":
        candidate = primary["candidates"][0]
    else:
        selected = original.get("candidate_resolution", {}).get("selected_id")
        candidate = next((item for item in primary["candidates"] if item["id"] == selected), None)
    record = {"symbol": key[0], "ex_date": key[1], "structured_review": provider,
              "prior_candidate_selection": original.get("candidate_resolution"), "candidate": candidate}
    if candidate:
        pdf = root / candidate["source"]
        assert sha(pdf) == candidate["source_sha256"]
        pages = [page.extract_text() or "" for page in PdfReader(pdf).pages]
        terms = extract_terms(pages)
        amounts = sorted({Decimal(item["value"]) for item in terms.get("cash_per_share", [])})
        stored = Decimal(str(row["current_action"]["cash_per_share"]))
        record.update(pages=pages, diagnostic_terms=terms, parsed_amounts=[str(amount) for amount in amounts],
                      current_amount=str(stored), primary_minus_current=[str(amount-stored) for amount in amounts])
    records.append(record)
assert len(records) == 26
save(base / "provider_precision_primary_diagnostic.json", {
    "status": "DIAGNOSTIC_ONLY", "created_at": now(), "script_sha256": sha(Path(__file__)),
    "extractor_sha256": sha(root / "src/quantfolio/distributions.py"),
    "initial_inventory_sha256": sha(initial_file), "primary_index_sha256": sha(index_file),
    "structured_review_sha256": sha(structured_file), "records": records,
    "facts_accepted": 0, "scope": "The unchanged extractor is used only to locate primary financial passages for all 26 provider precision cases. This does not bypass any production gate, approve facts, identify a unique final entitlement where parsing is incomplete, or loosen a tolerance. Independent reading and visual checks remain required.",
}, immutable=True)
for row in records:
    print(row["symbol"], row["ex_date"], "stored", row.get("current_amount"), "primary parsed", row.get("parsed_amounts"), "PDF", row["candidate"]["id"] if row["candidate"] else None, flush=True)
