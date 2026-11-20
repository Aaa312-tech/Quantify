"""Archive issuer notices for every unresolved event in the frozen stock universe."""

from __future__ import annotations

import html
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .dataset import load_bundle
from .storage import digest, log, now, save, sha

LOOKUP_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
DOCUMENT_ROOT = "https://static.cninfo.com.cn/"
PAGE_SIZE = 30
QUERY_TOPICS = {
    "分": "distributions",
    "转增": "capitalization",
    "送股": "bonus_shares",
    "牌": "suspensions",
}


def source_queue(bundle):
    """Select missing facts by input coverage, without consulting returns or holdings."""
    actions = []
    for action in bundle["actions"]:
        missing = []
        if action["cash_per_share"] > 0 and not action.get("cash_pay_date"):
            missing.append("cash_pay_date")
        if action["share_multiplier"] > 1 and not action.get("share_list_date"):
            missing.append("share_list_date")
        if missing:
            actions.append({**action, "missing_fields": missing})

    calendar = bundle["calendar"]
    gaps = []
    reviewed = 0
    for symbol, raw in sorted(bundle["raws"].items()):
        positions = calendar.get_indexer(raw.index)
        if (positions < 0).any() or not raw.index.is_monotonic_increasing:
            raise ValueError("Raw history does not align with the frozen calendar")
        for left, right in zip(positions[:-1], positions[1:]):
            if right - left <= 1:
                continue
            anchor, resume = (str(calendar[index].date()) for index in (left, right))
            status = bundle.get("opening_status", {}).get(resume, {}).get(symbol, {})
            if (
                status.get("review_sha256")
                and status.get("suspended") is False
                and status.get("reference_date") == anchor
            ):
                reviewed += 1
                continue
            gaps.append(
                {
                    "symbol": symbol,
                    "last_observed_session": anchor,
                    "next_observed_session": resume,
                    "missing_sessions": [str(day.date()) for day in calendar[left + 1 : right]],
                }
            )
    terms = defaultdict(set)
    for action in actions:
        terms[action["symbol"]].add("分")
        if "share_list_date" in action["missing_fields"]:
            if action.get("capitalization_per_share", 0) > 0:
                terms[action["symbol"]].add("转增")
            if action.get("bonus_per_share", 0) > 0:
                terms[action["symbol"]].add("送股")
    for gap in gaps:
        terms[gap["symbol"]].add("牌")
    return {
        "actions": actions,
        "gaps": gaps,
        "already_reviewed_gap_endpoints": reviewed,
        "queries": [
            {"symbol": symbol, "term": term}
            for symbol, selected in sorted(terms.items())
            for term in sorted(selected)
        ],
        "scope": "Missing payment/listing dates and internal quote gaps; no inferred facts or fills.",
    }


def notice_kind(title):
    title = html.unescape(re.sub(r"<[^>]+>", "", title)).replace(" ", "")
    if any(word in title for word in ("停牌", "复牌")) and "公告" in title:
        return "suspension"
    distribution = any(word in title for word in ("分配", "分派", "派息", "转增", "送股"))
    if distribution and "公告" in title:
        if "实施" in title:
            return "distribution"
        if any(word in title for word in ("更正", "补充", "修订", "调整", "取消", "终止")):
            return "amendment"
    return None


def parse_page(payload, org_id, term, start, end):
    """Verify issuer identity and pagination; catalogue dates are not publication times."""
    total, more = payload.get("totalAnnouncement"), payload.get("hasMore")
    rows = payload.get("announcements")
    if type(total) is not int or total < 0 or type(more) is not bool:
        raise ValueError("Announcement index has no valid total or pagination flag")
    if rows is None and total == 0:
        rows = []
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE or (more and not rows):
        raise ValueError("Announcement page is empty or malformed")
    records = []
    for row in rows:
        title = html.unescape(re.sub(r"<[^>]+>", "", str(row.get("announcementTitle", ""))))
        identifier = str(row.get("announcementId", ""))
        if row.get("orgId") != org_id or not re.fullmatch(r"\d{6}", str(row.get("secCode", ""))):
            raise ValueError("Announcement belongs to an unexpected issuer")
        if not identifier.isdigit() or term not in title:
            raise ValueError("Announcement identity or title filter is inconsistent")
        relative = row.get("adjunctUrl", "")
        if not re.fullmatch(r"finalpage/\d{4}-\d{2}-\d{2}/[\w-]+\.[Pp][Dd][Ff]", relative):
            raise ValueError("Announcement has an unsupported document path")
        date = str(
            pd.Timestamp(row["announcementTime"], unit="ms", tz="UTC")
            .tz_convert("Asia/Shanghai")
            .date()
        )
        if not start <= date <= end or relative.split("/")[1] != date:
            raise ValueError("Announcement date falls outside its registered query")
        records.append(
            {
                "id": identifier,
                "published_code": row["secCode"],
                "org_id": org_id,
                "title": title,
                "catalog_date": date,
                "url": DOCUMENT_ROOT + relative,
                "kind": notice_kind(title),
            }
        )
    return records, total, more


