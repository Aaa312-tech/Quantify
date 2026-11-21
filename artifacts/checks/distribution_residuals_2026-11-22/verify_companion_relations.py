"""Verify the observed price headers and cash boundary without altering source facts."""

import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from check_companion_relations import reader_check, require
from read_primary_financials import CASH, normalize

from quantfolio.storage import digest, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/residual_notice_relations.json")
SCOPE = BASE / "companion_binding_scope.json"
PAIR = re.compile(
    r"调整前转股价格:(?P<before>\d+(?:\.\d+)?)元/股"
    r"[^0-9]{0,12}调整后转股价格:(?P<after>\d+(?:\.\d+)?)元/股"
)


def close_reader(pages, note, symbol, scope):
    check = reader_check(pages, note, symbol)
    if note["id"] in scope["first_page_labelled_price_pairs"]:
        matches = list(PAIR.finditer(normalize(pages[0])))
        require(len(matches) == 1 and len(note["price_changes"]) == 1, "Ambiguous price header")
        match, wanted = matches[0], note["price_changes"][0]
        require(Decimal(match["before"]) == Decimal(wanted["before"]), "Before price differs")
        require(Decimal(match["after"]) == Decimal(wanted["after"]), "After price differs")
        check["labelled_price_header"] = {"page": 1, "text": match[0], **wanted}
    if note["id"] == scope["cross_page_cash"]["id"]:
        boundary = scope["cross_page_cash"]
        left, right = (normalize(pages[i - 1]) for i in boundary["pages"])
        require(left.endswith(boundary["left_page_ends_with"]), "Unexpected left boundary")
        require(right.startswith(boundary["right_page_starts_with"]), "Unexpected right boundary")
        matches = [m for m in CASH.finditer(left + right) if m.start() < len(left) < m.end()]
        require(len(matches) == 1, "Cash boundary is not unique")
        match = matches[0]
        value = Decimal(match["amount"]) / (10 if match["ten"] else 1)
        require(
            value == Decimal(boundary["amount_per_share"]) == Decimal(note["cash_per_share"]),
            "Cross-page cash differs",
        )
        check["cross_page_cash"] = {
            "pages": boundary["pages"],
            "text": match[0],
            "per_share": str(value),
            "tokens_removed": 0,
        }
    good_cash = bool(check["gross_cash_passages"] or check.get("cross_page_cash"))
    good_price = bool(check["directional_price_passages"] or check.get("labelled_price_header"))
    require(
        check["labelled_stock_identity"]
        and good_cash
        and good_price
        and check["event_date_contexts"],
        f"Incomplete reader binding: {note['id']}",
    )
    check["initial_status"] = check["status"]
    check["status"] = "PASS_SCOPED_READER_BINDING"
    return check


