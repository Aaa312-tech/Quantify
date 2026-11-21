"""Restore adjacent contract rows and a split nominal header found by inventory checks."""

import importlib.util
from decimal import Decimal
from pathlib import Path

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
CHECKS = HERE.parent
OUTPUT = ROOT / "data/processed/etf_exposures_2026-11-21"
spec = importlib.util.spec_from_file_location(
    "financial_layouts", CHECKS / "resolve_financial_layouts.py"
)
layouts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layouts)
base = layouts.base


def main():
    protocol = read(HERE / "financial_followup_protocol.json")
    visual = read(HERE / "visual_review.json")
    source = OUTPUT / "financial_fields_reviewed.json.gz"
    if sha(source) != protocol["input_sha256"] or visual["status"] != "VISUALLY_REVIEWED":
        raise ValueError("Input or visual receipt differs")
    for record in visual["records"]:
        if sha(ROOT / record["panel"]) != record["panel_sha256"]:
            raise ValueError("Reviewed image changed")
    save(
        HERE / "source_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "layout_source_sha256": sha(CHECKS / "resolve_financial_layouts.py"),
            "financial_source_sha256": sha(CHECKS / "review_financial_fields.py"),
            "protocol_sha256": sha(HERE / "financial_followup_protocol.json"),
            "visual_review_sha256": sha(HERE / "visual_review.json"),
        },
        immutable=True,
    )
    results = load_gzip(source)
    docs = {d["source"]: d for d in read(CHECKS / "notes_reviewed.json")["documents"]}
    scope = {d["source"]: d for d in protocol["scope"]}
    for result in results:
        if result["source"] not in scope:
            continue
        doc = docs[result["source"]]
        entry = scope[result["source"]]
        note = load_gzip(ROOT / doc["financial_notes"]) if doc["financial_notes"] else None
        original = note or load_gzip(ROOT / doc["extracted_pages"])
        pages = {p["page"]: p for p in original["pages"]}
        if doc["symbol"] == "sh518880":
            index = next(
                i
                for i, t in enumerate(pages[34]["tables"])
                if "本期末" in str(t["cells"]) and "2022" in str(t["cells"])
            )
            # The last current-period header is the derivative table, after trading assets.
            candidates = [
                i
                for i, t in enumerate(pages[34]["tables"])
                if "本期末" in str(t["cells"]) and "2022" in str(t["cells"])
            ]
            index = candidates[-1]
            result["nominal"]["tables"].append(
                layouts.resolve_nominal(doc, {"page": 34, "table": index}, pages, note)
            )
        else:
            if len(result["contracts"]["tables"]) != 1:
                raise ValueError("Expected one original contract table")
            table = result["contracts"]["tables"][0]
            first, second = [pages[p] for p in entry["pages"]]
            following = second["tables"][0]
            continuation = following["cells"][0]
            if not base.compact(continuation[0]):
                row = table["rows"][-1]
                fragments = [
                    {
                        "page": row["page"],
                        "table": row["table"],
                        "row": row["row"],
                        "cells": row["source_cells"],
                    },
                    {"page": second["page"], "table": 0, "row": 0, "cells": continuation},
                ]
                for fragment in fragments:
                    fragment["proof"] = base.proof(
                        pages[fragment["page"]], "".join(base.compact(x) for x in fragment["cells"])
                    )
                joined = [
                    base.compact(a) + base.compact(b)
                    for a, b in zip(row["source_cells"], continuation)
                ]
                row.update(name=joined[1], source_cells=joined, fragments=fragments)
                del row["proof"]
            if any(base.CODE.fullmatch(base.compact(r[0])) for r in following["cells"]):
                table["rows"].extend(base.table_rows(second, following, 0, -1))
            table["continuation_table"] = {
                "page": second["page"],
                "table": 0,
                "cells": following["cells"],
            }
            table["market_value_sum_cny"] = str(
                sum(Decimal(r["reported_market_value_cny"]) for r in table["rows"])
            )
            table["fair_value_change_sum_cny"] = str(
                sum(Decimal(r["reported_fair_value_change_cny"]) for r in table["rows"])
            )
        result["inventory_followup_visual_id"] = entry["id"]
    target = OUTPUT / "financial_observations.json.gz"
    dump_gzip(target, results)
    save(
        HERE / "completion.json",
        {
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "output": target.relative_to(ROOT).as_posix(),
            "output_sha256": sha(target),
            "corrected_reports": len(scope),
            "contract_rows": sum(len(t["rows"]) for r in results for t in r["contracts"]["tables"]),
            "nominal_tables": sum(len(r["nominal"]["tables"]) for r in results),
            "status": "RESOLVED_PENDING_INDEPENDENT_AUDIT",
            "activated": False,
        },
        immutable=True,
    )


if __name__ == "__main__":
    main()
