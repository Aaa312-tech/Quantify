"""Bind the remaining candidate-issuer amendments, keeping distinct schemes separate."""

import re
import unicodedata
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from importlib.metadata import version
from pathlib import Path

import pymupdf
from pypdf import PdfReader

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/candidate_amendment_relations.json")
NOTES = BASE / "candidate_amendment_notes.json"
FIELDS = Path("configs/protocols/residual_field_review.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def prose(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)


def section(text, scheme, role):
    if f"{role}_section_start" not in scheme:
        return text
    starts = list(re.finditer(scheme[f"{role}_section_start"], text))
    require(len(starts) == 1, f"Ambiguous {role} section")
    remaining = text[starts[0].end() :]
    end = re.search(scheme[f"{role}_section_end"], remaining)
    require(end, f"Missing {role} section boundary")
    return remaining[: end.start()]


def period_pattern(scheme):
    suffix = {
        "annual": r"\s*年?\s*年度",
        "half_year": r"\s*年半年度",
        "first_three_quarters": r"\s*年前三季度",
        "special": r"\s*年回报股东特别分红",
    }[scheme["period_kind"]]
    return str(scheme["year"]) + suffix


def gross_cash(text):
    patterns = [
        r"每\s*(10)?\s*股派发(?:特别)?现金(?:红利|股利)(?:人民币)?\s*(\d+(?:\.\d+)?)\s*元(?:人民币)?\s*\(含税\)",
        r"派发每\s*(10)\s*股现金股利人民币\s*(\d+(?:\.\d+)?)\s*元\s*\(含税\)",
    ]
    return [
        {"cash": Decimal(value) / Decimal(unit or 1), "text": match.group(0)}
        for pattern in patterns
        for match in re.finditer(pattern, text)
        for unit, value in [match.groups()]
    ]


def main():
    protocol, notes = read(PROTOCOL), read(NOTES)
    for item in protocol["inputs"]:
        require(sha(item["path"]) == item["sha256"], "Registered dependency changed")
    archive = load_gzip(BASE / "source_text.json.gz")["documents"]
    archive.update(load_gzip(BASE / "related_source_text.json.gz")["documents"])
    registered = {r["source"]["id"]: r for r in protocol["sources"]}
    require({r["id"] for r in notes["records"]} == set(registered), "Document scope")
    candidates = read(FIELDS)["records"]
    documents, checks, relations, arithmetic, limitations = {}, [], [], [], []
    numeric_pairs = numeric_tokens = observed = 0
    for note in notes["records"]:
        document_id, source = note["id"], note["source"]
        require(
            registered[document_id]["source"] == source == archive[source["path"]]["source"],
            "Source metadata",
        )
        require(sha(source["path"]) == source["sha256"], "PDF changed")
        require(sha(source["receipt"]) == source["receipt_sha256"], "Receipt changed")
        pypdf_pages = [p.extract_text() for p in PdfReader(source["path"]).pages]
        with pymupdf.open(source["path"]) as pdf:
            mupdf_pages = [p.get_text(sort=False) for p in pdf]
        require(
            pypdf_pages == archive[source["path"]]["readers"]["pypdf"]["pages"],
            "Prior extraction changed",
        )
        require(
            len(pypdf_pages) == len(mupdf_pages) == registered[document_id]["page_count"],
            "Page scope",
        )
        require(
            note["full_text_pages_read"] == list(range(1, len(pypdf_pages) + 1)),
            "Full reading scope",
        )
        readers = {"pypdf": pypdf_pages, "mupdf_unsorted": mupdf_pages}
        for left, right in zip(pypdf_pages, mupdf_pages):
            inventories = [
                Counter(re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", unicodedata.normalize("NFKC", t)))
                for t in [left, right]
            ]
            require(inventories[0] == inventories[1], "Reader numeric inventory differs")
            numeric_pairs += 1
            numeric_tokens += sum(inventories[0].values())
        for image in note["observed_pages"]:
            require(
                image["full_page_actually_observed"]
                and sha(image["image"]) == image["image_sha256"],
                "Observed page changed",
            )
            observed += 1
        require(
            note["explicit_dividend_dates"] == {},
            "No stock dates are accepted from these amendments",
        )
        for scheme in note["schemes"]:
            cash = Decimal(scheme["gross_cash_per_share"])
            eligible = Decimal(scheme["eligible_shares"])
            total = Decimal(scheme["printed_new_aggregate"].replace(",", ""))
            require(
                Decimal(scheme["printed_cash_per_unit"]) / scheme["printed_cash_unit_shares"]
                == cash,
                "Cash unit",
            )
            calculated = (cash * eligible).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            require(calculated == total, f"Disclosed gross aggregate differs: {document_id}")
            row = {
                "id": document_id,
                "period": scheme["period"],
                "declared_cash": str(cash),
                "eligible_shares": str(eligible),
                "computed_aggregate": str(calculated),
            }
            if document_id == "1220173816":
                ratio = Decimal(scheme["printed_old_aggregate"].replace(",", "")) / eligible
                half_up = ratio.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
                require(
                    half_up == Decimal("0.98973") and cash == Decimal("0.98974"),
                    "Observed ratio difference changed",
                )
                row.update(
                    intended_total_ratio=str(ratio),
                    conventional_half_up_five_places=str(half_up),
                    ratio_rounding_certified=False,
                )
                limitations.append(
                    {
                        "id": document_id,
                        "kind": "UNSPECIFIED_RATIO_ROUNDING",
                        "finding": "Source says keep five decimal places but prints 0.98974; exact quotient is about 0.9897333 and usual half-up gives 0.98973. Declared cash and recomputed new aggregate remain separate from the intended-total ratio. No cash is changed.",
                    }
                )
            if scheme["capitalization_per_share"]:
                cap = Decimal(scheme["capitalization_per_share"])
                observation = note["capitalization_observation"]
                difference = eligible * (1 + cap) - Decimal(observation["printed_post_total"])
                require(
                    difference == Decimal(observation["exact_ratio_post_minus_printed"]),
                    "Printed post-total difference",
                )
                if "printed_added_shares" in observation:
                    require(
                        eligible * cap - Decimal(observation["printed_added_shares"])
                        == Decimal(observation["exact_ratio_added_minus_printed"]),
                        "Printed added-share difference",
                    )
                row.update(capitalization_observation=observation, account_rounding_certified=False)
            arithmetic.append(row)
            for reader, pages in readers.items():
                text = [prose(p) for p in pages]
                require(re.search(r"证券代码:\s*" + note["symbol"][2:], text[0]), "Issuer identity")
                require(re.search(period_pattern(scheme), text[0]), "Financial period")
                cash_text = section(text[scheme["cash_page"] - 1], scheme, "cash")
                terms = section(text[scheme["terms_page"] - 1], scheme, "terms")
                evidence = gross_cash(cash_text)
                require(
                    evidence and all(r["cash"] == cash for r in evidence),
                    f"Current cash wording: {document_id}:{reader}:{scheme['period']}",
                )
                require(scheme["printed_new_aggregate"] in terms, "Scoped new aggregate text")
                require(
                    f"{int(scheme['eligible_shares']):,}" in terms, "Scoped eligible-share text"
                )
                if scheme["printed_old_aggregate"]:
                    require(
                        scheme["printed_old_aggregate"] in "\n".join(text), "Prior aggregate text"
                    )
                if scheme["capitalization_per_share"]:
                    amounts = re.findall(
                        r"每\s*(10)?\s*股(?:以资本公积金)?转增\s*(\d+(?:\.\d+)?)\s*股", terms
                    )
                    require(
                        amounts
                        and all(
                            Decimal(v) / Decimal(u or 1)
                            == Decimal(scheme["capitalization_per_share"])
                            for u, v in amounts
                        ),
                        "Capitalization wording",
                    )
                    require(
                        f"{int(note['capitalization_observation']['printed_post_total']):,}"
                        in terms,
                        "Post-capitalization total wording",
                    )
                checks.append(
                    {
                        "id": document_id,
                        "reader": reader,
                        "period": scheme["period"],
                        "cash": str(cash),
                        "gross_quotes": [e["text"] for e in evidence],
                        "status": "DECLARED_CURRENT_TERMS_AGREE",
                    }
                )
        for candidate in candidates:
            if candidate["symbol"] != note["symbol"]:
                continue
            primary = archive[candidate["source"]["path"]]["source"]
            matching = [
                s for s in note["schemes"] if re.search(period_pattern(s), primary["title"])
            ]
            expected = [
                s
                for s in note["schemes"]
                if s["same_period_candidate_ex_date"] == candidate["ex_date"]
            ]
            require(matching == expected and len(matching) <= 1, "Period binding differs")
            if matching:
                scheme = matching[0]
                require(
                    abs(
                        Decimal(candidate["provider"]["cash_per_share"])
                        - Decimal(scheme["gross_cash_per_share"])
                    )
                    <= Decimal("1e-12"),
                    "Candidate gross cash",
                )
                require(source["catalog_date"] <= primary["catalog_date"], "Late amendment")
                if scheme["capitalization_per_share"]:
                    require(
                        Decimal(candidate["provider"]["capitalization_per_share"])
                        == Decimal(scheme["capitalization_per_share"]),
                        "Candidate capitalization",
                    )
            relations.append(
                {
                    "id": document_id,
                    "symbol": candidate["symbol"],
                    "ex_date": candidate["ex_date"],
                    "primary": candidate["source"],
                    "primary_title": primary["title"],
                    "primary_known_at_preserved": candidate["known_at"],
                    "matching_period": matching[0]["period"] if matching else None,
                    "disposition": "SAME_PERIOD_DECLARED_TERMS_AGREE"
                    if matching
                    else "OTHER_FINANCIAL_PERIOD",
                    "event_accepted": False,
                }
            )
        if document_id in {"1223766205", "1225311881", "1224768212", "1223633214"}:
            limitations.append(
                {
                    "id": document_id,
                    "kind": "OBSERVED_SOURCE_LABEL_OR_AGGREGATE_LIMIT",
                    "finding": note["finding"],
                }
            )
        documents[document_id] = {"source": source, "readers": readers}
    require(len(documents) == 14 and numeric_pairs == 35 and observed == 20, "Full evidence scope")
    require(
        len(arithmetic) == 15 and len(checks) == 30 and len(relations) == 22,
        "Scheme/reader/relation scope",
    )
    require(
        sum(r["disposition"] == "SAME_PERIOD_DECLARED_TERMS_AGREE" for r in relations) == 6,
        "Same-period scope",
    )
    combined = [r for r in arithmetic if r["id"] == "1223476478"]
    require(
        sum(Decimal(r["computed_aggregate"]) for r in combined) == Decimal("3842107646.75"),
        "Two-scheme total",
    )
    text_path = BASE / "candidate_amendment_text.json.gz"
    require(not text_path.exists(), "Refusing to replace reader evidence")
    dump_gzip(text_path, {"documents": documents})
    result = {
        "status": "PASS_SCOPED_RELATIONS_WITH_DISCLOSURE_LIMITS",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "protocol_sha256": sha(PROTOCOL),
        "notes_sha256": sha(NOTES),
        "dependencies": {item["path"]: item["sha256"] for item in protocol["inputs"]},
        "reader_versions": {"pypdf": version("pypdf"), "pymupdf": version("PyMuPDF")},
        "text_output": {"path": text_path.as_posix(), "sha256": sha(text_path)},
        "documents_read_in_full": 14,
        "pages_read_in_full": 35,
        "whole_pages_observed": 20,
        "schemes": 15,
        "numeric_page_pairs": numeric_pairs,
        "numeric_tokens_per_reader": numeric_tokens,
        "reader_checks": checks,
        "scheme_arithmetic": arithmetic,
        "disclosure_limitations": limitations,
        "event_relations": relations,
        "same_period_relations": 6,
        "other_period_relations": 16,
        "new_financial_facts_accepted": 0,
        "active_inputs_changed": False,
        "account_rounding_certified": False,
        "all_residual_events_still_unverified": 387,
        "full_archive_relationships_complete": False,
        "whole_goal_complete": False,
    }
    save(BASE / "candidate_amendment_review.json", result, immutable=True)
    print(result["status"], "14 notices / 35 pages / 15 schemes / 22 event relations")


if __name__ == "__main__":
    main()
