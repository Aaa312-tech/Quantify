"""Bind whole-page observations to independently checked financial statements."""

import re
import unicodedata
from collections import Counter
from pathlib import Path

import fitz
from read_primary_financials import DATE_PATTERNS, document_text, evidence, normalize, require

from quantfolio.storage import load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/residual_financial_scope.json")
NOTES = BASE / "financial_page_notes.json"


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def payment_boundary(source):
    path = source["source"]["source"]
    geometry = []
    with fitz.open(path) as pdf:
        for number in (2, 3):
            page = pdf[number - 1]
            words = page.get_text("words")
            footer = [
                word
                for word in words
                if word[4] == str(number)
                and word[1] > page.rect.height * 0.9
                and page.rect.width * 0.4 < word[0] < page.rect.width * 0.6
            ]
            require(len(footer) == 1, "Observed isolated footer not found")
            body = [word for word in words if word != footer[0]]
            require(max(word[3] for word in body) < footer[0][1] - 20, "Footer not isolated")
            geometry.append(
                {
                    "page": number,
                    "page_rectangle": list(page.rect),
                    "footer_word": list(footer[0]),
                    "last_body_word_bottom": max(word[3] for word in body),
                    "whole_page_words": [list(word) for word in words],
                }
            )
    checks = {}
    for reader, pages in source["readers"].items():
        parts = []
        for number in (2, 3):
            lines = [line for line in pages[number - 1].splitlines() if line.strip()]
            matches = [i for i, line in enumerate(lines) if compact(line) == str(number)]
            require(len(matches) == 1, "Footer text is not unique")
            require(matches[0] in (0, len(lines) - 1), "Footer is inside body reading order")
            parts.append(
                normalize("\n".join(line for i, line in enumerate(lines) if i != matches[0]))
            )
        joined = "".join(parts)
        matches = list(DATE_PATTERNS["cash_pay_date"].finditer(joined))
        require(len(matches) == 1, "Payment sentence not unique after scoped footer exclusion")
        match = matches[0]
        require(
            (match["year"], match["month"], match["day"]) == ("2024", "6", "26"),
            "Payment date conflict",
        )
        require(
            match.start() < len(parts[0]) < match.end(), "Sentence does not cross observed pages"
        )
        checks[reader] = {"value": "2024-06-26", "pages": [2, 3], "statement": match[0]}
    return {"source": source["source"], "geometry": geometry, "readers": checks}


def price_contexts(pages, role, dates):
    text, locations, _, _ = document_text(pages)
    keyword = r"转股价格" if role == "CONVERTIBLE_PRICE" else r"回购.{0,8}价格"
    passages = []
    for match in re.finditer(keyword, text):
        start, end = max(0, match.start() - 30), min(len(text), match.end() + 240)
        segment = text[start:end]
        if re.search(r"调整|调降|变更", segment) and re.search(r"\d+(?:\.\d+)?元", segment):
            passages.append(evidence(text, locations, start, end))
    date_checks = []
    for date in sorted(dates):
        year, month, day = (int(part) for part in date.split("-"))
        # Month/day optional zero is explicit; do not strip numeric cell boundaries.
        pattern = rf"(?<!\d){year}(?:年|/|-)0?{month}(?:月|/|-)0?{day}日?(?!\d)"
        mentions = []
        for match in re.finditer(pattern, text):
            start, end = max(0, match.start() - 100), min(len(text), match.end() + 100)
            mentions.append(evidence(text, locations, start, end))
        date_checks.append({"ex_date": date, "mentions": mentions})
    return {
        "price_adjustment_passages": passages,
        "event_date_mentions": date_checks,
        "status": "PRICE_AND_EVENT_CONTEXT_PRESENT"
        if passages and all(row["mentions"] for row in date_checks)
        else "PRICE_OR_EVENT_CONTEXT_REQUIRES_REVIEW",
        "scope": "Context inventory; title and numerical mentions alone do not certify that every clause is an unamended same-event price adjustment.",
    }


