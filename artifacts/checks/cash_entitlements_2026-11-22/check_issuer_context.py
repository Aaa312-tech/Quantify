"""Bind full-text issuer context decisions without accepting financial fields."""

import argparse
import re
import unicodedata
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).replace(",", "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    base = Path(__file__).resolve().parent
    inventory_path = base / "related_source_inventory.json"
    inventory = read(inventory_path)
    spec = read(args.observations)
    protocol_path = root / "configs/protocols/cash_entitlement_review.json"
    require(spec["protocol_sha256"] == sha(protocol_path), "Protocol changed")
    require(spec["inventory_sha256"] == sha(inventory_path), "Inventory changed")
    require(spec["facts_accepted"] == 0, "Context review cannot accept source facts")
    primary_evidence = [
        base / name
        for name in (
            "remaining_primary_checks.json",
            "remaining_primary_closure.json",
            "adjusted_cash_primary_checks.json",
            "adjusted_cash_arithmetic_review.json",
        )
    ]
    evidence = [read(path) for path in primary_evidence]

    for issuer in spec["issuers"]:
        symbol = issuer["symbol"]
        rows = [row for row in inventory["records"] if symbol in row["symbols"]]
        decisions = {item["source_id"]: item for item in issuer["documents"]}
        require(len(decisions) == len(issuer["documents"]), "Duplicate document decision")
        require(set(decisions) == {row["id"] for row in rows}, "Incomplete received inventory")
        observation_paths = sorted((base / "observations").glob(f"{symbol}_*.json"))
        observations = [read(path) for path in observation_paths]
        require(bool(observations), "Scoped primary observation missing")
        require(
            {item["source_id"] for item in observations}
            == {key for key, value in decisions.items() if value["role"] == "SCOPED_PRIMARY"},
            "Scoped and other-period decisions do not match registered primary events",
        )
        recorded, flags = [], {}
        for row in rows:
            decision = decisions[row["id"]]
            for key in ("source", "receipt"):
                require(sha(root / row[key]["path"]) == row[key]["sha256"], "Bound source changed")
            with fitz.open(root / row["source"]["path"]) as document:
                pages = [page.get_text(sort=True) for page in document]
            flags[f"{row['id']}:independent_text_identity"] = pages == row["independent_pages"]
            require(decision["full_text_pages_read"] == len(pages), "Incomplete full-text review")
            text = compact("\n".join(pages))
            for index, phrase in enumerate(decision["evidence_phrases"], 1):
                flags[f"{row['id']}:context_phrase_{index}"] = compact(phrase) in text
            recorded.append(
                {
                    **decision,
                    "source": row["source"],
                    "receipt": row["receipt"],
                    "catalog_date": row["catalog_date"],
                    "title": row["title"],
                    "complete_text_pages_actually_read": len(pages),
                    "independent_reader_matches": pages == row["independent_pages"],
                }
            )
        retained_primary_failures = [
            item
            for report in evidence
            for item in report.get("failures", [])
            if item.get("symbol") == symbol
        ]
        failures = [name for name, passed in flags.items() if not passed]
        save(
            base / "issuer_checks" / f"{symbol}.json",
            {
                "status": "ISSUER_CONTEXT_REVIEW_COMPLETE"
                if not failures
                else "ISSUER_CONTEXT_REVIEW_FAILED",
                "checked_at": now(),
                "script_sha256": sha(Path(__file__)),
                "protocol_sha256": sha(protocol_path),
                "inventory_sha256": sha(inventory_path),
                "entered_observations": {
                    "path": args.observations.as_posix(),
                    "sha256": sha(args.observations),
                },
                "primary_events": [item["ex_date"] for item in observations],
                "primary_observations": {
                    path.relative_to(root).as_posix(): sha(path) for path in observation_paths
                },
                "primary_review_evidence": {
                    path.relative_to(root).as_posix(): sha(path) for path in primary_evidence
                },
                "retained_initial_primary_failures": retained_primary_failures,
                "received_documents": len(rows),
                "related_decisions": recorded,
                "checks": flags,
                "failures": failures,
                "source_anomalies": issuer["source_anomalies"],
                "related_visual_review": [],
                "facts_accepted": 0,
                "runtime_inputs_changed": False,
                "archive_boundary": "All received issuer notices in the bound inventory were read in full. No scoped amendment was found in the distinct-period notices. Archive completeness and exact publication time remain uncertified.",
                "acceptance_boundary": "This closes received-document context only. It does not accept fields, resolve retained primary arithmetic failures, infer account rounding or apply any unscoped event. Primary visual and arithmetic evidence remains separately bound.",
            },
            immutable=True,
        )
        print(
            symbol,
            "documents",
            len(rows),
            "checks",
            len(flags),
            "failures",
            failures,
            "retained primary failures",
            len(retained_primary_failures),
        )
        require(not failures, f"Context checks failed: {symbol}")


if __name__ == "__main__":
    main()
