"""Read explicitly denominated renminbi payouts in the current A-share scheme."""

import bisect
import importlib.util
import re
from decimal import Decimal
from pathlib import Path

READER = Path.cwd() / "artifacts/checks/payout_scope_2026-11-21/read_primary_fields.py"
SPEC = importlib.util.spec_from_file_location("scoped_primary_reader", READER)
PRIOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRIOR)
compact = PRIOR.compact


def extract(pdf):
    evidence, layouts, rejected, first = PRIOR.extract(pdf)
    pages, _ = PRIOR.geometric_pages(pdf)
    ends, offset = [], 0
    for page in pages:
        offset += len(page)
        ends.append(offset)
    full = "".join(pages)
    headings = list(re.finditer(r"(?m)^\s*[一二三四五六七八九十]+、\s*", full))
    for index, heading in enumerate(headings):
        start = heading.end()
        stop = headings[index + 1].start() if index + 1 < len(headings) else len(full)
        positions = [pos for pos in range(start, stop) if not full[pos].isspace()]
        text = "".join(full[pos] for pos in positions)
        if not text.startswith("A股权益分派方案"):
            continue
        for match in re.finditer(
            r"向全体股东每10股派(?:发)?人民币(?P<amount>\d+(?:\.\d+)?)元\(含税", text
        ):
            prefix = re.split(r"[。；;]", text[: match.start()])[-1]
            excluded = [
                "前次",
                "此前",
                "已派",
                "全年",
                "累计",
                "H股",
                "税后",
                "除权",
                "折算",
                "拟",
                "预案",
            ]
            roles = [word for word in excluded if word in prefix]
            page = bisect.bisect_right(ends, positions[match.start()]) + 1
            last = bisect.bisect_right(ends, positions[match.end() - 1]) + 1
            if page != last:
                roles.append("UNREVIEWED_CURRENCY_PAGE_BOUNDARY")
            if roles:
                rejected.append({"page": page, "role": roles, "text": match[0]})
                continue
            evidence.setdefault("cash_per_share", []).append(
                {
                    "value": str(Decimal(match["amount"]) / 10),
                    "page": page,
                    "pages": [page],
                    "method": "explicit_renminbi_distribution",
                    "title": "A股权益分派方案",
                    "context": text[max(0, match.start() - 180) : match.end()],
                    "parts": [{"page": page, "text": match[0]}],
                }
            )
            layouts.append({"page": page, "family": "explicit_renminbi_distribution"})
    return evidence, layouts, rejected, first