def _download(url, form=None):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"}
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(
        url, data=urlencode(form).encode() if form is not None else None, headers=headers
    )
    with urlopen(request, timeout=25) as response:
        content = response.read(25_000_001)
    if len(content) > 25_000_000:
        raise ValueError("Source exceeds the registered 25 MB response limit")
    return content


def _capture(root, target, url, form=None):
    record = {"url": url, "form": form, "attempts": []}
    for attempt in range(3):
        trace = {"requested_at": now()}
        try:
            content = _download(url, form)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
            trace["status"] = "RECEIVED"
            record.update(
                status="RECEIVED", path=target.relative_to(root).as_posix(), sha256=sha(target)
            )
        except (OSError, URLError, ValueError) as error:
            trace.update(status="FAILED", error=f"{type(error).__name__}: {error}")
            record["status"] = "FAILED"
            if isinstance(error, HTTPError):
                trace["http_status"] = error.code
                record["access_blocked"] = error.code in (401, 403, 429)
        trace["completed_at"] = now()
        record["attempts"].append(trace)
        if record["status"] == "RECEIVED" or record.get("access_blocked"):
            break
        if attempt < 2:
            time.sleep(attempt + 1)
    time.sleep(0.2)
    return record


def _collect_query(root, target, query, issuer, start, end):
    form = {
        "pageSize": str(PAGE_SIZE),
        "column": "",
        "tabName": "fulltext",
        "stock": issuer["code"] + "," + issuer["orgId"],
        "searchkey": query["term"],
        "secid": "",
        "plate": "",
        "category": "",
        "trade": "",
        "seDate": start + "~" + end,
        "sortName": "time",
        "sortType": "asc",
        "isHLtitle": "false",
    }
    result = {**query, "org_id": issuer["orgId"], "pages": [], "records": [], "status": "FAILED"}
    seen = set()
    expected = None
    try:
        for page in range(1, 101):
            form["pageNum"] = str(page)
            request = _capture(root, target / f"page_{page:03d}.json", QUERY_URL, dict(form))
            result["pages"].append(request)
            if request["status"] != "RECEIVED":
                result["access_blocked"] = request.get("access_blocked", False)
                raise ValueError("Announcement page could not be archived")
            payload = json.loads((root / request["path"]).read_bytes())
            records, total, more = parse_page(payload, issuer["orgId"], query["term"], start, end)
            if expected is not None and total != expected:
                raise ValueError("Announcement count changed between pages")
            expected = total
            for notice in records:
                key = (notice["id"], notice["published_code"])
                if key in seen:
                    raise ValueError("Duplicate announcement across index pages")
                seen.add(key)
            result["records"].extend(records)
            if len(seen) > total or (not more and len(seen) != total):
                raise ValueError("Announcement pagination did not cover its reported total")
            if not more:
                result.update(status="COMPLETE", total=total)
                break
        else:
            raise ValueError("Announcement query exceeded 100 pages")
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    result["completed_at"] = now()
    save(target / "query.json", result, immutable=True)
    return result


