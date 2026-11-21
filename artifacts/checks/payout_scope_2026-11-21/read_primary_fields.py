"""Read geometric date cells and implementation paragraphs independently of production."""

import bisect
import importlib.util
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

import fitz

READER = Path.cwd() / "artifacts/checks/distribution_wording_2026-11-21/read_field_closure.py"
SPEC = importlib.util.spec_from_file_location("prior_primary_cells", READER)
PRIOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRIOR)
compact = PRIOR.compact


def scheme_title(value):
    title = compact(value)
    stop = title.find("方案", 0, 70)
    if stop < 0:
        return None
    title = title[: stop + 2]
    normal = title
    for prefix in ("本次实施的", "本次", "本公司", "公司"):
        normal = normal.removeprefix(prefix)
    normal = re.sub(
        r"^20\d{2}年?(?:年度(?:中期|末期)?|半年度|中期|前三季度|第[一二三四]季度)?"
        r"(?=(?:A股)?(?:权益|利润|分配|分红))",
        "",
        normal,
    ).removeprefix("A股")
    titles = {
        "权益分派方案",
        "权益分配方案",
        "利润分配方案",
        "利润分派方案",
        "分配方案",
        "分红派息方案",
        "利润分配及资本公积金转增股本方案",
        "分配及转增股本方案",
        "分配、转增股本方案",
        "利润分配及转增股本方案",
    }
    return (title, normal) if normal in titles else None


def geometric_pages(pdf):
    pages, markers = [], {}
    with fitz.open(pdf) as document:
        for number, page in enumerate(document, 1):
            lines = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = unicodedata.normalize("NFKC", "".join(s["text"] for s in line["spans"]))
                    lines.append((line["bbox"], text))
            lines.sort(key=lambda row: (round(row[0][1], 1), row[0][0]))
            kept = []
            for bbox, text in lines:
                counter = re.fullmatch(r"[-—–]\s*(\d+)\s*[-—–]", text.strip())
                margin = bbox[1] > page.rect.height * 0.9 or bbox[3] < page.rect.height * 0.08
                if counter and int(counter[1]) == number and margin:
                    markers.setdefault(number, []).append(
                        {
                            "page": number,
                            "text": text.strip(),
                            "bbox": list(bbox),
                            "page_size": [page.rect.width, page.rect.height],
                        }
                    )
                else:
                    kept.append(text)
            pages.append("\n".join(kept) + "\n")
    return pages, markers


def extract(pdf):
    evidence, layouts, rejected, first = PRIOR.extract(pdf)
    # Retain the explicitly A-share gross summary, but derive all paragraph amounts
    # afresh rather than inheriting the previous section-recognition assumptions.
    evidence["cash_per_share"] = [
        row
        for row in evidence.get("cash_per_share", [])
        if row.get("method") == "labelled_a_share_gross_summary"
    ]
    pages, markers = geometric_pages(pdf)
    ends, offset = [], 0
    for page in pages:
        offset += len(page)
        ends.append(offset)
    full = "".join(pages)
    major = list(re.finditer(r"(?m)^\s*[一二三四五六七八九十]+、\s*", full))
    first_major = major[0].start() if major else len(full)
    numbered = [
        match
        for match in re.finditer(r"(?m)^[ \t]*\d{1,2}[、.]\s*", full)
        if match.start() < first_major
    ]
    headings = sorted(major + numbered, key=lambda match: match.start())
    for index, heading in enumerate(headings):
        start = heading.end()
        stop = headings[index + 1].start() if index + 1 < len(headings) else len(full)
        positions = [pos for pos in range(start, stop) if not full[pos].isspace()]
        text = "".join(full[pos] for pos in positions)
        title = scheme_title(text)
        windows = []
        if title:
            windows.append((0, len(text), title[0], "qualified_current_heading"))
        else:
            for marker in re.finditer(
                r"(?:^|[、.。；;])(?P<title>公司(?:本次)?实施的[^:。；;]{1,64}方案):", text
            ):
                current = re.sub(r"^公司(?:本次)?实施的", "", marker["title"])
                if scheme_title(current):
                    end = text.find("。", marker.end())
                    windows.append(
                        (
                            marker.end(),
                            end if end >= 0 else len(text),
                            marker["title"],
                            "explicit_implementation_clause",
                        )
                    )
        for begin, end, current_title, family in windows:
            body = text[begin:end]
            pattern = (
                r"每(?P<unit>10股|股)(?P<words>[^0-9。；;每元,，()]{0,42}?)"
                r"(?P<amount>\d+(?:\.\d+)?)元(?:人民币)?"
                r"(?:\(以下简称[“\"]元[”\"]\))?(?P<tail>[^0-9()元。；;,，]{0,8})\(含税"
            )
            for match in re.finditer(pattern, body):
                if "现金" not in match["words"] + match["tail"]:
                    continue
                prefix = re.split(r"[。；;]", body[: match.start()])[-1]
                for group in re.findall(r"\([^()]*\)", prefix):
                    if re.fullmatch(r"其中A股[0-9,]+股,H股[0-9,]+股", group[1:-1]):
                        prefix = prefix.replace(group, "")
                if "公司本次利润分配以" in prefix:
                    for reference in re.finditer(r"审议通过的《([^》]+)》", prefix):
                        if reference[1].endswith("利润分配预案"):
                            prefix = prefix.replace(reference[0], "已审议的文件")
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
                begin_pos = begin + match.start()
                end_pos = begin + match.end()
                first_page = bisect.bisect_right(ends, positions[begin_pos]) + 1
                last_page = bisect.bisect_right(ends, positions[end_pos - 1]) + 1
                parts = []
                for number in range(first_page, last_page + 1):
                    segment = "".join(
                        text[pos]
                        for pos in range(begin_pos, end_pos)
                        if bisect.bisect_right(ends, positions[pos]) + 1 == number
                    )
                    parts.append({"page": number, "text": segment})
                method = family
                if last_page != first_page:
                    valid_boundary = (
                        last_page == first_page + 1
                        and re.fullmatch(r"每(?:10)?股", parts[0]["text"])
                        and parts[1]["text"].startswith(("派", "分配现金"))
                        and bool(markers.get(first_page)) == bool(markers.get(last_page))
                        and all(
                            len(markers.get(number, [])) <= 1 for number in (first_page, last_page)
                        )
                    )
                    if not valid_boundary:
                        roles.append("UNSUPPORTED_PAGE_BOUNDARY")
                    method = "geometric_adjacent_page_gross_cash"
                if roles:
                    rejected.append(
                        {
                            "page": first_page,
                            "role": roles,
                            "text": match[0],
                            "title": current_title,
                        }
                    )
                    continue
                amount = Decimal(match["amount"]) / (10 if match["unit"] == "10股" else 1)
                evidence["cash_per_share"].append(
                    {
                        "value": str(amount),
                        "page": first_page,
                        "pages": list(range(first_page, last_page + 1)),
                        "method": method,
                        "context": body[max(0, match.start() - 180) : match.end()],
                        "title": current_title,
                        "parts": parts,
                        "page_number_lines": [
                            row
                            for number in range(first_page, last_page + 1)
                            for row in markers.get(number, [])
                        ],
                    }
                )
                layouts.append(
                    {
                        "page": first_page,
                        "family": method,
                        "title": current_title,
                        "unit": match["unit"],
                    }
                )
    return evidence, layouts, rejected, first
