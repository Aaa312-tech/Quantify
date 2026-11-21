"""Adjudicate layout boundaries and listing dates without changing archived extraction."""

import datetime as dt
import importlib.util
import re
from pathlib import Path

import pymupdf
from pypdf import PdfReader

from quantfolio.storage import dump_gzip, now, read, save, sha

ROOT = Path.cwd()
OUT = ROOT / "artifacts/checks/etf_sources_2026-11-21"
PROCESSED = ROOT / "data/processed/etf_sources_2026-11-21"
spec = importlib.util.spec_from_file_location("etf_review", OUT / "review_source.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
initial = read(PROCESSED / "observations.json")["observations"]
early = read(ROOT / "data/raw/etf_early_anchor_2026-11-21/collection.json")
bank, bank_text = review.observations(early["documents"][0])
save(OUT / "field_closure_protocol.json", {
    "registered_at": now(), "source_sha256": sha(Path(__file__)), "initial_review_sha256": sha(OUT / "review.json"),
    "early_anchor_collection_sha256": sha(ROOT / "data/raw/etf_early_anchor_2026-11-21/collection.json"),
    "scope": "Add the registered 2022 bank summary, cross-check listing dates in every accepted summary, and adjudicate two cross-page benchmark spans using rendered source pages. Keep initial extraction and wrong-document rejection unchanged.",
    "benchmark_layout_cases": ["sh512800/2025-06-05: following-page footer 2", "sh588000/2026-05-29: following-page footer 2/6"],
    "no_input_activation": True,
}, immutable=True)
rows = [*initial, bank]
accepted, unresolved = [], []
corrections = []
date_pattern = re.compile(r"(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})日?")
for row in rows:
    if not row["identity_verified_by_both_parsers"]:
        continue
    path = ROOT / (row.get("reading_copy") or row["source_path"])
    source = ROOT / row["source_path"]
    if sha(source) != row["source_sha256"]:
        raise ValueError("Original source changed")
    with pymupdf.open(path) as doc:
        primary = [page.get_text() for page in doc]
    independent = [page.extract_text() or "" for page in PdfReader(path).pages]
    dates, contexts = [], []
    for pages in (primary, independent):
        first = review.compact(pages[0])
        start = first.find("上市交易所")
        finish = first.find("基金类型", start)
        context = first[start:finish] if start >= 0 and finish > start else ""
        matches = date_pattern.findall(context)
        contexts.append(context)
        dates.append(dt.date(*map(int, matches[0])).isoformat() if len(matches) == 1 else None)
    if not dates[0] or dates[0] != dates[1] or contexts[0] != contexts[1]:
        unresolved.append({"symbol": row["symbol"], "date": row["catalogue_date"], "field": "listing_on", "dates": dates, "contexts": contexts})
    benchmark = row["agreed_fields"]["performance_benchmark"]
    value = benchmark["value"]
    if len(benchmark["pages"]) > 1:
        key = (row["symbol"], row["catalogue_date"])
        endings = {("sh512800", "2025-06-05"): ("2", "中证银行指数"),
                   ("sh588000", "2026-05-29"): ("2/6", "上证科创板50成份指数收益率")}
        footer, corrected = endings[key]
        if value != corrected + footer:
            raise ValueError("Unexpected cross-page benchmark")
        for pages in (primary, independent):
            left = review.compact(pages[0]).split("业绩比较基准")[-1]
            right = review.compact(pages[1]).split("风险收益特征")[0]
            if left != corrected or right != footer:
                raise ValueError("Cross-page boundary differs from inspected source")
        corrections.append({"symbol": key[0], "catalogue_date": key[1], "source_path": row["source_path"],
                            "source_sha256": row["source_sha256"], "raw_span": value, "field_value": corrected,
                            "excluded_text": footer, "excluded_role": "following_page_footer_before_risk_row", "visual_pages": [1, 2]})
        value = corrected
    accepted.append({**row, "reviewed_benchmark": value, "listing_on": dates[0],
                     "listing_evidence": {"page": 1, "raw_span": contexts[0], "both_readers_agree": dates[0] is not None and dates[0] == dates[1] and contexts[0] == contexts[1]}})
if unresolved:
    save(OUT / "listing_unresolved.json", unresolved, immutable=True)
    raise ValueError(f"Unresolved listing dates: {len(unresolved)}")
funds = []
prior = {r["symbol"]: r for r in read(PROCESSED / "funds.json")}
for symbol, old in sorted(prior.items()):
    selected = [r for r in accepted if r["symbol"] == symbol]
    listing = {r["listing_on"] for r in selected}
    inception = {r["agreed_fields"]["inception_on"] for r in selected}
    types = {r["agreed_fields"]["fund_type"]["value"] for r in selected}
    if len(listing) != 1 or len(inception) != 1 or len(types) != 1:
        raise ValueError("Fund identity/type changed across source documents")
    before = [r for r in selected if r["catalogue_date"] < "2024-01-02"]
    if not before:
        raise ValueError("Fund has no pre-model summary anchor")
    funds.append({"symbol": symbol, "current_name": old["current_name"], "legacy_label": old["legacy_label"],
                  "fund_type": next(iter(types)), "inception_on": next(iter(inception)), "listing_on": next(iter(listing)),
                  "summary_count": len(selected), "benchmark_wording_variants": sorted({r["reviewed_benchmark"] for r in selected}),
                  "earliest_source_date": min(r["catalogue_date"] for r in selected), "latest_source_date": max(r["catalogue_date"] for r in selected),
                  "pre_model_anchor": {k: min(before, key=lambda r: r["catalogue_date"])[k] for k in ("source_path", "source_sha256", "catalogue_date")},
                  "historical_publication_time_certified": False, "continuous_unchanged_index_certified": False,
                  "look_through_sector_weights_available": False})
save(PROCESSED / "reviewed_observations.json", {"records": accepted, "corrections": corrections, "rejected_source_records": [r for r in initial if not r["identity_verified_by_both_parsers"]]}, immutable=True)
save(PROCESSED / "funds_reviewed.json", funds, immutable=True)
dump_gzip(PROCESSED / "early_anchor_pages.json.gz", bank_text)
save(OUT / "field_closure.json", {"status": "FIELD_SCOPED_REVIEW_COMPLETE_NOT_ACTIVATED", "completed_at": now(),
    "source_sha256": sha(Path(__file__)), "accepted_summary_pdfs": len(accepted), "accepted_pages": sum(r["page_count"] for r in accepted),
    "funds": len(funds), "pre_model_anchors": len(funds), "listing_dates_cross_checked": len(accepted),
    "cross_page_corrections": corrections, "rejected_catalogue_documents": 1,
    "declared_types": {kind: sum(r["fund_type"] == kind for r in funds) for kind in sorted({r["fund_type"] for r in funds})},
    "raw_sources_unchanged": True, "raw_extraction_unchanged": True, "model_inputs_changed": False,
    "classification_scope": "Source-stated legal fund type and performance benchmark; not CAPCO sector assignment or dated constituent exposure."}, immutable=True)
print({"funds": len(funds), "summaries": len(accepted), "listing_dates": len(accepted), "corrected_page_boundaries": len(corrections)}, flush=True)
