"""Retain all provider rows and require agreement on every event economic/date field."""

import datetime as dt
import gzip
import importlib.util
import json
import re
from decimal import Decimal
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = root / "artifacts/checks/distribution_wording_2026-11-21"
protocol = read(root / "configs/protocols/distribution_wording.json")
review_file = base / "source_review.json"
review = read(review_file)
old_file = root / protocol["previous_review"]["path"]
assert sha(old_file) == protocol["previous_review"]["sha256"]
old = {(r["symbol"], r["ex_date"]): r for r in read(old_file)["records"]}
structured_file = root / protocol["source_inputs"]["structured_actions"]["path"]
assert sha(structured_file) == protocol["source_inputs"]["structured_actions"]["sha256"]
stored = {(r["symbol"], r["ex_date"]): r["stored_action"] for r in read(structured_file)["records"]}
reader_file = base / "read_primary_fields.py"
spec = importlib.util.spec_from_file_location("wording_primary_reader", reader_file)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
rows = []
preserved = []
for row in review["records"]:
    key = row["symbol"], row["ex_date"]
    previous = old[key]
    assert row["original_action_sha256"] == previous["original_action_sha256"]
    assert row.get("candidate_resolution") == previous.get("candidate_resolution")
    for field, value in previous["verified_fields"].items():
        assert row["verified_fields"].get(field) == value, (key, field)
        preserved.append([*key, field, value])
    added = {
        f: v for f, v in row["verified_fields"].items() if f not in previous["verified_fields"]
    }
    if not added:
        continue
    assert set(added) <= {"cash_pay_date", "share_list_date"}
    source = row["source"]
    pdf = root / source["path"]
    receipt = read(root / source["receipt"])
    assert sha(pdf) == source["sha256"] == receipt["sha256"]
    assert sha(root / source["receipt"]) == source["receipt_sha256"]
    assert receipt["published_code"] == key[0][-6:] and key[0] in receipt["requested_symbols"]
    assert receipt["catalog_date"] == source["catalog_date"]
    assert "实施" in receipt["title"] and not re.search(
        "更正|修订|调整|补充|取消|提示|预案", receipt["title"]
    )
    known = dt.date.fromisoformat(source["catalog_date"]) + dt.timedelta(days=1)
    assert str(known) <= key[1] and row["known_at"] == str(known) + "T00:00:00+08:00"
    candidate = row["structured_source"]
    assert sha(root / candidate["source"]) == candidate["source_sha256"]
    with gzip.open(root / candidate["source"], "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    candidates = [dict(zip(payload["fields"], values)) for values in payload["rows"]]
    matches = [
        r
        for r in candidates
        if r["code"].replace(".", "") == key[0] and r["dividOperateDate"] == key[1]
    ]
    matches = list({json.dumps(r, sort_keys=True): r for r in matches}.values())
    economic_fields = (
        "code",
        "dividRegistDate",
        "dividOperateDate",
        "dividPayDate",
        "dividStockMarketDate",
        "dividCashPsBeforeTax",
        "dividStocksPs",
        "dividReserveToStockPs",
    )
    assert matches and len({tuple(r[f] for f in economic_fields) for r in matches}) == 1
    raw = matches[0]
    expected = {
        "record_date": raw["dividRegistDate"],
        "ex_date": raw["dividOperateDate"],
        "cash_pay_date": raw["dividPayDate"],
        "cash_per_share": raw["dividCashPsBeforeTax"],
    }
    if "share_list_date" in added:
        expected["share_list_date"] = raw["dividStockMarketDate"]
    assert expected["record_date"] == stored[key]["record_date"]
    assert abs(
        Decimal(expected["cash_per_share"]) - Decimal(str(stored[key]["cash_per_share"]))
    ) <= Decimal("1e-12")
    assert all(expected[f] == v == candidate[f] for f, v in added.items())
    evidence, layouts, rejected, first = reader.extract(pdf)
    with fitz.open(pdf) as document:
        page_text = [p.get_text(sort=True) for p in document]
    labelled = re.findall(r"(?:证券|股票)\s*代码\s*[:：]?\s*(\d{6})(?!\d)", page_text[0])
    checks = {
        "issuer": {
            "expected": key[0][-6:],
            "observed": labelled,
            "passed": key[0][-6:] in labelled,
        },
        "title": {
            "expected": receipt["title"],
            "passed": reader.PRIOR.compact(receipt["title"]) in first,
        },
    }
    for field, value in expected.items():
        observed = {r["value"] for r in evidence.get(field, [])}
        if field == "cash_per_share":
            observed = {Decimal(v) for v in observed}
            passed = len(observed) == 1 and abs(next(iter(observed)) - Decimal(value)) <= Decimal(
                "1e-12"
            )
        else:
            passed = observed == {value}
        checks[field] = {
            "expected": value,
            "observed": sorted(map(str, observed)),
            "passed": passed,
        }
    rows.append(
        {
            "symbol": key[0],
            "ex_date": key[1],
            "added_fields": added,
            "source": source,
            "receipt": receipt,
            "raw_provider": raw,
            "all_provider_rows": matches,
            "provider_differences": {
                f: [r[f] for r in matches] for f in raw if len({r[f] for r in matches}) > 1
            },
            "checks": checks,
            "evidence": evidence,
            "layouts": layouts,
            "rejected": rejected,
            "passed": all(r["passed"] for r in checks.values()),
        }
    )
assert set(old) == {(r["symbol"], r["ex_date"]) for r in review["records"]} and len(old) == 1839
output = {
    "status": "PASS" if all(r["passed"] for r in rows) else "FAIL",
    "checked_at": now(),
    "checker_sha256": sha(Path(__file__)),
    "prior_failed_checker_sha256": sha(base / "check_fields.py"),
    "prior_failure_log_sha256": sha(base / "independent_fields.log"),
    "reader_sha256": sha(reader_file),
    "prior_reader_sha256": sha(reader.READER),
    "source_review_sha256": sha(review_file),
    "preserved_fields": preserved,
    "events": len(rows),
    "field_checks": sum(len(r["checks"]) - 2 for r in rows),
    "failed_events": sum(not r["passed"] for r in rows),
    "records": rows,
}
save(base / "independent_closure.json", output, immutable=True)
print(output["status"], "events", len(rows), "failed", output["failed_events"])
for row in rows:
    if not row["passed"]:
        print(
            row["symbol"],
            row["ex_date"],
            {k: v for k, v in row["checks"].items() if not v["passed"]},
        )
assert output["status"] == "PASS"
