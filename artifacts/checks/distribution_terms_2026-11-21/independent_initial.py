"""Check added dividend facts with PyMuPDF cells and a separate paragraph reader.

This audit does not import the production extractor. Amount candidates are collected
before comparison with the expected value; dates follow cell headers, not text order.
"""

import argparse
import bisect
import datetime as dt
import gzip
import hashlib
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

import fitz


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def calendar_date(value):
    value = compact(value).replace("年", "/").replace("月", "/").removesuffix("日")
    if not re.fullmatch(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", value):
        return None
    try:
        return dt.date(*map(int, re.split("[-/]", value))).isoformat()
    except ValueError:
        return None


HEADERS = {
    "股权登记日": "record_date",
    "除权(息)日": "ex_date",
    "除权除息日": "ex_date",
    "除息日": "ex_date",
    "现金红利发放日": "cash_pay_date",
    "新增无限售条件流通股份上市日": "share_list_date",
}
DATE_TOKEN = r"20\d{2}(?:年|/|-)\d{1,2}(?:月|/|-)\d{1,2}日?(?!\d)"


def extract(pdf):
    evidence = {}
    layouts = []

    def add(field, value, page, method, context, **extra):
        if value is not None:
            evidence.setdefault(field, []).append(
                {
                    "value": value,
                    "page": page,
                    "method": method,
                    "context": context,
                    **extra,
                }
            )

    paragraphs = []
    ends = []
    offset = 0
    with fitz.open(pdf) as document:
        for number, page in enumerate(document, 1):
            raw = unicodedata.normalize("NFKC", page.get_text(sort=True))
            paragraphs.append(raw + "\n")
            offset += len(raw) + 1
            ends.append(offset)
            text = compact(raw)
            for label, field in HEADERS.items():
                if field == "cash_pay_date":
                    continue
                for match in re.finditer(
                    re.escape(label) + r"(?:为[:：]?|[:：])(" + DATE_TOKEN + ")", text
                ):
                    add(field, calendar_date(match[1]), number, "labelled_prose", match[0])
            for match in re.finditer(r"现金红利将于(" + DATE_TOKEN + r")([^。；;]{0,120})", text):
                if "划入其资金账户" in match[2]:
                    add("cash_pay_date", calendar_date(match[1]), number, "payment_prose", match[0])
            for match in re.finditer(r"上市日(?:为[:：]?|[:：])(" + DATE_TOKEN + ")", text):
                add("share_list_date", calendar_date(match[1]), number, "listing_prose", match[0])
            if "股权登记日" not in text or "现金红利发放日" not in text:
                continue
            for table in page.find_tables().tables:
                cells = table.extract()
                if not cells:
                    continue
                header = [compact(c) for c in cells[0]]
                if "股权登记日" not in header or "现金红利发放日" not in header:
                    continue
                mapping = {i: HEADERS[c] for i, c in enumerate(header) if c in HEADERS}
                if len(set(mapping.values())) != len(mapping):
                    continue
                class_column = header.index("股份类别") if "股份类别" in header else None
                rows = cells[1:]
                if class_column is None and len(rows) != 1:
                    continue
                signature = " | ".join(header)
                layouts.append({"page": number, "family": signature, "bbox": list(table.bbox)})
                for row_number, row in enumerate(rows, 1):
                    if class_column is not None and compact(row[class_column]) != "A股":
                        continue
                    for column, field in mapping.items():
                        value = calendar_date(row[column])
                        add(
                            field,
                            value,
                            number,
                            "geometric_table_cell",
                            row[column],
                            header=header[column],
                            table_bbox=list(table.bbox),
                            row=row_number,
                            column=column,
                        )

    # Keep page positions while treating wrapped Chinese paragraphs as one sentence.
    combined = "".join(paragraphs)
    headings = list(re.finditer(r"(?m)^\s*[一二三四五六七八九十]+、", combined))
    rejected = []
    accepted_titles = {
        "权益分派方案",
        "利润分配方案",
        "分配方案",
        "分红派息方案",
        "利润分配及资本公积金转增股本方案",
        "分配、转增股本方案",
        "利润分配及转增股本方案",
        "分配及转增股本方案",
    }
    for index, heading in enumerate(headings):
        start = heading.end()
        stop = headings[index + 1].start() if index + 1 < len(headings) else len(combined)
        segment = combined[start:stop]
        positions = [start + i for i, c in enumerate(segment) if not c.isspace()]
        content = "".join(combined[i] for i in positions)
        title_end = content.find("方案", 0, 55)
        if title_end < 0:
            continue
        title = content[: title_end + 2]
        base = title.removeprefix("本次实施的").removeprefix("本次")
        if base not in accepted_titles:
            continue
        pattern = r"每(?P<unit>10股|股)(?P<words>[^。；;每]{0,42}?)(?P<amount>\d+(?:\.\d+)?)元(?:人民币)?(?P<tail>[^()元。；;]{0,8})\(含税"
        for match in re.finditer(pattern, content):
            if "现金" not in match["words"] + match["tail"]:
                continue
            prefix = re.split(r"[。；;]", content[: match.start()])[-1]
            # Remove only complete parenthesized capital-composition statements.
            for group in re.findall(r"\([^()]*\)", prefix):
                tokens = compact(group[1:-1])
                if re.fullmatch(r"其中A股[0-9,]+股,H股[0-9,]+股", tokens):
                    prefix = prefix.replace(group, "")
            role_pattern = r"已派|前次|上次|此前|全年|累计|H股|税后|除权|折算"
            if not re.search(r"向全体股东(?:按)?$", prefix):
                role_pattern += r"|预案|拟"
            role = re.search(role_pattern, prefix)
            page_number = bisect.bisect_right(ends, positions[match.start()]) + 1
            if role:
                rejected.append({"page": page_number, "role": role[0], "text": match[0]})
                continue
            value = Decimal(match["amount"]) / (10 if match["unit"] == "10股" else 1)
            add(
                "cash_per_share",
                str(value),
                page_number,
                "implementation_paragraph",
                content[max(0, match.start() - 100) : match.end()],
                title=title,
            )
            layouts.append({"page": page_number, "family": "amount:" + title + ":" + match["unit"]})
    return evidence, layouts, rejected, compact(paragraphs[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    base = root / "artifacts/checks/distribution_terms_2026-11-21"
    review_path = base / "approved_wording/source_review.json"
    review = read(review_path)
    previous_path = root / "artifacts/checks/distribution_repair_2026-11-21/source_review.json"
    previous = {(r["symbol"], r["ex_date"]): r for r in read(previous_path)["facts"]}
    structured_path = (
        root / "artifacts/checks/disclosure_sources_2026-11-21/all_structured_actions.json"
    )
    original = {(r["symbol"], r["ex_date"]): r for r in read(structured_path)["records"]}
    restored = {(r[0], r[1]) for r in read(base / "preservation_failure.json")["lost_fields"]}
    rows = []
    for record in review["facts"]:
        key = record["symbol"], record["ex_date"]
        added = {
            k: v
            for k, v in record["verified_fields"].items()
            if k not in previous.get(key, {}).get("verified_fields", {})
        }
        if not added and key not in restored:
            continue
        source = record["source"]
        pdf = root / source["path"]
        receipt = read(root / source["receipt"])
        assert sha(pdf) == source["sha256"] == receipt["sha256"]
        assert sha(root / source["receipt"]) == source["receipt_sha256"]
        assert receipt["published_code"] == key[0][-6:]
        assert key[0] in receipt["requested_symbols"]
        assert receipt["catalog_date"] == source["catalog_date"]
        assert "实施" in receipt["title"]
        assert not any(
            word in receipt["title"]
            for word in ["更正", "修订", "调整", "补充", "取消", "提示", "预案"]
        )
        known = dt.date.fromisoformat(source["catalog_date"]) + dt.timedelta(days=1)
        assert str(known) <= key[1]
        candidate = record["structured_source"]
        assert sha(root / candidate["source"]) == candidate["source_sha256"]
        with gzip.open(root / candidate["source"], "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        provider_rows = [dict(zip(payload["fields"], r)) for r in payload["rows"]]
        matches = [
            r
            for r in provider_rows
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
        stored = original[key]["stored_action"]
        assert expected["record_date"] == stored["record_date"]
        assert abs(
            Decimal(expected["cash_per_share"]) - Decimal(str(stored["cash_per_share"]))
        ) <= Decimal("1e-12")
        for field, value in added.items():
            assert expected[field] == value == candidate[field]
        evidence, layouts, rejected, first = extract(pdf)
        issuer_found = key[0][-6:] in first
        checks = {}
        for field, expected_value in expected.items():
            values = {r["value"] for r in evidence.get(field, [])}
            if field == "cash_per_share":
                values = {Decimal(v) for v in values}
                passed = len(values) == 1 and abs(
                    next(iter(values)) - Decimal(expected_value)
                ) <= Decimal("1e-12")
            else:
                passed = values == {expected_value}
            checks[field] = {
                "passed": passed,
                "expected": expected_value,
                "observed": sorted(str(v) for v in values),
            }
        rows.append(
            {
                "symbol": key[0],
                "ex_date": key[1],
                "added_fields": added,
                "restored_prior_fact": key in restored,
                "source": source,
                "structured_source": candidate,
                "source_available_at": str(known),
                "issuer_found": issuer_found,
                "checks": checks,
                "passed": issuer_found and all(r["passed"] for r in checks.values()),
                "evidence": evidence,
                "layouts": layouts,
                "rejected_amounts": rejected,
            }
        )
        if len(rows) % 25 == 0:
            print("INDEPENDENT SOURCES", len(rows), flush=True)
    output = {
        "status": "PASS" if all(r["passed"] for r in rows) else "FAIL",
        "checked_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        "checker_sha256": sha(Path(__file__)),
        "pymupdf_version": fitz.VersionBind,
        "source_review_sha256": sha(review_path),
        "previous_review_sha256": sha(previous_path),
        "structured_inventory_sha256": sha(structured_path),
        "events": len(rows),
        "added_events": sum(bool(r["added_fields"]) for r in rows),
        "added_fields": dict(Counter(f for r in rows for f in r["added_fields"])),
        "checks": sum(len(r["checks"]) for r in rows),
        "failed_events": sum(not r["passed"] for r in rows),
        "records": rows,
        "facts_applied": False,
    }
    assert not args.output.exists()
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print({k: v for k, v in output.items() if k != "records"}, flush=True)


if __name__ == "__main__":
    main()
