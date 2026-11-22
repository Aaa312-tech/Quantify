"""Review the fixed financial-notice queue and retain unresolved event boundaries."""

import re
import unicodedata
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from importlib.metadata import version
from pathlib import Path

import pymupdf
from pypdf import PdfReader
from review_candidate_amendments import prose, require

from quantfolio.storage import digest, dump_gzip, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/financial_notice_relations.json")
NOTES = BASE / "remaining_amendment_notes.json"
PRIOR = Path("artifacts/checks/distribution_roles_2026-11-21/amendment_closure.json")
REVIEW = Path("artifacts/checks/cash_entitlements_2026-11-22/source_review.json")
NUMBERS = r"\d+(?:,\d{3})*(?:\.\d+)?"
CASH = (
    r"每\s*(10)?\s*股(?:派发现金(?:红利|股利)(?:为|人民币)?|现金红利人民币)"
    r"\s*(\d+(?:\.\d+)?)\s*元(?:人民币)?\s*\(含税"
)
CAPITAL = r"每\s*10\s*股(?:以(?:资本)?公积金?)?转增\s*(\d+(?:\.\d+)?)\s*股"


def scoped(text, note, role):
    text = prose(text)
    if start := note.get(f"{role}_section_start"):
        matches = list(re.finditer(start, text))
        require(len(matches) == 1, f"Ambiguous {role} section start")
        text = text[matches[0].end() :]
    if end := note.get(f"{role}_section_end"):
        matches = list(re.finditer(end, text))
        require(len(matches) == 1, f"Ambiguous {role} section end")
        text = text[: matches[0].start()]
    return text


def inventory(text):
    return Counter(re.findall(NUMBERS, unicodedata.normalize("NFKC", text)))


def financial_check(identifier, note, pages, reader):
    cash_text = scoped(pages[note["cash_page"] - 1], note, "cash")
    matches = list(re.finditer(CASH, cash_text))
    cash = Decimal(note["printed_cash_per_unit"]) / note["printed_cash_unit_shares"]
    require(matches, "Current gross cash absent")
    for match in matches:
        require(
            int(match[1] or 1) == note["printed_cash_unit_shares"]
            and Decimal(match[2]) / int(match[1] or 1) == cash,
            "Current gross cash or unit conflicts",
        )
    terms = scoped("\n".join(pages[p - 1] for p in note["terms_pages"]), note, "terms")
    numbers = {Decimal(v.replace(",", "")) for v in inventory(terms)}
    eligible, total = Decimal(note["eligible_shares"]), Decimal(note["printed_new_aggregate"])
    require(eligible in numbers and total in numbers, "Current base/aggregate missing")
    product = cash * eligible
    rounded = product.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if note.get("aggregate_role") == "RETAINED_EXPECTED_TOTAL":
        require(
            product - total == Decimal(note["expected_exact_product_minus_printed"]),
            "Expected-total diagnostic differs",
        )
        arithmetic_status = "EXPECTED_TOTAL_DIFFERS_FROM_PUBLISHED_RATIO_PRODUCT"
    else:
        require(rounded == total, "Declared aggregate does not reconcile at printed cent precision")
        arithmetic_status = "DECLARED_AGGREGATE_RECONCILES_AT_CENT_PRECISION"
    result = {
        "id": identifier,
        "reader": reader,
        "cash_per_share": str(cash),
        "gross_quotes": [m.group(0) for m in matches],
        "eligible_shares": str(eligible),
        "exact_cash_product": str(product),
        "cent_rounded_product": str(rounded),
        "printed_total": str(total),
        "exact_product_minus_printed": str(product - total),
        "status": arithmetic_status,
        "account_rounding_certified": False,
    }
    if places := note.get("intended_total_ratio_decimal_places"):
        ratio = Decimal(note["printed_old_aggregate"]) / eligible
        require(
            ratio.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP) == cash, "Cash ratio"
        )
        result["intended_total_quotient"] = str(ratio)
        result["declared_ratio_decimal_places"] = places
    if note["capitalization_per_share"] is not None:
        capital_text = scoped(
            pages[note.get("capitalization_page", note["cash_page"]) - 1], note, "cash"
        )
        rates = [Decimal(v) / 10 for v in re.findall(CAPITAL, capital_text)]
        capital = Decimal(note["capitalization_per_share"])
        added = Decimal(note["printed_added_shares"])
        require(rates and set(rates) == {capital}, "Current capitalization ratio")
        require(added in numbers, "Declared added shares missing")
        result["capitalization"] = {
            "per_share": str(capital),
            "exact_added_shares": str(eligible * capital),
            "printed_added_shares": str(added),
            "exact_minus_printed": str(eligible * capital - added),
            "registration_and_account_rounding_not_certified": True,
        }
    if identifier == "1225311448":
        context = prose(pages[1])
        require(re.search(r"2025\s*年\s*3\s*月\s*21", context), "Old capital reference date")
        require(re.search(r"2026\s*年\s*3\s*月\s*20", context), "Old cash reference date")
        require("2025-036" in context, "Original incentive reference")
    if identifier == "1225344207":
        require("不以资本公积金转增股本" in prose(pages[1]), "Earlier no-capitalization terms")
        require("尚需提交公司" in prose(pages[0]), "Uncompleted shareholder approval")
    return result


