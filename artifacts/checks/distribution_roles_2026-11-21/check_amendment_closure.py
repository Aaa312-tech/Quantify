"""Inventory archived amendments for every issuer with a resolved candidate set."""

import hashlib
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


def period(title):
    match = re.search(r"(20\d{2})年?(半年度|中期|前三季度|第一季度|年度)", compact(title))
    if not match:
        return None
    return match[1], "半年度" if match[2] == "中期" else match[2]


# Read from the complete amended notices before comparison with implementation facts.
# These are gross amounts, not the theoretical ex-dividend reference adjustment.
AMOUNTS = {
    "1224830339": (2, "0.08", "每股派发现金红利0.08元(含税)"),
    "1220552205": (2, "0.22", "每10股派发现金红利2.20元(含税)"),
    "1224225176": (2, "0.30", "每10股派发现金红利3.00元(含税)"),
    "1220521720": (2, "0.38193", "调整后公司向全体股东每股派发现金红利0.38193元(含税)"),
    "1223788789": (2, "0.35", "每10股派发现金红利人民币3.50元(含税)"),
    "1220392087": (2, "0.91", "每10股派发9.10元现金红利(含税)"),
    "1223836166": (2, "1.19", "每10股派发11.90元现金红利(含税)"),
    "1224707097": (2, "0.80", "每10股派发8.00元现金红利(含税)"),
    "1225426436": (2, "1.20", "每10股派发12.00元现金红利(含税)"),
    "1225600401": (2, "1.18", "每10股派发11.80元现金红利(含税)"),
    "1220456964": (2, "0.12", "每10股派发现金红利1.2元(含税)"),
    "1221542417": (2, "0.055", "每10股派发现金红利0.55元(含税)"),
    "1224068499": (1, "0.08", "每10股派发现金红利人民币0.80元(含税)"),
    "1225401626": (1, "0.30", "每10股派发现金红利人民币3.00元(含税)"),
    "1225558586": (1, "0.50", "每10股派发现金红利人民币5.00元(含税)"),
}


def main():
    review_file = BASE / "source_review.json"
    review = read(review_file)
    resolved = [
        r
        for r in review["records"]
        if r.get("candidate_resolution", {}).get("status") == "RESOLVED_COMPANIONS"
    ]
    symbols = {r["symbol"] for r in resolved}
    collection_file = ROOT / "data/raw/disclosures_retry_2026-11-21/collection.json"
    collection = read(collection_file)
    rows = []
    for item in collection["documents"]:
        receipt_file = ROOT / item["receipt"]
        receipt = read(receipt_file)
        if not symbols.intersection(receipt["requested_symbols"]):
            continue
        title = receipt["title"]
        if receipt["kind"] != "amendment" and not re.search(
            "更正|修订|补充|取消|终止|撤销|延期", title
        ):
            continue
        pdf = ROOT / receipt["path"]
        assert sha(pdf) == receipt["sha256"] and receipt["status"] == "RECEIVED"
        with fitz.open(pdf) as doc:
            pages = [p.get_text(sort=True) for p in doc]
        full = "\n".join(pages)
        text = compact(full)
        labelled = re.findall(r"(?:证券|股票)\s*代码\s*[:：]?\s*(\d{6})(?!\d)", pages[0])
        assert receipt["published_code"] in labelled
        fiscal = period(title)
        price = bool(re.search("回购.*价格|转股价格", title))
        governance = "修订公司章程及利润分配管理制度" in title
        trading_status = "股票撤销退市风险警示暨停复牌" in title
        if price:
            kind = "PRICE_NOTICE"
            assert "调整" in text and re.search(r"[0-9]+(?:\.[0-9]+)?元", text)
        elif governance or trading_status:
            kind = "GOVERNANCE_OR_TRADING_STATUS"
            assert compact(title) in compact(pages[0])
        else:
            kind = "DISTRIBUTION_AMENDMENT"
            assert fiscal is not None
            assert re.search(rf"{fiscal[0]}年?(?:{fiscal[1]}|中期)", text)
        relevance = []
        for record in resolved:
            if record["symbol"] not in receipt["requested_symbols"]:
                continue
            primary = next(
                r
                for r in record["candidate_resolution"]["candidates"]
                if r["role"] == "IMPLEMENTATION"
            )
            target_period = period(primary["title"])
            assert target_period is not None
            entry = {
                "symbol": record["symbol"],
                "ex_date": record["ex_date"],
                "primary_id": primary["id"],
                "primary_period": target_period,
            }
            if kind != "DISTRIBUTION_AMENDMENT":
                entry["decision"] = kind
            elif fiscal != target_period:
                entry["decision"] = "DIFFERENT_DIVIDEND_PERIOD"
            elif record["status"] != "VERIFIED_FIELDS":
                entry["decision"] = "NO_FIELDS_ACCEPTED_FOR_EVENT"
            else:
                page, amount, quotation = AMOUNTS[receipt["id"]]
                assert compact(quotation) in compact(pages[page - 1])
                if receipt["id"] in {"1224068499", "1225401626", "1225558586"}:
                    assert "每股分配金额不变" in compact(pages[1])
                    entry["unchanged_amount_support"] = {"page": 2, "text": "每股分配金额不变"}
                observed = {Decimal(r["value"]) for r in record["evidence"]["cash_per_share"]}
                assert observed == {Decimal(amount)}
                entry.update(
                    decision="AMENDED_GROSS_AGREES",
                    page=page,
                    gross_per_share=amount,
                    text=quotation,
                )
            relevance.append(entry)
        rows.append(
            {
                "id": receipt["id"],
                "title": title,
                "kind": kind,
                "period": fiscal,
                "source": receipt["path"],
                "source_sha256": sha(pdf),
                "receipt": item["receipt"],
                "receipt_sha256": sha(receipt_file),
                "catalog_date": receipt["catalog_date"],
                "pages": pages,
                "relevance": relevance,
            }
        )
    output = {
        "status": "PASS",
        "checker_sha256": sha(Path(__file__)),
        "prior_failed_checker_sha256": sha(BASE / "check_amendments.py"),
        "prior_failure_log_sha256": sha(BASE / "amendment_review.log"),
        "source_review_sha256": sha(review_file),
        "collection_sha256": sha(collection_file),
        "archived_documents_scanned": len(collection["documents"]),
        "affected_issuers": len(symbols),
        "amendment_notices": len(rows),
        "kinds": dict(Counter(r["kind"] for r in rows)),
        "decisions": dict(Counter(x["decision"] for r in rows for x in r["relevance"])),
        "records": rows,
        "facts_applied": False,
        "scope": "Whole frozen archive, including amount/total adjustments without revision keywords. Financial same-period amendments are compared with gross implementation terms; nonaccepted events remain unresolved. This does not certify complete archive coverage or historical publication times.",
    }
    target = BASE / "amendment_closure.json"
    assert not target.exists()
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({k: v for k, v in output.items() if k != "records"})


if __name__ == "__main__":
    main()
