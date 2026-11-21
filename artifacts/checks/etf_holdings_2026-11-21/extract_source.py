"""Locate original ETF portfolio sections and cross-check report identity and dates."""

import bisect
import datetime as dt
import re
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import dump_gzip, now, read, save, sha

ROOT = Path.cwd()
P = read(ROOT / "configs/protocols/etf_holdings.json")
OUT = ROOT / P["processed_output"]
CHECKS = ROOT / P["review_output"]
DATE = r"([〇零○一二三四五六七八九十\d]{4})年+([〇零○一二三四五六七八九十\d]{1,3})月+([〇零○一二三四五六七八九十\d]{1,3})日+"


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def date_value(parts):
    digits = {char: str(index) for index, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": "0", "○": "0"})

    def number(value):
        if "十" in value:
            left, right = value.split("十")
            return int(digits.get(left, left) or "1") * 10 + int(digits.get(right, right) or "0")
        return int("".join(digits.get(char, char) for char in value))

    return dt.date(*map(number, parts)).isoformat()


def identity(texts, row):
    cover = compact(texts[0])
    year = str(row["report_year"])
    period = {
        "annual": "年度",
        "interim": "中期",
        "quarterly": "第" + str(row["quarter"]) + "季度",
    }[row["report_type"]]
    normalized = cover
    for index, chinese in enumerate("一二三四", 1):
        normalized = normalized.replace("第" + chinese + "季度", "第" + str(index) + "季度")
    normalized = normalized.replace("半年度报告", "中期报告")
    if year + "年" + period + "报告" not in normalized:
        raise ValueError("Cover report type/year differs from catalogue")
    deliveries = {
        date_value(parts) for parts in re.findall(r"(?:报告)?送出日期[:：]?" + DATE, cover)
    }
    if len(deliveries) != 1:
        raise ValueError("Missing or ambiguous cover delivery date")
    delivery = next(iter(deliveries))
    before_delivery = re.split(r"(?:报告)?送出日期", cover)[0]
    dates = [date_value(parts) for parts in re.findall(DATE, before_delivery)]
    if row["title_period_end"] not in dates:
        raise ValueError("Report-period end missing from cover")
    all_text = compact("\n".join(texts))
    code_matches = re.findall(
        r"(?:基金主代码|基金代码|交易代码|基金交易代码)[:：]?(\d{6})", all_text
    )
    if row["symbol"][2:] not in code_matches:
        raise ValueError("Fund code is not in a labelled identity field")
    if delivery <= row["title_period_end"]:
        raise ValueError("Delivery does not follow reporting period")
    return {
        "report_type": row["report_type"],
        "period_end": row["title_period_end"],
        "delivery_date": delivery,
        "labelled_fund_codes": sorted(set(code_matches)),
    }


def extract(row):
    path = ROOT / row["download"]["path"]
    target = OUT / "pages" / row["symbol"] / (path.stem + ".json.gz")
    if target.exists():
        raise FileExistsError("Do not overwrite a prior extraction: " + str(target))
    result = {
        k: row[k]
        for k in (
            "symbol",
            "title",
            "catalogue_date",
            "url",
            "report_type",
            "title_period_end",
            "publication_notice",
        )
    }
    result.update(source=row["download"]["path"], source_sha256=sha(path), status="UNREVIEWED")
    if result["source_sha256"] != row["download"]["sha256"]:
        raise ValueError("Download receipt differs")
    with fitz.open(path) as document:
        reader = PdfReader(path)
        if len(reader.pages) != len(document):
            raise ValueError("PDF reader page counts differ")
        texts = [page.get_text() for page in document]
        result.update(pages=len(texts), pdf_metadata=document.metadata)
        if row["publication_notice"]:
            pages = [
                {"page": i + 1, "fitz": text, "pypdf": reader.pages[i].extract_text()}
                for i, text in enumerate(texts)
            ]
            result.update(status="NOTICE_ARCHIVED_NOT_PORTFOLIO", content_identity_reviewed=False)
        else:
            offsets = [0]
            for text in texts:
                offsets.append(offsets[-1] + len(compact(text)))
            joined = "".join(map(compact, texts))
            starts = list(re.finditer(r"(?:§)?([578])(?:投资组合报告)+\1\.1", joined))
            split_starts = []
            if not starts:
                for index, text in enumerate(texts):
                    for heading in re.finditer(
                        r"(?m)^\s*(?:§\s*)?([578])\s*(?:投资组合报告\s*)+$", text
                    ):
                        chapter = heading[1]
                        location = joined.find(
                            compact(heading[0]), offsets[index], offsets[index + 1]
                        )
                        tail = joined[location + len(compact(heading[0])) :]
                        if chapter + ".1" not in tail[:1000]:
                            continue
                        split_starts.append((index, chapter, location + len(compact(heading[0]))))
            identity_pages = min(10, len(texts))
            pypdf_cover = [reader.pages[i].extract_text() for i in range(identity_pages)]
            errors = []
            try:
                a, b = identity(texts[:identity_pages], row), identity(pypdf_cover, row)
                if a != b:
                    raise ValueError("Independent identity fields differ")
                result["identity"] = a
                result["catalogue_delivery_difference_days"] = (
                    dt.date.fromisoformat(row["catalogue_date"])
                    - dt.date.fromisoformat(a["delivery_date"])
                ).days
            except Exception as error:
                errors.append(f"identity: {error}")
            if len(starts) == 1 or len(split_starts) == 1:
                if starts:
                    match = starts[0]
                    chapter = int(match[1])
                    start_page = bisect.bisect_right(offsets, match.start()) - 1
                    start_end = match.end()
                    result["section_locator"] = "heading followed by first subsection"
                else:
                    start_page, chapter, start_end = split_starts[0]
                    chapter = int(chapter)
                    result["section_locator"] = "standalone heading across page boundary"
                end_match = re.search(
                    r"(?:§)?" + str(chapter + 1) + r"(?:开放式基金份额变动|基金份额持有人信息)",
                    joined[start_end:],
                )
                if end_match is None:
                    errors.append("Portfolio closing chapter not found")
                    end_page = len(texts) - 1
                else:
                    end_page = bisect.bisect_right(offsets, start_end + end_match.start()) - 1
                result.update(
                    portfolio_chapter=chapter,
                    portfolio_first_page=start_page + 1,
                    portfolio_last_page=end_page + 1,
                )
                indices = sorted(set(range(identity_pages)) | set(range(start_page, end_page + 1)))
            else:
                errors.append(f"Expected one actual portfolio chapter, found {len(starts)}")
                indices = list(range(identity_pages))
            pages = [
                {
                    "page": i + 1,
                    "fitz": texts[i],
                    "pypdf": pypdf_cover[i]
                    if i < identity_pages
                    else reader.pages[i].extract_text(),
                }
                for i in indices
            ]
            for item in pages:
                if (
                    result.get("portfolio_first_page", len(texts) + 1)
                    <= item["page"]
                    <= result.get("portfolio_last_page", 0)
                ):
                    try:
                        found = document[item["page"] - 1].find_tables(strategy="lines_strict")
                        item["tables"] = [
                            {
                                "bbox": list(table.bbox),
                                "row_count": table.row_count,
                                "col_count": table.col_count,
                                "cells": table.extract(),
                                "preceding_text": document[item["page"] - 1].get_text(
                                    clip=fitz.Rect(
                                        0, 0, document[item["page"] - 1].rect.width, table.bbox[1]
                                    )
                                ),
                                "row_boxes": [list(row.bbox) for row in table.rows],
                                "cell_boxes": [
                                    [list(cell) if cell else None for cell in row.cells]
                                    for row in table.rows
                                ],
                            }
                            for table in found.tables
                        ]
                    except Exception as error:
                        item["table_error"] = f"{type(error).__name__}: {error}"
                        errors.append(f"Page {item['page']} grid extraction: {error}")
            result.update(
                status="IDENTITY_AND_SECTION_REVIEWED" if not errors else "REVIEW_REQUIRED",
                errors=errors,
            )
        dump_gzip(
            target,
            {
                "source": result["source"],
                "source_sha256": result["source_sha256"],
                "pages_in_document": len(document),
                "pages": pages,
            },
        )
        result.update(
            extracted_pages=target.relative_to(ROOT).as_posix(),
            extraction_sha256=sha(target),
            dual_reader_pages=len(pages),
        )
    return result


def extract_safe(row):
    try:
        return extract(row)
    except Exception as error:
        return {
            "symbol": row["symbol"],
            "title": row["title"],
            "url": row["url"],
            "source": row["download"]["path"],
            "source_sha256": row["download"]["sha256"],
            "status": "EXTRACTION_FAILED",
            "error": f"{type(error).__name__}: {error}",
        }


def main():
    if (CHECKS / "extraction.json").exists():
        raise FileExistsError("Preserve the earlier review")
    collection = read(ROOT / P["raw_output"] / "collection.json")
    received = [r for r in collection["documents"] if r["status"] == "RECEIVED"]
    # Retry receipts are appended explicitly, never replacing failed source records.
    retry_file = ROOT / "data/raw/etf_reports_retry_2026-11-21/collection.json"
    if retry_file.exists():
        retry = read(retry_file)
        received.extend(r for r in retry["documents"] if r["status"] == "RECEIVED")
    if len({r["url"] for r in received}) != len(received):
        raise ValueError("Multiple received copies require explicit provenance")
    if len(received) != 552:
        raise ValueError("Expected the entire registered report and notice inventory")
    save(
        CHECKS / "extraction_protocol.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(ROOT / "configs/protocols/etf_holdings.json"),
            "received_documents": len(received),
            "fitz_version": fitz.VersionBind,
            "pypdf_version": __import__("pypdf").__version__,
        },
        immutable=True,
    )
    results = []
    with ProcessPoolExecutor(max_workers=2) as pool:
        for index, result in enumerate(pool.map(extract_safe, received), 1):
            results.append(result)
            if index % 25 == 0 or result["status"] in ("REVIEW_REQUIRED", "EXTRACTION_FAILED"):
                print(
                    index,
                    "/",
                    len(received),
                    result["symbol"],
                    result["status"],
                    result.get("errors", []),
                    flush=True,
                )
    save(
        CHECKS / "extraction.json",
        {
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "counts": dict(Counter(r["status"] for r in results)),
            "documents": results,
            "model_inputs_changed": False,
            "holdings_cells_validated": False,
        },
        immutable=True,
    )
    print("FINISHED", Counter(r["status"] for r in results), flush=True)


if __name__ == "__main__":
    main()