def main():
    protocol, notes = read(PROTOCOL), read(NOTES)
    dependencies = {item["path"]: item["sha256"] for item in protocol["inputs"]}
    require(all(sha(p) == h for p, h in dependencies.items()), "Registered dependency changed")
    archive = load_gzip(BASE / "source_text.json.gz")["documents"]
    archive.update(load_gzip(BASE / "related_source_text.json.gz")["documents"])
    prior = read(PRIOR)
    require(prior["status"] == "PASS", "Prior review status")
    old = {r["id"]: r for r in prior["records"]}
    registered = {r["source"]["id"]: r for r in protocol["sources"]}
    require({r["id"] for r in notes["records"]} == set(registered), "Document scope")
    documents, checks, reused, failures = {}, [], [], []
    pages_compared = numeric_tokens = observed = fresh_pages = 0
    periods = {}
    for note in notes["records"]:
        identifier, source = note["id"], note["source"]
        require(
            source == registered[identifier]["source"] == archive[source["path"]]["source"],
            "Source",
        )
        require(sha(source["path"]) == source["sha256"], "PDF identity")
        require(sha(source["receipt"]) == source["receipt_sha256"], "Receipt identity")
        pypdf_pages = [page.extract_text() for page in PdfReader(source["path"]).pages]
        with pymupdf.open(source["path"]) as pdf:
            mupdf_pages = [page.get_text(sort=False) for page in pdf]
        require(pypdf_pages == archive[source["path"]]["readers"]["pypdf"]["pages"], "Stored text")
        require(len(pypdf_pages) == len(mupdf_pages) == note["page_count"], "Physical page count")
        readers = {"pypdf": pypdf_pages, "mupdf_unsorted": mupdf_pages}
        documents[identifier] = {"source": source, "readers": readers}
        for page, (left, right) in enumerate(zip(pypdf_pages, mupdf_pages), 1):
            first, second = inventory(left), inventory(right)
            if first != second:
                failures.append(
                    {
                        "id": identifier,
                        "page": page,
                        "reason": "NUMERIC_INVENTORY",
                        "pypdf_only": dict(first - second),
                        "mupdf_only": dict(second - first),
                    }
                )
            pages_compared += 1
            numeric_tokens += sum(first.values())
        if note["review_mode"] == "REUSED_PRIOR_SCOPED_FULL_TEXT_REVIEW":
            record = old[identifier]
            require(
                digest(record)
                == note["prior_record_sha256"]
                == registered[identifier]["prior_record_sha256"],
                "Prior record identity",
            )
            require(
                record["source"] == source["path"] and record["source_sha256"] == source["sha256"],
                "Reused source",
            )
            require(
                record["receipt"] == source["receipt"]
                and record["receipt_sha256"] == source["receipt_sha256"],
                "Reused receipt",
            )
            require(len(record["pages"]) == note["page_count"], "Prior full-text scope")
            require(record["period"] == note["period"], "Prior period")
            periods[identifier] = record["period"]
            reused.append(
                {
                    "id": identifier,
                    "prior_record_sha256": digest(record),
                    "pages": len(record["pages"]),
                    "period": record["period"],
                    "prior_relevance_preserved": record["relevance"],
                    "new_financial_checks_claimed": False,
                }
            )
            continue
        require(
            note["full_text_pages_read"] == list(range(1, note["page_count"] + 1)),
            "Full reading scope",
        )
        fresh_pages += len(pypdf_pages)
        for page in note["observed_pages"]:
            require(
                page["full_page_actually_observed"] and sha(page["image"]) == page["image_sha256"],
                "Observed page identity",
            )
            observed += 1
        scheme = note["scheme"]
        suffix = "年度" if scheme["period_kind"] == "annual" else "半年度"
        periods[identifier] = [str(scheme["year"]), suffix]
        for reader, pages in readers.items():
            try:
                require(
                    re.search(str(scheme["year"]) + r"\s*年?\s*" + suffix, prose("\n".join(pages))),
                    "Declared period",
                )
                checks.append(financial_check(identifier, scheme, pages, reader))
            except ValueError as error:
                failures.append({"id": identifier, "reader": reader, "reason": str(error)})

    prior_records = read(REVIEW)["records"]
    original = {(r["symbol"], r["ex_date"]): r for r in prior_records}
    index = read("artifacts/checks/disclosure_sources_2026-11-21/primary_event_index.json")
    indexed = {(r["symbol"], r["ex_date"]): r for r in index["records"]}
    relations, event_contexts = [], []
    for event in protocol["residual_events"]:
        key = event["symbol"], event["ex_date"]
        require(digest(original[key]) == event["prior_record_sha256"], "Original residual changed")
        candidates = indexed[key]["candidates"]
        references = [c for c in candidates if c["title"].endswith("权益分派实施公告")]
        require(len(references) == 1, "Catalogue period reference is ambiguous")
        reference = references[0]
        matches = re.findall(r"(20\d{2})年?(年度|半年度|中期)", reference["title"])
        require(len(matches) == 1, "Catalogue period is ambiguous")
        require(sha(reference["source"]) == reference["source_sha256"], "Period reference identity")
        event_contexts.append(
            {
                **event,
                "catalogue_candidates_preserved": candidates,
                "period_reference": reference,
                "catalogue_period": list(matches[0]),
                "primary_body_role_certified_here": False,
                "disposition": "REMAINS_UNVERIFIED_PENDING_FULL_RESIDUAL_REVIEW",
            }
        )
        for identifier, source_row in registered.items():
            source = source_row["source"]
            if event["symbol"] not in source["scoped_symbols"]:
                continue
            same = periods[identifier] == list(matches[0])
            relations.append(
                {
                    "id": identifier,
                    "symbol": event["symbol"],
                    "ex_date": event["ex_date"],
                    "notice_period": periods[identifier],
                    "catalogue_reference_id": reference["id"],
                    "catalogue_reference_period": list(matches[0]),
                    "same_catalogue_period": same,
                    "notice_catalogued_after_event": source["catalog_date"] > event["ex_date"],
                    "disposition": "SAME_PERIOD_FINANCIAL_TERMS_REQUIRE_PRIMARY_REVIEW"
                    if same
                    else "OTHER_CATALOGUE_PERIOD",
                    "event_accepted": False,
                }
            )
    require(len(documents) == 29 and pages_compared == 79, "Reader scope")
    require(
        len(reused) == 12 and fresh_pages == 47 and observed == 25, "Declared reuse/reading scope"
    )
    require(len(event_contexts) == 29 and len(relations) == 55, "Event relationship scope")
    text_path = BASE / "remaining_amendment_text.json.gz"
    require(not text_path.exists(), "Reader output must be new")
    dump_gzip(text_path, {"documents": documents})
    result = {
        "status": "PARTIAL_SCOPED_FINANCIAL_RELATIONS"
        if failures
        else "PASS_SCOPED_FINANCIAL_RELATIONS_WITH_LIMITS",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "helper_sha256": sha(BASE / "review_candidate_amendments.py"),
        "protocol_sha256": sha(PROTOCOL),
        "notes_sha256": sha(NOTES),
        "dependencies": dependencies,
        "reader_versions": {"pypdf": version("pypdf"), "pymupdf": version("pymupdf")},
        "text_output": {"path": text_path.as_posix(), "sha256": sha(text_path)},
        "documents_reextracted": len(documents),
        "numeric_page_pairs": pages_compared,
        "numeric_tokens_per_reader": numeric_tokens,
        "new_human_full_text_documents": 17,
        "new_human_full_text_pages": fresh_pages,
        "new_observed_whole_pages": observed,
        "reused_prior_full_text": reused,
        "financial_checks": checks,
        "failures": failures,
        "event_contexts": event_contexts,
        "relations": relations,
        "same_catalogue_period_relations": sum(r["same_catalogue_period"] for r in relations),
        "other_catalogue_period_relations": sum(not r["same_catalogue_period"] for r in relations),
        "prior_records_sha256": digest(prior_records),
        "prior_accepted_events_preserved": sum(
            bool(r.get("verified_fields")) for r in prior_records
        ),
        "new_financial_facts_accepted": 0,
        "source_unverified_events": 387,
        "account_rounding_certified": False,
        "active_inputs_changed": False,
        "complete_archive_relationships_reviewed": False,
        "whole_goal_complete": False,
    }
    save(BASE / "remaining_amendment_review.json", result, immutable=True)
    print(
        result["status"],
        len(checks),
        "financial reader checks;",
        len(failures),
        "failures",
        flush=True,
    )
    require(not failures, "Scoped financial review remains incomplete; retain original evidence")


if __name__ == "__main__":
    main()
