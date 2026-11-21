"""Describe every residual source failure without changing financial acceptance."""

import re
import unicodedata
from collections import Counter
from pathlib import Path

from quantfolio.distributions import notice_role
from quantfolio.storage import load_gzip, now, read, save, sha


def values(terms, field):
    return sorted({row["value"] for row in terms.get(field, [])})


def boundary_lines(pages):
    result = []
    for number, raw in enumerate(pages, 1):
        lines = [line.strip() for line in unicodedata.normalize("NFKC", raw).splitlines()]
        occupied = [index for index, line in enumerate(lines) if line]
        for index in dict.fromkeys(occupied[:1] + occupied[-1:]):
            text = lines[index]
            for style, pattern in (
                ("paired_dash", r"[-—–]\s*(\d+)\s*[-—–]"),
                ("chinese_total", r"第\s*(\d+)\s*页\s*共\s*(\d+)\s*页"),
                ("fraction_total", r"(\d+)\s*/\s*(\d+)"),
                ("bare_number_not_removable", r"(\d+)"),
            ):
                match = re.fullmatch(pattern, text)
                if match:
                    result.append(
                        {
                            "page": number,
                            "line": index + 1,
                            "text": text,
                            "style": style,
                            "physical_page_agrees": int(match[1]) == number,
                            "physical_total_agrees": (
                                int(match[2]) == len(pages) if match.lastindex == 2 else None
                            ),
                        }
                    )
                    break
    return result


root = Path.cwd()
base = root / "artifacts/checks/distribution_residuals_2026-11-22"
protocol = read(root / "configs/protocols/distribution_residuals.json")
inventory = read(base / "document_inventory.json")
diagnostics = read(base / "initial_diagnostics.json")
texts = load_gzip(base / "source_text.json.gz")["documents"]
index_file = protocol["source_inputs"]["event_index"]
assert sha(index_file["path"]) == index_file["sha256"]
index = {(row["symbol"], row["ex_date"]): row for row in read(index_file["path"])["records"]}
events = {(row["symbol"], row["ex_date"]): row for row in inventory["events"]}
records, counts, role_counts = [], Counter(), Counter()
for row in diagnostics["records"]:
    key = row["symbol"], row["ex_date"]
    event = events[key]
    record = {
        "symbol": key[0],
        "ex_date": key[1],
        "prior_status": row["prior_status"],
        "selected_primary": event["selected_primary"],
        "readers": {},
        "candidate_roles": [],
        "source_disposition": "UNVERIFIED_PENDING_FIELD_REVIEW",
    }
    for reader, result in row["readers"].items():
        terms = result["terms"]
        field_values = {
            field: values(terms, field)
            for field in (
                "record_date",
                "ex_date",
                "cash_per_share",
                "unscoped_cash_per_share",
                "cash_pay_date",
                "share_list_date",
            )
        }
        missing = [
            field
            for field in ("record_date", "ex_date", "cash_per_share", "cash_pay_date")
            if not field_values[field]
        ]
        conflicts = [
            field
            for field in ("record_date", "ex_date", "cash_per_share", "cash_pay_date")
            if len(field_values[field]) > 1
        ]
        record["readers"][reader] = {
            "status": result["status"],
            "field_values_not_accepted": field_values,
            "missing": missing,
            "conflicts": conflicts,
        }
        counts[reader + ":" + result["status"]] += 1
    for candidate in index[key]["candidates"]:
        document = texts[candidate["source"]]
        assert document["source"]["sha256"] == candidate["source_sha256"]
        roles = {
            reader: notice_role(key[0], candidate, data["pages"])
            for reader, data in document["readers"].items()
        }
        record["candidate_roles"].append(
            {"source": candidate["source"], "id": candidate["id"], "readers": roles}
        )
        if not event["selected_primary"]:
            for reader, role in roles.items():
                role_counts[reader + ":" + role["reason"]] += 1
    records.append(record)

boundaries = {
    path: {reader: boundary_lines(data["pages"]) for reader, data in item["readers"].items()}
    for path, item in texts.items()
}
assert len(records) == len(events) == 387
assert {(r["symbol"], r["ex_date"]) for r in records} == set(events)
output = {
    "status": "LAYOUT_DIAGNOSTICS_NOT_ACCEPTANCE",
    "created_at": now(),
    "checker_sha256": sha(__file__),
    "source_sha256": sha(root / "src/quantfolio/distributions.py"),
    "inventory_sha256": sha(base / "document_inventory.json"),
    "initial_diagnostics_sha256": sha(base / "initial_diagnostics.json"),
    "source_text_sha256": sha(base / "source_text.json.gz"),
    "events": len(records),
    "documents": len(texts),
    "records": records,
    "boundary_lines": boundaries,
    "reader_status_counts": dict(counts),
    "unselected_candidate_role_counts": dict(role_counts),
    "facts_accepted": 0,
    "limitations": [
        "A text match does not verify financial meaning or change source selection.",
        "Existing date, monetary, source timing and provider gates remain in force.",
        "Only the separately recorded sample pages have been visually inspected.",
    ],
}
save(base / "layout_diagnostics.json", output, immutable=True)
print("LAYOUT_DIAGNOSTICS_COMPLETE", len(records), len(texts), "facts accepted", 0)
print("UNSELECTED_CANDIDATE_ROLES", dict(role_counts))
