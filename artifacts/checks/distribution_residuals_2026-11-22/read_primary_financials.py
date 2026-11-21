"""Independently inventory narrative dates, gross cash and notice identity.

This is a source diagnostic, not financial acceptance or a runtime data repair.
The production distribution parser is deliberately not imported.
"""

import datetime as dt
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

import fitz

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/residual_narrative_review.json")
DATE = r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
DATE_PATTERNS = {
    "record_date": re.compile(r"股权登记日(?:为)?[:：]?" + DATE),
    "ex_date": re.compile(r"(?:除权除息|除权\(息\)|除息)日(?:为)?[:：]?" + DATE),
    "cash_pay_date": re.compile(
        r"(?:本公司|公司)此次委托中国结算深圳分公司代派的(?:A股股东)?现金红利将于"
        + DATE
        + r"通过股东托管证券公司\(或其他托管机构\)直接划入其资金账户"
    ),
    "share_list_date": re.compile(r"本次所转增的无限售条件流通股的起始交易日为" + DATE),
}
CASH = re.compile(
    r"每(?P<ten>10)?股(?P<description>(?:实际)?(?:派发|派送|发放|派|分配)?"
    r"(?:现金红利|现金股利|现金股息|现金分红|现金|人民币|红利|股利|合计|为|:){0,6})"
    r"(?P<amount>\d+(?:\.\d+)?)(?P<currency>美元|港元|元)"
    r"(?:人民币)?(?:现金)?\(含税\)"
)
SECTION = re.compile(r"^[一二三四五六七八九十]{1,3}[、.]")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    # Preserve token boundaries inside numbers; a line break cannot repair cash.
    return re.sub(
        r"\s+",
        lambda m: (
            " "
            if m.start()
            and m.end() < len(text)
            and text[m.start() - 1] in "0123456789.,"
            and text[m.end()] in "0123456789.,"
            else ""
        ),
        text,
    )


def page_lines(raw, number, total):
    lines = [line for line in raw.splitlines() if line.strip()]
    counters = []
    for index in sorted({0, len(lines) - 1}):
        if not lines:
            break
        text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", lines[index]))
        style = None
        if re.fullmatch(r"[-—–]" + str(number) + r"[-—–]", text):
            style = "paired_dash"
        elif text == f"第{number}页共{total}页":
            style = "chinese"
        elif text == f"{number}/{total}":
            style = "fraction"
        if style:
            counters.append({"line": index, "style": style, "text": lines[index]})
    return lines, counters


def document_text(pages):
    prepared = [page_lines(raw, number, len(pages)) for number, raw in enumerate(pages, 1)]
    styles = [rows[0]["style"] if len(rows) == 1 else None for _, rows in prepared]
    joins = [
        len(prepared[i][1]) <= 1 and len(prepared[i + 1][1]) <= 1 and styles[i] == styles[i + 1]
        for i in range(len(pages) - 1)
    ]
    text, locations, sections, removed = "", [], [], []
    for index, (lines, counters) in enumerate(prepared):
        joined = (index > 0 and joins[index - 1]) or (index < len(joins) and joins[index])
        drop = {row["line"] for row in counters} if joined else set()
        if drop:
            removed.extend({"page": index + 1, **row} for row in counters)
        body = normalize("\n".join(line for i, line in enumerate(lines) if i not in drop))
        if index:
            separator = "" if joins[index - 1] else "\u241c"
            if (
                not separator
                and text
                and body
                and text[-1] in "0123456789.,"
                and body[0] in "0123456789.,"
            ):
                separator = " "
            text += separator
            locations.extend([index] * len(separator))
        start = len(text)
        text += body
        locations.extend([index + 1] * len(body))
        search_from = 0
        for line in lines:
            heading = normalize(line)
            if not SECTION.match(heading):
                continue
            offset = body.find(heading, search_from)
            if offset >= 0:
                sections.append({"start": start + offset, "text": heading})
                search_from = offset + len(heading)
    return text, locations, sections, removed


