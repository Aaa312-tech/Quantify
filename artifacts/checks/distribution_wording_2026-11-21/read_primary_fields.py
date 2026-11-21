"""Independently read geometric date cells and current gross-cash paragraphs."""

import bisect
import importlib.util
import re
from decimal import Decimal
from pathlib import Path

import fitz

READER = Path.cwd() / "artifacts/checks/distribution_terms_2026-11-21/independent_fields.py"
SPEC = importlib.util.spec_from_file_location("dated_primary_fields", READER)
PRIOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRIOR)


def extract(pdf):
    evidence, layouts, rejected, first = PRIOR.extract(pdf)
    pages = []
    ends = []
    with fitz.open(pdf) as document:
        for page in document:
            pages.append(page.get_text(sort=True) + "\n")
            ends.append(sum(map(len, pages)))
    full = "".join(pages)
    headings = list(re.finditer(r"(?m)^\s*[一二三四五六七八九十]+、", full))
    titles = {
        "权益分派方案",
        "权益分配方案",
        "利润分配方案",
        "分配方案",
        "分红派息方案",
        "利润分配及资本公积金转增股本方案",
        "分配及转增股本方案",
        "分配、转增股本方案",
        "利润分配及转增股本方案",
    }
    for index, heading in enumerate(headings):
        start = heading.end()
        stop = headings[index + 1].start() if index + 1 < len(headings) else len(full)
        positions = [n for n in range(start, stop) if not full[n].isspace()]
        text = PRIOR.compact(full[start:stop])
        end = text.find("方案", 0, 55)
        if end < 0:
            continue
        title = text[: end + 2]
        normal = title.removeprefix("本次实施的").removeprefix("本次")
        normal = re.sub(r"^20\d{2}年?(?:年度|半年度|中期|前三季度|第[一二三四]季度)?", "", normal)
        if normal not in titles:
            continue
        # Collect amounts before seeing the expected provider value. Parentheses
        # can only define the same yuan unit; financial qualifications stay intact.
        pattern = (
            r"每(?P<unit>10股|股)(?P<words>[^。；;每元,，]{0,42}?)"
            r"(?P<amount>\d+(?:\.\d+)?)元(?:人民币)?"
            r"(?P<definition>\(以下简称[“\"]元[”\"]\))?"
            r"(?P<tail>[^()元。；;,，]{0,8})\(含税"
        )
        for match in re.finditer(pattern, text):
            if "现金" not in match["words"] + match["tail"]:
                continue
            prefix = re.split(r"[。；;]", text[: match.start()])[-1]
            for group in re.findall(r"\([^()]*\)", prefix):
                if re.fullmatch(r"其中A股[0-9,]+股,H股[0-9,]+股", group[1:-1]):
                    prefix = prefix.replace(group, "")
            excluded = [
                "已派",
                "前次",
                "上次",
                "此前",
                "全年",
                "累计",
                "H股",
                "税后",
                "除权",
                "折算",
            ]
            if not prefix.endswith(("向全体股东", "向全体股东按")):
                excluded += ["预案", "拟"]
            roles = [word for word in excluded if word in prefix]
            page = bisect.bisect_right(ends, positions[match.start()]) + 1
            if roles:
                rejected.append({"page": page, "role": roles, "text": match[0]})
                continue
            amount = Decimal(match["amount"]) / (10 if match["unit"] == "10股" else 1)
            family = (
                "currency_definition"
                if match["definition"]
                else "cash_dividend_noun"
                if "股息" in match["words"]
                else "cash_distribution_verb"
                if any(w in match["words"] for w in ("分配", "派送"))
                else "dated_heading"
                if re.search(r"20\d{2}", title)
                else "equity_distribution_heading"
                if "权益分配" in title
                else "existing_gross_paragraph"
            )
            evidence.setdefault("cash_per_share", []).append(
                {
                    "value": str(amount),
                    "page": page,
                    "method": family,
                    "context": text[max(0, match.start() - 100) : match.end()],
                    "title": title,
                }
            )
            layouts.append({"page": page, "family": family})
    return evidence, layouts, rejected, first
