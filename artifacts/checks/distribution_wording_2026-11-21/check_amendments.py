"""Account for every archived amendment candidate for the newly supported issuers."""

import re
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = root / "artifacts/checks/distribution_wording_2026-11-21"
fields = read(base / "field_acceptance.json")
assert fields["status"] == "PASS"
symbols = {r["symbol"] for r in fields["records"]}
collection_file = root / "data/raw/disclosures_retry_2026-11-21/collection.json"
collection = read(collection_file)
manifest = read(root / "configs/integrity_manifest.json")["files"]
assert sha(collection_file) == manifest[collection_file.relative_to(root).as_posix()]
inventory = []
related = []
for item in collection["documents"]:
    receipt_file = root / item["receipt"]
    receipt = read(receipt_file)
    if not symbols.intersection(receipt["requested_symbols"]):
        continue
    assert sha(receipt_file) == manifest[item["receipt"]]
    selected = receipt["kind"] == "amendment" or bool(
        re.search("更正|修订|补充|取消|终止|撤销|延期", receipt["title"])
    )
    inventory.append(
        {
            "receipt": item["receipt"],
            "receipt_sha256": sha(receipt_file),
            "id": receipt["id"],
            "title": receipt["title"],
            "kind": receipt["kind"],
            "symbols": receipt["requested_symbols"],
            "amendment_candidate": selected,
        }
    )
    if not selected:
        continue
    pdf = root / receipt["path"]
    assert receipt["status"] == "RECEIVED" and sha(pdf) == receipt["sha256"]
    with fitz.open(pdf) as document:
        pages = [page.get_text(sort=True) for page in document]
    compact = "".join(pages[0].split())
    assert receipt["id"] == "1224797176"
    assert receipt["title"] == "关于公司股票交易撤销其他风险警示暨停复牌的公告"
    assert receipt["title"] in compact and "证券代码:002602" in compact.replace("：", ":")
    assert "股票交易价格日涨跌幅限制" in compact and "开市起复牌" in compact
    related.append(
        {
            "source": receipt,
            "receipt_sha256": sha(receipt_file),
            "pages": pages,
            "decision": "TRADING_STATUS_NOTICE_NOT_A_DISTRIBUTION_AMENDMENT",
            "affected_events": [
                {"symbol": r["symbol"], "ex_date": r["ex_date"]}
                for r in fields["records"]
                if r["symbol"] in receipt["requested_symbols"]
            ],
        }
    )
assert len(related) == 1
save(
    base / "amendment_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "collection_sha256": sha(collection_file),
        "field_acceptance_sha256": sha(base / "field_acceptance.json"),
        "issuers": sorted(symbols),
        "issuer_documents": len(inventory),
        "amendment_candidates": len(related),
        "unresolved_conflicts": 0,
        "inventory": inventory,
        "records": related,
        "scope": "Complete frozen collection only; not a certification that every historical filing or amendment exists in the archive. No historical trading status is applied by this source review.",
    },
    immutable=True,
)
print(
    "PASS",
    len(symbols),
    "issuers",
    len(inventory),
    "archived documents",
    len(related),
    "related notice",
)