def evidence(text, locations, start, end):
    return {
        "pages": sorted(set(locations[start:end])),
        "text": text[start:end],
        "context": text[max(0, start - 220) : min(len(text), end + 150)],
    }


def cash_scope(text, locations, sections, match):
    previous = [row for row in sections if row["start"] < match.start()]
    section = previous[-1]["text"] if previous else None
    prefix = text[max(0, match.start() - 250) : match.start()]
    sentence = re.split(r"[。；]", prefix)[-1]
    if match["currency"] != "元":
        return "OTHER_CURRENCY", section
    if re.search(r"除权|折算|虚拟分派|虚拟分红", sentence):
        return "EX_REFERENCE", section
    if section and re.search(r"审议|预案|通过.*方案", section):
        return "APPROVAL_OR_PROPOSAL", section
    if section and "方案" in section and re.search(r"权益分派|利润分配|分配", section):
        return "CURRENT_IMPLEMENTATION_SECTION", section
    if (
        locations[match.start()] == 1
        and not previous
        and re.search(r"每股分配比例|每股转增比例|本次.{0,20}(?:实施|利润分配|权益分派)", prefix)
    ):
        return "CURRENT_FIRST_PAGE_SUMMARY", section
    if re.search(r"本次(?:利润分配|权益分派|分派)[^。；]{0,220}$", prefix) and not re.search(
        r"拟|预案|如.*(?:变动|变化)", sentence
    ):
        return "EXPLICIT_CURRENT_DISTRIBUTION", section
    return "SCOPE_REQUIRES_REVIEW", section


def financial_inventory(pages):
    text, locations, sections, removed = document_text(pages)
    cash = []
    for match in CASH.finditer(text):
        scope, section = cash_scope(text, locations, sections, match)
        unit = 10 if match["ten"] else 1
        cash.append(
            {
                **evidence(text, locations, match.start(), match.end()),
                "amount": match["amount"],
                "per_shares": unit,
                "per_share": str(Decimal(match["amount"]) / unit),
                "currency": match["currency"],
                "gross_tax_qualification": "含税",
                "scope": scope,
                "section": section,
            }
        )
    dates = {}
    for field, pattern in DATE_PATTERNS.items():
        dates[field] = []
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 2) : match.start()]
            value = dt.date(*(int(match[key]) for key in ("year", "month", "day"))).isoformat()
            dates[field].append(
                {
                    **evidence(text, locations, match.start(), match.end()),
                    "value": value,
                    "share_class": "B" if prefix == "B股" else "NOT_B_LABELLED",
                }
            )
    return {"cash": cash, "dates": dates, "removed_counters": removed, "sections": sections}


def identity(pages, catalogue, symbol):
    first = re.sub(r"\s+", "", unicodedata.normalize("NFKC", pages[0]))
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", catalogue))
    labelled_codes = re.findall(r"(?:证券|股票)代码[:：]?(\d{6})(?!\d)", first)
    name_match = re.search(r"(?:证券|股票)简称[:：]?(.{1,20}?)(?=公告编号[:：]?)", first)
    alias = name_match[1] if name_match else None
    body_title = title
    if alias and body_title.startswith(alias):
        body_title = body_title[len(alias) :].lstrip(":：")
    title_matches = bool(body_title) and body_title in first
    amendment = bool(re.search(r"更正|修订|取消|延期|补充", title))
    if title_matches and not amendment and re.search(r"权益分派实施公告$", body_title):
        role = "IMPLEMENTATION"
    elif title_matches and not amendment and re.search(r"调整.*回购.*价格.*公告$", body_title):
        role = "REPURCHASE_PRICE"
    elif title_matches and not amendment and re.search(r"调整.*转股价格.*公告$", body_title):
        role = "CONVERTIBLE_PRICE"
    else:
        role = "REQUIRES_REVIEW"
    return {
        "labelled_security_codes": labelled_codes,
        "issuer_matches": symbol[2:] in labelled_codes,
        "catalogue_title": catalogue,
        "labelled_short_name": alias,
        "body_title": body_title,
        "exact_first_page_title": title_matches,
        "role": role,
        "has_amendment_word": amendment,
        "first_page_text": pages[0],
    }