def collect_disclosures(paths, output):
    """Collect a complete, bounded source queue without changing research inputs."""
    target = (paths.root / output).resolve()
    parent = (paths.root / "data/raw").resolve()
    if (
        not parent.is_relative_to(paths.root)
        or target == parent
        or not target.is_relative_to(parent)
    ):
        raise ValueError("Disclosure collection needs a named directory under data/raw")
    if target.exists():
        raise FileExistsError(f"Disclosure collection already exists: {target}")
    bundle = load_bundle(paths)
    queue = source_queue(bundle)
    start = str((bundle["calendar"][0] - pd.Timedelta(days=31)).date())
    end = str(bundle["cutoff"])[:10]
    save(target / "queue.json", queue, immutable=True)
    protocol = {
        "registered_at": now(),
        "bundle_sha256": sha(paths.bundle),
        "queue_sha256": digest(queue),
        "collector_sha256": sha(Path(__file__)),
        "start": start,
        "end": end,
        "lookup_url": LOOKUP_URL,
        "query_url": QUERY_URL,
        "max_pages_per_query": 100,
        "page_size": PAGE_SIZE,
        "attempts_per_request": 3,
        "scope": "Keyword catalogue and primary-document collection for the whole unresolved input queue. No facts are certified or applied; catalogue dates do not establish pre-open availability.",
        "selection": "Every missing cash payment/share listing date and unreviewed internal gap, independent of holdings, predictions and returns. Further source searches are required for unmatched or ambiguous events.",
    }
    save(target / "protocol.json", protocol, immutable=True)
    lookup = _capture(paths.root, target / "stock_lookup.json", LOOKUP_URL)
    save(target / "stock_lookup_receipt.json", lookup, immutable=True)
    if lookup["status"] != "RECEIVED":
        raise ValueError("Issuer lookup failed; preserved request evidence")
    stocks = json.loads((target / "stock_lookup.json").read_bytes())["stockList"]
    by_code = defaultdict(list)
    for row in stocks:
        by_code[row["code"]].append(row)
    results, documents = [], {}
    blocked = False
    for number, query in enumerate(queue["queries"], 1):
        issuer = by_code.get(query["symbol"][2:], [])
        folder = target / "indexes" / query["symbol"] / QUERY_TOPICS[query["term"]]
        if len(issuer) != 1:
            result = {**query, "status": "FAILED", "error": "Issuer lookup is absent or ambiguous"}
            save(folder / "query.json", result, immutable=True)
        else:
            result = _collect_query(paths.root, folder, query, issuer[0], start, end)
        results.append(
            {
                **query,
                "status": result["status"],
                "path": (folder / "query.json").relative_to(paths.root).as_posix(),
            }
        )
        if result["status"] == "COMPLETE":
            for notice in result["records"]:
                if notice["kind"] is None:
                    continue
                prior = documents.setdefault(notice["id"], {**notice, "requested_symbols": []})
                if prior["url"] != notice["url"] or prior["org_id"] != notice["org_id"]:
                    raise ValueError("The same document identifier has conflicting issuer metadata")
                if query["symbol"] not in prior["requested_symbols"]:
                    prior["requested_symbols"].append(query["symbol"])
        if number % 20 == 0:
            log(
                "disclosure indexes",
                completed=number,
                total=len(queue["queries"]),
                documents=len(documents),
            )
        if result.get("access_blocked"):
            blocked = True
            break
    downloads = []
    if not blocked:
        for number, notice in enumerate(documents.values(), 1):
            file = target / "documents" / (notice["id"] + ".pdf")
            receipt = _capture(paths.root, file, notice["url"])
            if receipt["status"] == "RECEIVED" and not file.read_bytes().startswith(b"%PDF"):
                receipt.update(status="INVALID_DOCUMENT", error="Source response is not a PDF")
            receipt = {**notice, **receipt, "review_status": "NOT_REVIEWED"}
            save(file.with_suffix(".json"), receipt, immutable=True)
            downloads.append(
                {
                    "id": notice["id"],
                    "status": receipt["status"],
                    "receipt": file.with_suffix(".json").relative_to(paths.root).as_posix(),
                }
            )
            if number % 50 == 0:
                log("issuer documents", completed=number, total=len(documents))
            if receipt.get("access_blocked"):
                blocked = True
                break
    complete = (
        not blocked
        and len(results) == len(queue["queries"])
        and all(row["status"] == "COMPLETE" for row in results)
        and len(downloads) == len(documents)
        and all(row["status"] == "RECEIVED" for row in downloads)
    )
    result = {
        "status": "COLLECTED" if complete else "PARTIAL",
        "completed_at": now(),
        "source_bundle_sha256": protocol["bundle_sha256"],
        "queue_sha256": protocol["queue_sha256"],
        "action_events": len(queue["actions"]),
        "gap_endpoints": len(queue["gaps"]),
        "requested_queries": len(queue["queries"]),
        "query_status": dict(Counter(row["status"] for row in results)),
        "queries": results,
        "selected_documents": len(documents),
        "documents": downloads,
        "document_status": dict(Counter(row["status"] for row in downloads)),
        "access_blocked": blocked,
        "facts_applied": 0,
        "quality_flags_cleared": 0,
        "scope": "Source acquisition only. Complete pagination does not prove event coverage or a valid historical publication timestamp.",
    }
    if sha(paths.bundle) != protocol["bundle_sha256"]:
        raise ValueError("Active dataset changed during source acquisition")
    save(target / "collection.json", result, immutable=True)
    from .planning import register_output

    register_output(paths, target)
    return {key: value for key, value in result.items() if key not in ("queries", "documents")}
