"""Check reviewed amendment terms and separate their financial periods."""

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
NOTES = BASE / "financial_amendment_notes.json"
FIELDS = Path("configs/protocols/residual_field_review.json")
PROTOCOL = Path("configs/protocols/supplemental_amendment_sources.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def prose(value):
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)


def numeric_inventory(value):
    return Counter(re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", unicodedata.normalize("NFKC", value)))


def main():
    notes = read(NOTES)
    manifest = read("configs/integrity_manifest.json")["files"]
    require(sha(FIELDS) == manifest[FIELDS.as_posix()], "Candidate scope changed")
    inputs = [
        PROTOCOL,
        FIELDS,
        NOTES,
        BASE / "source_text.json.gz",
        BASE / "related_source_text.json.gz",
    ]
    for path in inputs[3:]:
        require(sha(path) == manifest[path.as_posix()], "Frozen text changed")
    archive = load_gzip(inputs[3])["documents"]
    archive.update(load_gzip(inputs[4])["documents"])
    candidates = read(FIELDS)["records"]
    documents, checks, relations = {}, [], []
    numeric_pairs, numeric_tokens = 0, 0
    for note in notes["records"]:
        source = note["source"]
        path, document_id = source["path"], note["id"]
        require(archive[path]["source"] == source, "Source metadata differs")
        require(sha(path) == source["sha256"], "PDF differs")
        require(sha(source["receipt"]) == source["receipt_sha256"], "Receipt differs")
        with pymupdf.open(path) as pdf:
            mupdf_pages = [page.get_text(sort=False) for page in pdf]
        pypdf_pages = [page.extract_text() for page in PdfReader(path).pages]
        readers = {"pypdf": pypdf_pages, "mupdf_unsorted": mupdf_pages}
        require(
            pypdf_pages == archive[path]["readers"]["pypdf"]["pages"], "Frozen pypdf text differs"
        )
        require(len(pypdf_pages) == len(mupdf_pages) == len(note["all_text_pages_read"]), "Pages")
        for page, pair in enumerate(zip(pypdf_pages, mupdf_pages), 1):
            left, right = map(numeric_inventory, pair)
            require(left == right, f"Reader numeric inventory differs: {document_id}:{page}")
            numeric_pairs += 1
            numeric_tokens += sum(left.values())
        for image in note["observed_pages"]:
            require(image["full_page_actually_inspected"], "Unobserved page")
            require(sha(image["image"]) == image["image_sha256"], "Observed image differs")
        cash = Decimal(note["new_cash_per_share"])
        eligible = Decimal(note["new_eligible_shares"])
        total = Decimal(note["printed_new_aggregate"].replace(",", ""))
        require(
            (cash * eligible).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == total,
            "Aggregate",
        )
        if note["symbol"] == "sh601006":
            intended = Decimal(note["printed_old_aggregate"].replace(",", ""))
            require(
                (intended / eligible).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP) == cash,
                "Cash rounding",
            )
        for reader, pages in readers.items():
            text = [prose(p) for p in pages]
            require(re.search(r"(?:证券|股票)代码:\s*" + note["symbol"][2:], text[0]), "Issuer")
            require(re.search(note["period_pattern"], text[0]), "Body period")
            current = text[note["financial_page"] - 1]
            if note["symbol"] == "sh601006":
                pattern = r"每股派发现金红利\s*(\d+(?:\.\d+)?)\s*元"
                values = [Decimal(v) for v in re.findall(pattern, current)]
                pair = re.search(
                    r"每股派发现金红利由\s*([\d.]+)\s*元\s*\(含税\)\s*调整为\s*([\d.]+)", text[0]
                )
                require(
                    pair
                    and list(map(Decimal, pair.groups()))
                    == [Decimal(note["old_cash_per_share"]), cash],
                    "Cash change pair",
                )
                require(
                    re.search(r"公司将在权益分派实施公告中明确.*?股权登记日", current, re.S),
                    "Deferred record date",
                )
            else:
                pattern = r"每\s*10\s*股派发现金红利(?:人民币)?\s*(\d+(?:\.\d+)?)\s*元"
                values = [Decimal(v) / 10 for v in re.findall(pattern, current)]
            require(values and all(v == cash for v in values), "Current gross cash wording")
            require(note["printed_old_aggregate"] in "\n".join(text), "Original aggregate text")
            require(note["printed_new_aggregate"] in current, "New aggregate text")
            eligible_printed = f"{int(note['new_eligible_shares']):,}"
            require(eligible_printed in current, "Eligible base text")
            if note["capitalization_per_share"]:
                cap = re.findall(r"每\s*10\s*股(?:以资本公积金)?转增\s*(\d+)\s*股", current)
                require(
                    cap
                    and all(
                        Decimal(c) / 10 == Decimal(note["capitalization_per_share"]) for c in cap
                    ),
                    "Capitalization",
                )
            if note["explicit_stock_record_date"]:
                year, month, day = note["explicit_stock_record_date"].split("-")
                date = rf"{year}\s*年\s*{int(month)}\s*月\s*{int(day)}\s*日"
                require(
                    re.search(r"股权登记日\s*\(\s*(?:即\s*)?" + date + r"\s*\)", current),
                    "Explicit stock record date",
                )
            checks.append(
                {
                    "id": document_id,
                    "reader": reader,
                    "cash": str(cash),
                    "status": "MATCHED_REVIEWED_TERMS",
                }
            )
        for candidate in candidates:
            if candidate["symbol"] != note["symbol"]:
                continue
            primary = archive[candidate["source"]["path"]]["source"]
            same = candidate["ex_date"] == note["same_period_candidate_ex_date"]
            require(
                bool(re.search(note["period_pattern"], primary["title"])) == same,
                "Candidate period binding",
            )
            if same:
                provider_cash = Decimal(candidate["provider"]["cash_per_share"])
                require(
                    abs(provider_cash - cash) <= Decimal("1e-12"), "Candidate gross cash differs"
                )
                require(
                    source["catalog_date"] <= primary["catalog_date"],
                    "Late amendment is retrospective",
                )
                if note["capitalization_per_share"]:
                    require(
                        Decimal(candidate["provider"]["capitalization_per_share"])
                        == Decimal(note["capitalization_per_share"]),
                        "Candidate capitalization",
                    )
                if note["explicit_stock_record_date"]:
                    require(
                        candidate["source_identity_fields"]["record_date"]
                        == note["explicit_stock_record_date"],
                        "Candidate stock record date",
                    )
            relations.append(
                {
                    "id": document_id,
                    "symbol": candidate["symbol"],
                    "ex_date": candidate["ex_date"],
                    "primary": candidate["source"],
                    "primary_title": primary["title"],
                    "known_at_preserved": candidate["known_at"],
                    "amendment_period": note["period"],
                    "disposition": "SAME_PERIOD_CURRENT_TERMS_AGREE"
                    if same
                    else "OTHER_FINANCIAL_PERIOD",
                    "event_accepted": False,
                }
            )
        documents[document_id] = {"source": source, "readers": readers}
    require(len(documents) == 10 and numeric_pairs == 22, "Full-reading scope")
    require(len(checks) == 20 and len(relations) == 22, "Binding scope")
    require(
        sum(r["disposition"] == "SAME_PERIOD_CURRENT_TERMS_AGREE" for r in relations) == 6,
        "Same-period scope",
    )
    text_path = BASE / "financial_amendment_text.json.gz"
    require(not text_path.exists(), "Refusing to replace reader evidence")
    dump_gzip(text_path, {"documents": documents})
    result = {
        "status": "PASS_SCOPED_FINANCIAL_AMENDMENT_REVIEW",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "dependencies": {p.as_posix(): sha(p) for p in inputs},
        "reader_versions": {"pypdf": version("pypdf"), "pymupdf": version("PyMuPDF")},
        "text_output": {"path": text_path.as_posix(), "sha256": sha(text_path)},
        "documents_read_in_full": 10,
        "pages_read_in_full": 22,
        "whole_pages_observed": 11,
        "numeric_page_pairs": numeric_pairs,
        "numeric_tokens_per_reader": numeric_tokens,
        "reader_checks": checks,
        "event_relations": relations,
        "same_period_candidate_relations": 6,
        "other_period_relations": 16,
        "new_financial_facts_accepted": 0,
        "active_inputs_changed": False,
        "full_archive_relationships_complete": False,
        "all_residual_events_still_unverified": 387,
        "account_rounding_certified": False,
        "whole_goal_complete": False,
    }
    save(BASE / "financial_amendment_review.json", result, immutable=True)
    print(result["status"], "10 notices / 22 pages / 6 same-period and 16 other-period relations")


if __name__ == "__main__":
    main()