def fixture_checks():
    checks = []
    for pages, expected in [
        (["二、权益分派方案\n每10股派2.50元人民币现金（含税）"], "0.25"),
        (["二、分配方案\n每股派发现金红利0.25元（含税）"], "0.25"),
        (["二、分配方案\n每股派发现金红利0.\n25元（含税）"], None),
        (["二、分配方案\n每股派发现金\n第1页共2页", "红利0.25元（含税）\n第2页共2页"], "0.25"),
        (["二、分配方案\n每股派发现金红利0.\n第1页共2页", "25元（含税）\n第2页共2页"], None),
        (["二、分配方案\n每股派发现金\n123", "红利0.25元（含税）\n456"], None),
    ]:
        result = financial_inventory(pages)["cash"]
        actual = result[0]["per_share"] if result else None
        require(actual == expected, f"Gross cash fixture failed: {pages}")
        checks.append({"pages": pages, "expected": expected, "actual": actual})
    for raw, expected in [
        (
            "一、股东大会审议通过的利润分配方案\n每股派发现金红利0.25元（含税）",
            "APPROVAL_OR_PROPOSAL",
        ),
        ("二、分配方案\n除权除息计算折算每10股现金红利为2.40元（含税）", "EX_REFERENCE"),
        ("二、分配方案\n每股派发现金红利0.25美元（含税）", "OTHER_CURRENCY"),
    ]:
        actual = financial_inventory([raw])["cash"][0]["scope"]
        require(actual == expected, "Cash scope fixture failed")
        checks.append({"pages": [raw], "expected": expected, "actual": actual})
    return checks