def main():
    scope = read(SCOPE)
    for item in read(PROTOCOL)["inputs"]:
        require(sha(item["path"]) == item["sha256"], f"Changed input {item['path']}")
    require(scope["cash_tolerance"] == "1e-12", "Existing cash threshold changed")
    tolerance_source = read(scope["cash_tolerance_source"])
    require(
        any("unchanged Decimal 1e-12" in m for m in tolerance_source["methods"]),
        "Prior precision convention missing",
    )
    archive = load_gzip(BASE / "source_text.json.gz")["documents"]
    archive.update(load_gzip(BASE / "related_source_text.json.gz")["documents"])
    require(len(archive) == 1135, "Archive scope differs")
    texts = load_gzip(BASE / "primary_financial_text.json.gz")["documents"]
    notes = read(BASE / "companion_notice_notes.json")["records"]
    prior = read(BASE / "financial_source_review.json")
    companions = {Path(r["source"]["source"]).stem: r for r in prior["companion_contexts"]}
    candidates = {
        (r["symbol"], r["ex_date"]): r
        for r in read("configs/protocols/residual_field_review.json")["records"]
    }
    diagnostic = read(BASE / "companion_reader_diagnostic.json")
    require(
        diagnostic["reader_checker_sha256"] == sha(BASE / "check_companion_relations.py"),
        "Initial checker changed",
    )
    require(
        diagnostic["manual_notes_sha256"] == sha(BASE / "companion_notice_notes.json"),
        "Manual notes changed",
    )
    initial = {row["id"]: row["checks"] for row in diagnostic["records"]}
    require(
        len(notes) == 41 and {n["id"] for n in notes} == set(companions) == set(initial),
        "Manual or diagnostic scope differs",
    )
    records, representations, changed = [], {}, []
    for note in notes:
        context = companions[note["id"]]
        path = context["source"]["source"]
        source = archive[path]["source"]
        require(sha(path) == source["sha256"] == context["source"]["sha256"], "PDF changed")
        if note["id"] == scope["cross_page_cash"]["id"]:
            require(
                source["sha256"] == scope["cross_page_cash"]["source_sha256"],
                "Observed cross-page source changed",
            )
        require(sha(source["receipt"]) == source["receipt_sha256"], "Receipt changed")
        require(len(context["source"]["records"]) == 1, "Ambiguous companion event")
        event = context["source"]["records"][0]
        require(not event["primary"], "Companion role changed")
        candidate = candidates[event["symbol"], event["ex_date"]]
        identity = candidate["source_identity_fields"]
        actual = Decimal(note["cash_per_share"])
        stored = Decimal(identity["cash_per_share"])
        require(
            actual == Decimal(candidate["provider"]["cash_per_share"]),
            "Companion cash differs from exact provider value",
        )
        require(abs(actual - stored) <= Decimal(scope["cash_tolerance"]), "Cash conflict")
        if actual != stored:
            representations[note["id"]] = [note["cash_per_share"], identity["cash_per_share"]]
        require(note["ex_date"] == event["ex_date"], "Event date differs")
        if note["record_date"]:
            require(note["record_date"] == identity["record_date"], "Stock record date differs")
        if "capitalization_per_share" in note:
            require(
                Decimal(note["capitalization_per_share"])
                == Decimal(candidate["provider"]["capitalization_per_share"]),
                "Capitalization reference substituted for recipient entitlement",
            )
        primary = candidate["source"]
        require(sha(primary["path"]) == primary["sha256"], "Primary PDF changed")
        require(sha(primary["receipt"]) == primary["receipt_sha256"], "Primary receipt changed")
        checks = {}
        require(set(texts[path]["readers"]) == {"pypdf", "mupdf_unsorted"}, "Reader missing")
        for reader, pages in texts[path]["readers"].items():
            require(
                reader_check(pages, note, event["symbol"]) == initial[note["id"]][reader],
                "Initial reader diagnostic changed",
            )
            check = close_reader(pages, note, event["symbol"], scope)
            if check["initial_status"] != "PASS":
                changed.append({"id": note["id"], "reader": reader})
            checks[reader] = check
        late = source["catalog_date"] > event["ex_date"]
        records.append(
            {
                "symbol": event["symbol"],
                "ex_date": event["ex_date"],
                "companion": source,
                "role": context["role"],
                "manual_review": note,
                "primary": primary,
                "primary_known_at": candidate["known_at"],
                "catalogued_after_ex_date": late,
                "reader_checks": checks,
                "temporal_use": "RETROSPECTIVE_ONLY"
                if late
                else "CATALOGUE_DATE_NOT_EXACT_PUBLICATION_TIME",
            }
        )
    require(representations == scope["representation_differences"], "Representation scope changed")
    require(len(changed) == 14, "Reader failure inventory changed")
    late_ids = [r["companion"]["id"] for r in records if r["catalogued_after_ex_date"]]
    require(
        set(late_ids) == {"1220778753", "1223762841", "1224037809", "1224704135"},
        "Late-source inventory changed",
    )
    observed = []
    for note in read(BASE / "companion_page_notes.json")["observations"]:
        source = companions[note["id"]]["source"]
        render = Path(".git/companion_page_review") / f"{note['id']}_page_{note['page']:02d}.png"
        observed.append(
            {
                **note,
                "source": source["source"],
                "source_sha256": source["sha256"],
                "render_sha256": sha(render),
            }
        )
    dependencies = [
        PROTOCOL,
        SCOPE,
        BASE / "companion_notice_notes.json",
        BASE / "companion_page_notes.json",
        BASE / "check_companion_relations.py",
        BASE / "companion_relations.log",
        BASE / "companion_reader_diagnostic.json",
        BASE / "read_primary_financials.py",
        BASE / "financial_source_review.json",
    ]
    result = {
        "status": "PASS_SCOPED_COMPANION_REVIEW",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "dependencies": {p.as_posix(): sha(p) for p in dependencies},
        "companion_count": len(records),
        "full_text_pages": sum(r["reader_checks"]["pypdf"]["page_count"] for r in records),
        "reader_checks": sum(len(r["reader_checks"]) for r in records),
        "role_counts": dict(Counter(r["role"] for r in records)),
        "late_companions": late_ids,
        "representation_differences": representations,
        "closed_reader_failures": changed,
        "whole_pages_observed": observed,
        "records": records,
        "records_digest": digest(records),
        "facts_accepted": 0,
        "source_phase_complete": False,
        "limitations": [
            "All 1135 archive documents and all 387 residual event dispositions remain in scope.",
            "Amendment review is pending; no new distribution fields are accepted or integrated.",
            "One notice lacks an explicit adjustment-effective date; two lack explicit record dates.",
            "Historical wording conflicts, approximate references and repurchase budget errors remain.",
            "Source catalogue dates are not certified exact publication times; later sources stay later.",
        ],
    }
    save(BASE / "companion_review.json", result, immutable=True)
    print(
        {
            key: result[key]
            for key in (
                "status",
                "companion_count",
                "full_text_pages",
                "reader_checks",
                "role_counts",
                "late_companions",
            )
        }
    )


if __name__ == "__main__":
    main()
