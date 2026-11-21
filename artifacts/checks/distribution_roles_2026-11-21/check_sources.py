"""Check notice identities, roles and additional dates with a separate PDF reader."""

import gzip
import hashlib
import importlib.util
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

import fitz

ROOT = Path.cwd()
BASE = ROOT / "artifacts/checks/distribution_roles_2026-11-21"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def identity(row):
    return row["symbol"], row["ex_date"]


def main():
    protocol = read(ROOT / "configs/protocols/distribution_roles.json")
    review_file = ROOT / protocol["review"]
    review = read(review_file)
    previous_file = ROOT / protocol["previous_review"]["path"]
    previous = read(previous_file)
    assert sha(previous_file) == protocol["previous_review"]["sha256"]
    old = {identity(row): row for row in previous["facts"]}
    new = {identity(row): row for row in review["facts"]}
    assert len(old) == 1282
    for key, row in old.items():
        assert all(new[key]["verified_fields"][k] == v for k, v in row["verified_fields"].items())
    assert {identity(r): r["original_action_sha256"] for r in previous["records"]} == {
        identity(r): r["original_action_sha256"] for r in review["records"]
    }
    original_index = read(ROOT / protocol["source_inputs"]["event_index"]["path"])
    index = {identity(r): r for r in original_index["records"]}
    structured = read(ROOT / protocol["source_inputs"]["structured_actions"]["path"])
    stored = {identity(r): r["stored_action"] for r in structured["records"]}
    field_reader = ROOT / "artifacts/checks/distribution_terms_2026-11-21/independent_fields.py"
    spec = importlib.util.spec_from_file_location("independent_dividend_reader", field_reader)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    roles, fields = [], []
    for row in review["records"]:
        key = identity(row)
        resolution = row.get("candidate_resolution")
        if resolution:
            originals = index[key]["candidates"]
            assert len(originals) == len(resolution["candidates"])
            resolved = resolution["status"] == "RESOLVED_COMPANIONS"
            if resolved:
                assert sum(r["role"] == "IMPLEMENTATION" for r in resolution["candidates"]) == 1
            for candidate, role in zip(originals, resolution["candidates"]):
                assert all(role[k] == v for k, v in candidate.items())
                pdf = ROOT / candidate["source"]
                receipt_file = ROOT / candidate["receipt"]
                receipt = read(receipt_file)
                assert sha(pdf) == candidate["source_sha256"] == receipt["sha256"]
                assert receipt["path"] == candidate["source"]
                assert receipt["id"] == candidate["id"]
                assert receipt["title"] == candidate["title"]
                assert receipt["catalog_date"] == candidate["catalog_date"]
                assert receipt["published_code"] == key[0][-6:]
                assert key[0] in receipt["requested_symbols"]
                with fitz.open(pdf) as document:
                    pages = [p.get_text(sort=True) for p in document]
                    blocks = [
                        [
                            {"bbox": list(b[:4]), "text": b[4]}
                            for b in p.get_text("blocks", sort=True)
                        ]
                        for p in document
                    ]
                title_found = compact(candidate["title"]) in compact(pages[0])
                labelled = re.findall(r"(?:证券|股票)\s*代码\s*[:：]?\s*(\d{6})(?!\d)", pages[0])
                code_found = key[0][-6:] in labelled
                body_checks = []
                for evidence in role["evidence"]:
                    text = compact(evidence["text"])
                    page = compact(pages[evidence["page"] - 1])
                    prices = re.findall(r"[0-9]+(?:\.[0-9]+)?元(?:/股)?", text)
                    object_found = (
                        "回购" in text and "价格" in text
                        if role["role"] == "REPURCHASE_PRICE"
                        else "转股价格" in text
                    )
                    body_checks.append(
                        {
                            **evidence,
                            "present_in_independent_page": text in page,
                            "prices": prices,
                            "passed": text in page
                            and bool(prices)
                            and object_found
                            and "调整" in text,
                        }
                    )
                passed = True
                if resolved:
                    title = candidate["title"]
                    passed = (
                        title_found
                        and code_found
                        and not any(
                            word in title
                            for word in ("更正", "修订", "补充", "取消", "终止", "撤销", "延期")
                        )
                    )
                    if role["role"] == "IMPLEMENTATION":
                        passed = (
                            passed
                            and title.endswith("实施公告")
                            and candidate["id"] == resolution["selected_id"]
                        )
                    else:
                        passed = (
                            passed and bool(body_checks) and all(r["passed"] for r in body_checks)
                        )
                roles.append(
                    {
                        "symbol": key[0],
                        "ex_date": key[1],
                        "source": candidate,
                        "role": role["role"],
                        "resolution": resolution["status"],
                        "reason": role["reason"],
                        "title_found": title_found,
                        "labelled_codes": labelled,
                        "code_found": code_found,
                        "body_checks": body_checks,
                        "blocks": blocks,
                        "passed": bool(passed),
                        "receipt_sha256": sha(receipt_file),
                    }
                )
        added = {
            f: v
            for f, v in row["verified_fields"].items()
            if f not in old.get(key, {}).get("verified_fields", {})
        }
        if not added:
            continue
        assert resolution and resolution["status"] == "RESOLVED_COMPANIONS"
        candidate = row["structured_source"]
        assert sha(ROOT / candidate["source"]) == candidate["source_sha256"]
        with gzip.open(ROOT / candidate["source"], "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        records = [dict(zip(payload["fields"], r)) for r in payload["rows"]]
        matches = [
            r
            for r in records
            if r["dividOperateDate"] == key[1] and r["code"].replace(".", "") == key[0]
        ]
        matches = list({json.dumps(r, sort_keys=True): r for r in matches}.values())
        assert len(matches) == 1
        provider = matches[0]
        expected = {
            "record_date": provider["dividRegistDate"],
            "ex_date": provider["dividOperateDate"],
            "cash_pay_date": provider["dividPayDate"],
            "cash_per_share": provider["dividCashPsBeforeTax"],
        }
        if "share_list_date" in added:
            expected["share_list_date"] = provider["dividStockMarketDate"]
        assert expected["record_date"] == stored[key]["record_date"]
        assert abs(
            Decimal(expected["cash_per_share"]) - Decimal(str(stored[key]["cash_per_share"]))
        ) <= Decimal("1e-12")
        assert all(expected[f] == v == candidate[f] for f, v in added.items())
        evidence, layouts, rejected, first = reader.extract(ROOT / row["source"]["path"])
        checks = {}
        for field, value in expected.items():
            observed = {r["value"] for r in evidence.get(field, [])}
            if field == "cash_per_share":
                observed = {Decimal(v) for v in observed}
                passed = len(observed) == 1 and abs(
                    next(iter(observed)) - Decimal(value)
                ) <= Decimal("1e-12")
            else:
                passed = observed == {value}
            checks[field] = {
                "expected": value,
                "observed": sorted(map(str, observed)),
                "passed": passed,
            }
        fields.append(
            {
                "symbol": key[0],
                "ex_date": key[1],
                "added_fields": added,
                "source": row["source"],
                "evidence": evidence,
                "layouts": layouts,
                "rejected_amounts": rejected,
                "checks": checks,
                "passed": key[0][-6:] in first and all(r["passed"] for r in checks.values()),
            }
        )
    assert sum("candidate_resolution" in r for r in review["records"]) == sum(
        r["status"] == "MULTIPLE_TEXT_CANDIDATES" for r in index.values()
    )
    output = {
        "status": "PASS" if all(r["passed"] for r in roles + fields) else "FAIL",
        "checker_sha256": sha(Path(__file__)),
        "field_reader_sha256": sha(field_reader),
        "pymupdf_version": fitz.VersionBind,
        "source_review_sha256": sha(review_file),
        "previous_review_sha256": sha(previous_file),
        "prior_events_preserved": len(old),
        "multi_events": sum("candidate_resolution" in r for r in review["records"]),
        "documents_checked": len(roles),
        "resolved_events": len(
            {identity(r) for r in roles if r["resolution"] == "RESOLVED_COMPANIONS"}
        ),
        "added_events": len(fields),
        "added_fields": dict(Counter(f for r in fields for f in r["added_fields"])),
        "checks": sum(len(r["checks"]) for r in fields),
        "failed_roles": sum(not r["passed"] for r in roles),
        "failed_events": sum(not r["passed"] for r in fields),
        "roles": roles,
        "fields": fields,
        "facts_applied": False,
    }
    target = BASE / "independent_sources.json"
    assert not target.exists()
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({k: v for k, v in output.items() if k not in ("roles", "fields")}, flush=True)


if __name__ == "__main__":
    main()