def main():
    protocol = read(PROTOCOL)
    parent_path = protocol["parent_protocol"]["path"]
    require(sha(parent_path) == protocol["parent_protocol"]["sha256"], "Protocol changed")
    require(
        sha("src/quantfolio/distributions.py") == protocol["production_reader_sha256"],
        "Reader changed",
    )
    require(sha(BASE / "source_text.json.gz") == protocol["candidate_text_sha256"], "Text changed")
    require(
        sha(BASE / "candidate_table_review.json") == protocol["table_review_sha256"],
        "Grid review changed",
    )
    output_text = BASE / "primary_financial_text.json.gz"
    output = BASE / "primary_financial_review.json"
    require(not output.exists() and not output_text.exists(), "Refusing to overwrite diagnostic")
    fixtures = fixture_checks()
    parent = read(parent_path)
    frozen = load_gzip(BASE / "source_text.json.gz")["documents"]
    tables = {
        (r["symbol"], r["ex_date"]): r
        for r in read(BASE / "candidate_table_review.json")["records"]
    }
    documents = {}
    for number, source in enumerate(protocol["documents"], 1):
        path = source["source"]
        require(sha(path) == source["sha256"], "PDF changed: " + path)
        require(frozen[path]["source"]["sha256"] == source["sha256"], "Frozen identity mismatch")
        with fitz.open(path) as pdf:
            pages = [page.get_text(sort=False) for page in pdf]
        pypdf = frozen[path]["readers"]["pypdf"]["pages"]
        require(len(pages) == len(pypdf), "Reader page-count mismatch")
        documents[path] = {
            "source": source,
            "readers": {"pypdf": pypdf, "mupdf_unsorted": pages},
            "inventories": {
                name: financial_inventory(value)
                for name, value in {"pypdf": pypdf, "mupdf_unsorted": pages}.items()
            },
            "identities": {
                name: identity(value, source["catalogue_title"], source["records"][0]["symbol"])
                for name, value in {"pypdf": pypdf, "mupdf_unsorted": pages}.items()
            },
        }
        if number % 25 == 0 or number == len(protocol["documents"]):
            print(
                "COMPLETE_PRIMARY_AND_COMPANION_FILES",
                number,
                "of",
                len(protocol["documents"]),
                flush=True,
            )
    records, counts = [], Counter()
    for record in parent["records"]:
        path = record["source"]["path"]
        doc = documents[path]
        expected_cash = Decimal(record["source_identity_fields"]["cash_per_share"])
        provider_cash = Decimal(record["provider"]["cash_per_share"])
        require(
            sha(record["provider"]["source"]) == record["provider"]["source_sha256"],
            "Provider changed",
        )
        cash_checks = {}
        for reader, inventory in doc["inventories"].items():
            current = [
                row
                for row in inventory["cash"]
                if row["scope"].startswith("CURRENT_")
                or row["scope"] == "EXPLICIT_CURRENT_DISTRIBUTION"
            ]
            conflicting_unscoped = [
                row
                for row in inventory["cash"]
                if row["scope"] == "SCOPE_REQUIRES_REVIEW"
                and abs(Decimal(row["per_share"]) - expected_cash) > Decimal("1e-12")
            ]
            agrees = (
                bool(current)
                and not conflicting_unscoped
                and all(
                    abs(Decimal(row["per_share"]) - expected_cash) <= Decimal("1e-12")
                    and abs(Decimal(row["per_share"]) - provider_cash) <= Decimal("1e-12")
                    for row in current
                )
            )
            cash_checks[reader] = {
                "status": "CURRENT_GROSS_CASH_AGREES"
                if agrees
                else "CASH_SCOPE_OR_VALUE_REQUIRES_REVIEW",
                "current": current,
                "conflicting_unscoped": conflicting_unscoped,
                "recognized_statements": len(inventory["cash"]),
            }
            counts[cash_checks[reader]["status"]] += 1
        date_checks = []
        grid = tables[(record["symbol"], record["ex_date"])]
        for check in grid["field_checks"]:
            field = check["field"]
            reader_evidence = {
                name: inv["dates"][field] for name, inv in doc["inventories"].items()
            }
            if check["status"] == "LABELLED_GRID_AGREES":
                status = "PREVIOUS_INDEPENDENT_GRID_AGREES"
            elif all(
                {r["value"] for r in rows if r["share_class"] != "B"} == {check["expected"]}
                for rows in reader_evidence.values()
            ):
                status = "TWO_READER_LABELLED_NARRATIVE_AGREES"
            else:
                status = "NARRATIVE_REQUIRES_REVIEW"
            counts[status] += 1
            date_checks.append(
                {
                    "field": field,
                    "expected": check["expected"],
                    "status": status,
                    "readers": reader_evidence,
                }
            )
        records.append(
            {
                "symbol": record["symbol"],
                "ex_date": record["ex_date"],
                "source": record["source"],
                "cash_checks": cash_checks,
                "date_checks": date_checks,
                "primary_identity": doc["identities"],
            }
        )
    dump_gzip(
        output_text,
        {
            "protocol_sha256": sha(PROTOCOL),
            "reader": {"name": "PyMuPDF", "version": fitz.VersionBind, "sort": False},
            "documents": documents,
        },
    )
    save(
        output,
        {
            "status": "INDEPENDENT_DIAGNOSTIC_COMPLETE_ACCEPTANCE_PENDING",
            "completed_at": now(),
            "checker_sha256": sha(__file__),
            "protocol_sha256": sha(PROTOCOL),
            "source_text_sha256": sha(output_text),
            "fixture_checks": fixtures,
            "document_count": len(documents),
            "physical_page_count": sum(len(d["readers"]["pypdf"]) for d in documents.values()),
            "records": records,
            "check_counts": dict(counts),
            "facts_accepted": 0,
            "limitations": "Narrative and grid checks are field-specific. Companion price scope, source/provider identity, related amendments and the full residual outcome review still gate financial acceptance. No PDF page has been visually reviewed by this script.",
        },
        immutable=True,
    )
    print("FINANCIAL_DIAGNOSTIC", dict(counts), flush=True)


if __name__ == "__main__":
    main()
