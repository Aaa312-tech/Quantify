"""Bind full companion readings to raw sources and the independent field review."""

import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from read_primary_financials import CASH, normalize

from quantfolio.storage import digest, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/residual_notice_relations.json")
NOTES = BASE / "companion_notice_notes.json"
PAGE_NOTES = BASE / "companion_page_notes.json"
PRICE = re.compile(
    r"由[^0-9。；;]{0,24}(?P<before>\d+(?:\.\d+)?)元/股"
    r"[^0-9。；;]{0,48}(?:调整为|调整至|调整到)"
    r"[^0-9。；;]{0,12}(?P<after>\d+(?:\.\d+)?)元/股"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def date_pattern(value):
    year, month, day = map(int, value.split("-"))
    return re.compile(rf"{year}年0?{month}月0?{day}日")


def reader_check(pages, note, symbol):
    normalized = [normalize(page) for page in pages]
    identity = bool(re.search(rf"证券代码:{symbol[2:]}", normalized[0]))
    cash = []
    prices = []
    dates = []
    for number, text in enumerate(normalized, 1):
        for match in CASH.finditer(text):
            value = Decimal(match["amount"]) / (10 if match["ten"] else 1)
            if value == Decimal(note["cash_per_share"]) and match["currency"] == "元":
                cash.append(
                    {
                        "page": number,
                        "claim": match[0],
                        "context": text[max(0, match.start() - 180) : match.end() + 160],
                    }
                )
        for match in PRICE.finditer(text):
            for change in note["price_changes"]:
                if Decimal(match["before"]) == Decimal(change["before"]) and Decimal(
                    match["after"]
                ) == Decimal(change["after"]):
                    prices.append(
                        {
                            "page": number,
                            "instrument": change["instrument"],
                            "claim": match[0],
                            "context": text[max(0, match.start() - 90) : match.end() + 180],
                        }
                    )
        for match in date_pattern(note["ex_date"]).finditer(text):
            dates.append(
                {"page": number, "context": text[max(0, match.start() - 60) : match.end() + 100]}
            )
    instruments = {change["instrument"] for change in note["price_changes"]}
    found = {item["instrument"] for item in prices}
    return {
        "page_count": len(pages),
        "page_text_sha256": [digest(page) for page in pages],
        "labelled_stock_identity": identity,
        "gross_cash_passages": cash,
        "directional_price_passages": prices,
        "event_date_contexts": dates,
        "status": "PASS" if identity and cash and dates and found == instruments else "FAIL",
        "interpretation": "Context retrieval supports the recorded full-text semantic review; "
        "number occurrence alone is not event or financial acceptance.",
    }


def main():
    protocol = read(PROTOCOL)
    for item in protocol["inputs"]:
        require(sha(item["path"]) == item["sha256"], f"Changed input: {item['path']}")
    narrative = load_gzip(BASE / "primary_financial_text.json.gz")["documents"]
    archive = load_gzip(BASE / "source_text.json.gz")["documents"]
    archive.update(load_gzip(BASE / "related_source_text.json.gz")["documents"])
    require(len(archive) == 1135, "Received document inventory changed")
    prior = read(BASE / "financial_source_review.json")
    contexts = {Path(row["source"]["source"]).stem: row for row in prior["companion_contexts"]}
    notes = read(NOTES)["records"]
    require(
        len(notes) == 41 and {n["id"] for n in notes} == set(contexts),
        "Companion manual review scope differs",
    )
    candidates = {
        (r["symbol"], r["ex_date"]): r
        for r in read("configs/protocols/residual_field_review.json")["records"]
    }
    records, failures = [], []
    for note in notes:
        context = contexts[note["id"]]
        source = context["source"]
        path = source["source"]
        metadata = archive[path]["source"]
        require(sha(path) == source["sha256"] == metadata["sha256"], "Changed companion PDF")
        require(sha(metadata["receipt"]) == metadata["receipt_sha256"], "Changed receipt")
        require(len(source["records"]) == 1, "Companion maps to multiple candidate events")
        event = source["records"][0]
        require(not event["primary"], "Primary misclassified as companion")
        key = event["symbol"], event["ex_date"]
        candidate = candidates[key]
        identity = candidate["source_identity_fields"]
        require(note["ex_date"] == key[1], "Manual event date mismatch")
        require(
            Decimal(note["cash_per_share"]) == Decimal(identity["cash_per_share"]),
            f"Companion current recipient cash differs: {key}",
        )
        if note["record_date"]:
            require(note["record_date"] == identity["record_date"], "Record date conflict")
        if "capitalization_per_share" in note:
            require(
                Decimal(note["capitalization_per_share"])
                == Decimal(candidate["provider"]["capitalization_per_share"]),
                "Capitalization reference substituted for recipient entitlement",
            )
        primary = candidate["source"]
        require(sha(primary["path"]) == primary["sha256"], "Changed primary")
        require(sha(primary["receipt"]) == primary["receipt_sha256"], "Changed primary receipt")
        checks = {
            reader: reader_check(pages, note, key[0])
            for reader, pages in narrative[path]["readers"].items()
        }
        require(set(checks) == {"pypdf", "mupdf_unsorted"}, "Independent reader missing")
        for reader, check in checks.items():
            if check["status"] != "PASS":
                failures.append(
                    {
                        "id": note["id"],
                        "reader": reader,
                        "identity": check["labelled_stock_identity"],
                        "cash": len(check["gross_cash_passages"]),
                        "prices": len(check["directional_price_passages"]),
                        "dates": len(check["event_date_contexts"]),
                    }
                )
        late = metadata["catalog_date"] > key[1]
        records.append(
            {
                "symbol": key[0],
                "ex_date": key[1],
                "companion": metadata,
                "role": context["role"],
                "manual_review": note,
                "primary": primary,
                "primary_known_at": candidate["known_at"],
                "catalogued_after_ex_date": late,
                "temporal_use": "RETROSPECTIVE_ONLY" if late else "CATALOGUE_DATE_NOT_EXACT_TIME",
                "reader_checks": checks,
                "status": "SCOPED_REVIEW_WITH_RETAINED_LIMITS",
            }
        )
    observations = []
    for observed in read(PAGE_NOTES)["observations"]:
        source = contexts[observed["id"]]["source"]
        image = Path(".git/companion_page_review") / (
            f"{observed['id']}_page_{observed['page']:02d}.png"
        )
        require(image.exists(), "Observed render missing")
        observations.append(
            {
                **observed,
                "source": source["source"],
                "source_sha256": source["sha256"],
                "render_sha256": sha(image),
            }
        )
    result = {
        "status": "PASS_SCOPED_COMPANION_REVIEW" if not failures else "READER_BINDING_FAILED",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "protocol_sha256": sha(PROTOCOL),
        "manual_notes_sha256": sha(NOTES),
        "page_notes_sha256": sha(PAGE_NOTES),
        "reader_helpers_sha256": sha(BASE / "read_primary_financials.py"),
        "prior_financial_review_sha256": sha(BASE / "financial_source_review.json"),
        "companion_count": len(records),
        "full_text_pages": sum(r["reader_checks"]["pypdf"]["page_count"] for r in records),
        "reader_checks": sum(len(r["reader_checks"]) for r in records),
        "role_counts": dict(Counter(r["role"] for r in records)),
        "late_companions": [r["companion"]["id"] for r in records if r["catalogued_after_ex_date"]],
        "whole_pages_observed": observations,
        "records": records,
        "failures": failures,
        "facts_accepted": 0,
        "source_phase_complete": False,
        "limitations": [
            "The 1135-document amendment review and all 387 event dispositions remain pending.",
            "Exact publication times and public archive completeness are not certified.",
            "One repurchase notice has no explicit adjustment-effective date; preserve null.",
            "Two companions lack an explicit stock record date; primary dates remain authoritative.",
            "Conflicting historical wording, rounding and repurchase-budget text remain recorded.",
            "No active data, source-acceptance records, models, accounts or paper plans change.",
        ],
    }
    save(BASE / "companion_relations.json", result, immutable=True)
    print(
        {
            key: result[key]
            for key in (
                "status",
                "companion_count",
                "full_text_pages",
                "reader_checks",
                "late_companions",
                "failures",
            )
        }
    )
    require(not failures, "Companion reader evidence incomplete; preserve this diagnostic")


if __name__ == "__main__":
    main()
