"""Corroborate ETF split facts and preserve the dated index-scope amendment."""

import re
from decimal import Decimal
from pathlib import Path

import joblib
import pymupdf
from pypdf import PdfReader

from quantfolio.storage import dump_gzip, now, read, save, sha

root = Path.cwd()
out = root / "artifacts/checks/etf_sources_2026-11-21"
p = read(root / "configs/protocols/etf_sources.json")
bundle = joblib.load(root / p["baseline_bundle"])
if sha(root / p["baseline_bundle"]) != p["baseline_bundle_sha256"]:
    raise ValueError("Baseline changed")
documents = [d for folder in ("etf_structural_notices_2026-11-21", "etf_merger_notices_2026-11-21")
             for d in read(root / "data/raw" / folder / "collection.json")["documents"]]
pages, checks = [], []
for row in documents:
    file = root / row["download"]["path"]
    if row["status"] != "RECEIVED" or sha(file) != row["download"]["sha256"]:
        raise ValueError("Notice source failed")
    with pymupdf.open(file) as doc:
        primary = [page.get_text() for page in doc]
    independent = [page.extract_text() or "" for page in PdfReader(file).pages]
    if len(primary) != len(independent):
        raise ValueError("Notice page counts disagree")
    texts = ["".join("".join(page.split()) for page in group) for group in (primary, independent)]
    if not all(row["symbol"][2:] in text for text in texts):
        raise ValueError("Notice code missing")
    pages.append({"source_path": row["download"]["path"], "pymupdf": primary, "pypdf": independent})
    result = {"symbol": row["symbol"], "title": row["title"], "catalogue_date": row["catalogue_date"],
              "source_url": row["url"], "source_path": row["download"]["path"], "source_sha256": sha(file),
              "pages": len(primary), "status": "SOURCE_ARCHIVED_IDENTITY_CHECKED"}
    if "结果" in row["title"]:
        known = [a for a in bundle["actions"] if a["symbol"] == row["symbol"] and a["ex_date"] == row["catalogue_date"] and a["share_multiplier"] != 1]
        if len(known) != 1:
            raise ValueError("Result notice has no unique existing ETF action")
        action = known[0]
        ratios, date_contexts = [], []
        y, m, d = map(int, action["record_date"].split("-"))
        literal_date = f"{y}年{m}月{d}日"
        for text in texts:
            matches = re.findall(r"1[：:](\d+(?:\.\d+)?)", text)
            if "份额合并" in row["title"]:
                matches = re.findall(r"本基金本次基金份额合并比例为(\d+\.\d+)", text)
            values = {Decimal(value) for value in matches}
            if len(values) != 1:
                raise ValueError("Ambiguous split ratio")
            ratios.append(next(iter(values)))
            candidates = []
            for match in re.finditer(re.escape(literal_date), text):
                context = text[max(0, match.start() - 30):match.end() + 30]
                if "权益登记日" in context:
                    candidates.append(context)
            if not candidates:
                raise ValueError("Record date is not tied to an entitlement statement")
            date_contexts.append(candidates)
        if ratios[0] != ratios[1] or ratios[0] != Decimal(str(action["share_multiplier"])):
            raise ValueError("Source split ratio differs from existing bundle")
        if action.get("fractional_rounding") == "CEILING" and not all("上进位" in text for text in texts):
            raise ValueError("Reverse-split rounding not corroborated")
        result.update(status="EXISTING_RATIO_AND_RECORD_DATE_CORROBORATED", existing_action_id=action["id"],
                      observed_ratio=str(ratios[0]), record_date=action["record_date"],
                      date_contexts=date_contexts, existing_simulated_ex_date=action["ex_date"],
                      simulated_ex_date_equals_result_catalogue_date=True,
                      rounding_corroborated=action.get("fractional_rounding") == "CEILING",
                      scope="Ratio and entitlement record date only; catalog date and current PDF do not prove every matching/timing term.")
    elif "标的指数" in row["title"]:
        clauses = ["515180易方达中证红利交易型开放式指数证券投资基金", "本次修订涉及的相关规则自2024年12月16日起生效", "北京证券交易所股票"]
        for clause in clauses:
            if not all(clause in text for text in texts):
                raise ValueError("Index-scope amendment clause differs")
        result.update(status="DATED_INDEX_SCOPE_AMENDMENT_VERIFIED", effective_date="2024-12-16",
                      applies_to="sh515180", verified_clauses=clauses, source_pages=[1, 2],
                      interpretation="Published rules add Beijing-market constituents and related cash-substitution/risk provisions; no dated constituent weights or complete historical composition are inferred.")
    checks.append(result)
matched = [row for row in checks if row["status"] == "EXISTING_RATIO_AND_RECORD_DATE_CORROBORATED"]
known = [a for a in bundle["actions"] if bundle["meta"][a["symbol"]]["kind"] == "etf" and a["share_multiplier"] != 1]
if {r["existing_action_id"] for r in matched} != {a["id"] for a in known}:
    raise ValueError("Existing ETF split inventory not covered")
dump_gzip(root / "data/processed/etf_sources_2026-11-21/structural_notice_pages.json.gz", pages)
save(out / "structural_review.json", {"status": "FIELD_SCOPED_SOURCE_REVIEW_COMPLETE", "completed_at": now(),
    "source_sha256": sha(Path(__file__)), "documents": checks, "document_count": len(checks),
    "existing_split_actions_checked": len(matched), "new_actions_applied": 0,
    "index_scope_amendments_verified": sum(r["status"] == "DATED_INDEX_SCOPE_AMENDMENT_VERIFIED" for r in checks),
    "remaining_amendment_title_hits": "Other generic contract/name/fee amendments remain a review queue; the title screen is not an exhaustive guarantee.",
    "model_inputs_changed": False}, immutable=True)
print({"documents": len(checks), "existing_splits_corroborated": len(matched), "scope_amendments": 1}, flush=True)