def main():
    protocol = read(PROTOCOL)
    for key in ("parent_protocol", "initial_checker", "initial_review", "initial_text"):
        require(sha(protocol[key]["path"]) == protocol[key]["sha256"], "Frozen dependency changed")
    output = BASE / "financial_closure.json"
    require(not output.exists(), "Refusing to overwrite source closure")
    sources = load_gzip(protocol["initial_text"]["path"])["documents"]
    by_id = {Path(path).stem: value for path, value in sources.items()}
    notes = read(NOTES)
    observed = {}
    for note in notes["observations"]:
        source = by_id[note["id"]]
        for number in note["pages"]:
            key = note["id"], number
            image = Path(f".git/financial_page_review/{note['id']}_page_{number:02}.png")
            require(image.is_file(), "Observed render missing")
            require(1 <= number <= len(source["readers"]["pypdf"]), "Page outside source")
            if key not in observed:
                observed[key] = {
                    "source": source["source"]["source"],
                    "source_sha256": source["source"]["sha256"],
                    "physical_page": number,
                    "whole_page_visually_observed": True,
                    "render_sha256": sha(image),
                    "renderer": "pdftoppm -r 125 -png -singlefile",
                    "observations": [],
                }
            observed[key]["observations"].append(note["observation"])
    require(len(observed) == 46, "Whole-page inventory differs from observed scope")
    manual = {row["id"]: row for row in notes["role_bindings"]}
    roles, role_counts, companions = [], Counter(), []
    for path, source in sources.items():
        require(sha(path) == source["source"]["sha256"], "PDF changed")
        doc_id = Path(path).stem
        identities = source["identities"]
        if doc_id in manual:
            binding = manual[doc_id]
            require((doc_id, 1) in observed, "Manual title lacks full-page inspection")
            for reader, pages in source["readers"].items():
                first = compact(pages[0])
                require(compact(binding["issuer"]) in first, "Printed issuer mismatch")
                require(compact(binding["title"]) in first, "Printed title mismatch")
                require(
                    binding["code"] in identities[reader]["labelled_security_codes"],
                    "Labelled code mismatch",
                )
            role = binding["role"]
            method = "OBSERVED_EXACT_PRINTED_ISSUER_AND_TITLE"
        else:
            require(
                all(
                    x["issuer_matches"] and x["exact_first_page_title"] for x in identities.values()
                ),
                "Unreviewed identity exception",
            )
            values = {row["role"] for row in identities.values()}
            require(len(values) == 1 and "REQUIRES_REVIEW" not in values, "Unreviewed title role")
            role = values.pop()
            method = "TWO_READER_EXACT_LABELLED_IDENTITY_AND_TITLE"
        primary = any(record["primary"] for record in source["source"]["records"])
        require(
            role == "IMPLEMENTATION"
            if primary
            else role in {"REPURCHASE_PRICE", "CONVERTIBLE_PRICE"},
            "Candidate role conflicts with registered primary",
        )
        role_counts[role] += 1
        roles.append(
            {
                "source": source["source"],
                "role": role,
                "method": method,
                "catalogue_title_preserved": source["source"]["catalogue_title"],
                "manual_binding": manual.get(doc_id),
            }
        )
        if not primary:
            dates = {record["ex_date"] for record in source["source"]["records"]}
            companions.append(
                {
                    "source": source["source"],
                    "role": role,
                    "readers": {
                        reader: price_contexts(pages, role, dates)
                        for reader, pages in source["readers"].items()
                    },
                }
            )
    boundary = payment_boundary(by_id["1220401550"])
    require(
        all(("1220401550", p) in observed for p in (2, 3)), "Payment boundary not visually observed"
    )
    initial = read(protocol["initial_review"]["path"])
    cash_file = BASE / "financial_scope_review.json"
    cash = read(cash_file)
    require(cash["protocol_sha256"] == sha(PROTOCOL), "Cash check has different protocol")
    require(
        cash["checker_sha256"] == sha(BASE / "review_financial_scope.py"), "Cash checker changed"
    )
    require(
        cash["check_counts"] == {"CURRENT_GROSS_CASH_AGREES": 190}, "Current cash exceptions remain"
    )
    require(
        len(cash["records"]) == 95 and len(cash["changed_checks"]) == 26, "Cash inventory mismatch"
    )
    initial_keys = {(row["symbol"], row["ex_date"]) for row in initial["records"]}
    require(
        initial_keys == {(row["symbol"], row["ex_date"]) for row in cash["records"]},
        "Cash event scope changed",
    )
    field_checks, counts = [], Counter()
    for record in initial["records"]:
        for check in record["date_checks"]:
            status = check["status"]
            if status == "NARRATIVE_REQUIRES_REVIEW":
                require(
                    (record["symbol"], record["ex_date"], check["field"])
                    == ("sz000783", "2024-06-26", "cash_pay_date"),
                    "Unexpected date exception",
                )
                status = "WHOLE_PAGES_AND_FOOTER_GEOMETRY_AGREE"
            counts[status] += 1
            field_checks.append(
                {
                    "symbol": record["symbol"],
                    "ex_date": record["ex_date"],
                    "field": check["field"],
                    "value": check["expected"],
                    "method": status,
                }
            )
    require(
        dict(counts)
        == {
            "PREVIOUS_INDEPENDENT_GRID_AGREES": 245,
            "TWO_READER_LABELLED_NARRATIVE_AGREES": 59,
            "WHOLE_PAGES_AND_FOOTER_GEOMETRY_AGREE": 1,
        },
        "Date scope incomplete",
    )
    save(
        output,
        {
            "status": "PRIMARY_FINANCIAL_FIELDS_CHECKED_RELATED_NOTICE_REVIEW_PENDING",
            "completed_at": now(),
            "checker_sha256": sha(__file__),
            "protocol_sha256": sha(PROTOCOL),
            "page_notes_sha256": sha(NOTES),
            "cash_scope_review_sha256": sha(cash_file),
            "initial_review_sha256": sha(protocol["initial_review"]["path"]),
            "whole_pages": list(observed.values()),
            "role_bindings": roles,
            "role_counts": dict(role_counts),
            "companion_contexts": companions,
            "payment_boundary": boundary,
            "date_field_checks": field_checks,
            "date_method_counts": dict(counts),
            "gross_cash_events_checked": 95,
            "gross_cash_reader_checks": 190,
            "facts_accepted": 0,
            "source_phase_complete": False,
            "limitations": [
                "This closes the registered primary per-share gross-cash and date checks, not all corporate-action terms or aggregate/account rounding.",
                "Companion title/issuer roles are bound; body context inventories still need same-event, amendment and contradictory-clause review.",
                "All 1135 received related documents and all 387 residual event dispositions remain in the source acceptance scope; no financial fields have been accepted or integrated.",
                "The received archive and catalogue availability convention do not establish complete public coverage or exact original publication timestamps.",
            ],
        },
        immutable=True,
    )
    companion_counts = Counter(
        result["status"] for row in companions for result in row["readers"].values()
    )
    print(
        "FIELD_CLOSURE",
        dict(counts),
        "cash",
        190,
        "whole_pages",
        len(observed),
        "roles",
        dict(role_counts),
        flush=True,
    )
    print("COMPANION_CONTEXTS", dict(companion_counts), flush=True)


if __name__ == "__main__":
    main()
