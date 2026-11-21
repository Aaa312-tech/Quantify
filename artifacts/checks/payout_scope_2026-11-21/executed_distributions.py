"""Review registered dividend sources without consulting predictions or account returns."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .dataset import check_integrity, load_snapshot
from .storage import digest, log, now, read, save, sha

DATE = r"2\s*0\s*\d\s*\d\s*(?:年|/|-)\s*\d{1,2}\s*(?:月|/|-)\s*\d{1,2}\s*日?(?!\d)"
EX_LABEL = r"(?:除权\s*(?:\(\s*息\s*\)|除息|息)?|除息)\s*日"
NUMBER = r"[0-9]+(?:\.[0-9]+)?"
SCHEME = (
    r"(?:本次(?:实施的)?\s*)?(?:(?:本)?公司\s*)?"
    r"(?:20\d{2}\s*年?\s*(?:年度(?:中期|末期)?|半年度|中期|前三季度|第[一二三四]季度)?\s*)?"
    r"(?:A\s*股\s*)?(?:权益分(?:派|配)|利润分(?:派|配)|分配|分红派息)"
    r"(?:(?:及|、)(?:资本公积金)?转增股本)?方案"
)
FIELDS = frozenset(
    (
        "cash_per_share",
        "cash_pay_date",
        "share_list_date",
        "ex_reference_cash_per_share",
        "cash_rounding",
    )
)


def _date(text):
    year, month, day = re.fullmatch(
        r"(2\s*0\s*\d\s*\d)\s*(?:年|/|-)\s*(\d{1,2})\s*(?:月|/|-)\s*(\d{1,2})\s*日?",
        text.strip(),
    ).groups()
    return dt.date(int(re.sub(r"\s", "", year)), int(month), int(day)).isoformat()


def _current_scheme(prefix):
    headings = list(re.finditer(r"[一二三四五六七八九十]+、", prefix))
    if not headings:
        headings = list(re.finditer(r"(?:^|\s)\d{1,2}[、.]\s*", prefix))
    title = prefix[headings[-1].end() :].strip() if headings else ""
    if re.match(SCHEME, title):
        return True
    # An approval-history section needs a separate, explicit implementation clause.
    clause = re.split(r"[。；;]", prefix)[-1]
    return (
        re.search(
            r"(?:^|[、，,:\s])(?:公司(?:本次)?实施的|本次实施的)\s*" + SCHEME + r"\s*[:：]",
            clause,
        )
        is not None
    )


def _current_payout(prefix, established_all_shareholder=False):
    clause = re.split(r"[。；;]", prefix)[-1]
    if "公司本次利润分配以" in clause:
        # A cited approved document is distinct from an amount that is still proposed.
        clause = re.sub(
            r"根据[^。；;]{0,120}?审议通过的\s*《[^》]{0,80}?利润分配预案》",
            "",
            clause,
        )
    # Outstanding A/H share counts describe the capital base, not the dividend class.
    clause = re.sub(
        r"\(其中\s*A\s*股\s*[\d,]+\s*股[,，]\s*H\s*股\s*[\d,]+\s*股\)",
        "",
        clause,
    )
    excluded = r"已派|前次|上次|此前|全年|累计|H\s*股|税后|除权|折算"
    # Approved implementation sections can retain proposal wording. Preserve the
    # old explicit all-shareholder grammar; expanded forms need narrower scope.
    if not established_all_shareholder:
        excluded += r"|预案|拟"
    return _current_scheme(prefix) and re.search(excluded, clause) is None


def _page_text(raw):
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw))
    return re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", text)


def _boundary_page(raw, number):
    lines = [line.strip() for line in unicodedata.normalize("NFKC", raw).splitlines()]
    occupied = [index for index, line in enumerate(lines) if line]
    markers = []
    for index in dict.fromkeys(occupied[:1] + occupied[-1:]):
        marker = re.fullmatch(r"[-—–]\s*(\d+)\s*[-—–]", lines[index])
        if marker:
            if int(marker[1]) != number:
                return None
            markers.append({"page": number, "line": index + 1, "text": lines[index]})
            lines[index] = ""
    if len(markers) > 1:
        return None
    return _page_text("\n".join(lines)).strip(), markers


def _cross_page_cash(pages):
    evidence, history = [], ""
    for page in range(1, len(pages)):
        left = _boundary_page(pages[page - 1], page)
        right = _boundary_page(pages[page], page + 1)
        if left is not None and right is not None and bool(left[1]) == bool(right[1]):
            subject = re.search(r"(?:每\s*10\s*股|(?:A\s*股)?每股)$", left[0])
            payout = re.match(
                r"(?:派(?:发|送)?现金(?:红利|股利|分红|股息)?|分配现金)"
                r"\s*(?:人民币\s*)?(" + NUMBER + r")\s*元\s*\(\s*含税",
                right[0],
            )
            if subject and payout and _current_payout(history + left[0][: subject.start()]):
                divisor = 10 if "10" in subject[0] else 1
                evidence.append(
                    {
                        "value": str(Decimal(payout[1]) / divisor),
                        "page": page,
                        "pages": [page, page + 1],
                        "text": subject[0] + " " + payout[0],
                        "parts": [
                            {"page": page, "text": subject[0]},
                            {"page": page + 1, "text": payout[0]},
                        ],
                        "page_number_lines": left[1] + right[1],
                    }
                )
        history += _page_text(pages[page - 1]) + " "
    return evidence


def extract_terms(pages):
    """Recognize only labelled dates and explicitly gross cash in supported layouts."""
    evidence = {}

    def add(field, value, page, excerpt):
        evidence.setdefault(field, []).append({"value": value, "page": page, "text": excerpt})

    history = ""
    for page, raw in enumerate(pages, 1):
        text = _page_text(raw)
        for field, label in (("record_date", "股权登记日"), ("ex_date", EX_LABEL)):
            for match in re.finditer(label + r"\s*(?:为\s*)?[:：]?\s*(" + DATE + ")", text):
                add(field, _date(match[1]), page, match[0])
        # Preserve spaces between table cells. Do not infer column order from date occurrence.
        header = (
            r"股份类别\s*股权登记日\s*最后交易日\s*"
            + EX_LABEL
            + r"\s*现金红利发放日\s*A\s*股\s*("
            + DATE
            + r")\s*[-—–−ー]\s*("
            + DATE
            + r")\s+("
            + DATE
            + ")"
        )
        for match in re.finditer(header, text):
            for field, value in zip(("record_date", "ex_date", "cash_pay_date"), match.groups()):
                add(field, _date(value), page, match[0])
        for listing in (False, True):
            labels = r"股权登记日\s*" + EX_LABEL + r"\s*"
            fields = ["record_date", "ex_date"]
            if listing:
                labels += r"新增无限售条件流通股份上市日\s*"
                fields.append("share_list_date")
            labels += r"现金红利发放日\s*"
            fields.append("cash_pay_date")
            dates = r"\s+".join("(" + DATE + ")" for _ in fields)
            for share_class in (False, True):
                pattern = (
                    r"股份类别\s*" + labels + r"A\s*股\s*" + dates
                    if share_class
                    else labels + dates
                )
                for match in re.finditer(pattern + r"(?!\s*" + DATE + ")", text):
                    for field, value in zip(fields, match.groups()):
                        add(field, _date(value), page, match[0])
        payment = (
            r"代派的\s*(?:A\s*股(?:股东)?\s*)?现金红利将于\s*("
            + DATE
            + r")[^。；;]{0,90}?直接划入其资金账户"
        )
        for match in re.finditer(payment, text):
            add("cash_pay_date", _date(match[1]), page, match[0])
        listing = (
            r"(?:本次(?:所)?送(?:\(转\)|转)股|新增无限售条件流通股份)的?上市日为[:：]?\s*("
            + DATE
            + ")"
        )
        for match in re.finditer(listing, text):
            add("share_list_date", _date(match[1]), page, match[0])
        amounts = (
            (
                r"A\s*股每股现金(?:红利|股利|分红|股息)\s*(?:人民币\s*)?("
                + NUMBER
                + r")\s*元\s*\(\s*含税",
                1,
            ),
            (
                r"(?:向全体股东(?:按)?)?每\s*10\s*股"
                r"(?:派(?:发|送)?\s*(?:现金(?:红利|股利|分红|股息)?\s*)?|分配\s*现金\s*)"
                r"(?:人民币\s*)?("
                + NUMBER
                + r")\s*元(?:人民币(?:\s*\(以下简称\s*[“\"]元[”\"]\s*\))?)?"
                r"\s*(?:现金\s*)?\(\s*含税",
                10,
            ),
            (
                r"本次利润分配[^。]{0,180}?每股派发现金(?:红利|股利|分红|股息)\s*(?:人民币\s*)?("
                + NUMBER
                + r")\s*元\s*\(\s*含税",
                1,
            ),
        )
        for pattern, divisor in amounts:
            for match in re.finditer(pattern, text):
                if divisor == 10:
                    if not _current_payout(
                        history + text[: match.start()], match[0].startswith("向全体股东")
                    ):
                        add(
                            "unscoped_cash_per_share",
                            str(Decimal(match[1]) / divisor),
                            page,
                            match[0],
                        )
                        continue
                add("cash_per_share", str(Decimal(match[1]) / divisor), page, match[0])
        per_share = (
            r"(?:A\s*股)?每股(?:派发\s*(?:20\d{2}\s*年\s*(?:中期|末期|年度)\s*)?)?"
            r"现金(?:红利|股利|分红|股息)\s*(?:人民币\s*)?(" + NUMBER + r")\s*元\s*\(\s*含税"
        )
        for match in re.finditer(per_share, text):
            prefix = history + text[: match.start()]
            if _current_payout(prefix):
                add("cash_per_share", str(Decimal(match[1])), page, match[0])
        history += text + " "
    cross_page = _cross_page_cash(pages)
    if cross_page:
        evidence.setdefault("cash_per_share", []).extend(cross_page)
    return evidence


def _unique(evidence, field):
    values = {item["value"] for item in evidence.get(field, [])}
    if field == "cash_per_share":
        values = {Decimal(value) for value in values}
    return next(iter(values)) if len(values) == 1 else None


def verified_terms(action, candidate, evidence):
    """Return field-scoped date facts only when primary and structured terms agree."""
    for field in ("symbol", "record_date", "ex_date"):
        if candidate.get(field) != action.get(field):
            return {}, "STRUCTURED_IDENTITY_MISMATCH"
    for field in ("record_date", "ex_date"):
        if _unique(evidence, field) != action.get(field):
            return {}, "PRIMARY_DATE_UNSUPPORTED_OR_CONFLICTING"
    amount = _unique(evidence, "cash_per_share")
    if amount is None or amount <= 0:
        return {}, "PRIMARY_GROSS_AMOUNT_UNSUPPORTED_OR_CONFLICTING"
    stored = Decimal(str(action["cash_per_share"]))
    provider = Decimal(str(candidate["cash_per_share"]))
    if any(abs(amount - other) > Decimal("1e-12") for other in (stored, provider)):
        return {}, "PRIMARY_GROSS_AMOUNT_MISMATCH"
    payment = _unique(evidence, "cash_pay_date")
    if payment != candidate.get("cash_pay_date") or not payment or payment < action["ex_date"]:
        return {}, "PRIMARY_PAYMENT_UNSUPPORTED_OR_CONFLICTING"
    if action.get("cash_pay_date") and action["cash_pay_date"] != payment:
        return {}, "EXISTING_PAYMENT_CONFLICT"
    fields = {"cash_pay_date": payment}
    listing = _unique(evidence, "share_list_date")
    if (
        action["share_multiplier"] > 1
        and listing
        and listing == candidate.get("share_list_date")
        and listing >= action["ex_date"]
        and action.get("share_list_date") in (None, listing)
    ):
        fields["share_list_date"] = listing
    return fields, "VERIFIED_FIELDS"


def source_available(candidate, action):
    """Catalogue dates are conservative retrospective bounds, not publication timestamps."""
    if "实施" not in candidate["title"] or any(
        term in candidate["title"]
        for term in ("更正", "修订", "调整", "补充", "取消", "提示", "预案")
    ):
        return None
    known = dt.date.fromisoformat(candidate["catalog_date"]) + dt.timedelta(days=1)
    return known.isoformat() + "T00:00:00+08:00" if str(known) <= action["ex_date"] else None


def checked_path(root, relative, expected):
    """Bind original documents and inputs before opening them."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or sha(path) != expected:
        raise ValueError(f"Source hash or path mismatch: {relative}")
    return path


def _compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def notice_role(symbol, candidate, pages):
    """Separate price notices only when their own issuer, title and body agree."""
    title = _compact(candidate["title"])
    result = {**candidate, "role": "UNKNOWN", "evidence": []}
    if not pages or not _compact(pages[0]):
        return {**result, "reason": "UNSUPPORTED_DOCUMENT"}
    first = _compact(pages[0])
    if title not in first:
        return {**result, "reason": "TITLE_NOT_IN_FIRST_PAGE"}
    codes = set(
        re.findall(
            r"(?:证券|股票)\s*代码\s*[:：]?\s*(\d{6})(?!\d)",
            unicodedata.normalize("NFKC", pages[0]),
        )
    )
    if symbol[-6:] not in codes:
        return {**result, "reason": "LABELLED_ISSUER_NOT_FOUND"}
    if any(word in title for word in ("更正", "修订", "补充", "取消", "终止", "撤销", "延期")):
        return {**result, "reason": "AMENDED_OR_WITHDRAWN_NOTICE"}
    distribution = any(word in title for word in ("分派", "分配", "分红", "派息"))
    if (
        distribution
        and title.endswith("实施公告")
        and not any(word in title for word in ("调整", "回购", "转股", "提示", "预案"))
    ):
        return {**result, "role": "IMPLEMENTATION", "reason": "FORMAL_IMPLEMENTATION_TITLE"}
    objects = {"REPURCHASE_PRICE": r"回购[^。；;]{0,16}?价格", "CONVERSION_PRICE": r"转股价格"}
    roles = [role for role, pattern in objects.items() if re.search(pattern, title)]
    if (
        not distribution
        or "调整" not in title
        or not title.endswith("公告")
        or "实施公告" in title
        or len(roles) != 1
    ):
        return {**result, "reason": "MIXED_OR_UNSUPPORTED_TITLE"}
    role = roles[0]
    for number, raw in enumerate(pages, 1):
        text = _compact(raw)
        if number == 1:
            text = text.split(title, 1)[1]
        for clause in re.split(r"[。；;]", text):
            if len(clause) > 400:
                continue
            if (
                re.search(objects[role], clause)
                and "调整" in clause
                and re.search(NUMBER + r"元(?:/股)?", clause)
            ):
                result["evidence"].append({"page": number, "text": clause})
    if not result["evidence"]:
        return {**result, "reason": "PRICE_ADJUSTMENT_BODY_NOT_SUPPORTED"}
    return {**result, "role": role, "reason": "PRICE_ADJUSTMENT_TITLE_AND_BODY"}


def resolve_companion_notices(symbol, candidates, documents):
    """Keep every candidate; mixed or ambiguous document sets cannot select a primary."""
    ids = [candidate["id"] for candidate in candidates]
    if len(ids) != len(set(ids)) or set(ids) != set(documents):
        raise ValueError("Candidate document identities differ or are duplicated")
    roles = [notice_role(symbol, c, documents[c["id"]]) for c in candidates]
    primaries = [row for row in roles if row["role"] == "IMPLEMENTATION"]
    resolved = (
        len(candidates) > 1
        and len(primaries) == 1
        and all(row["role"] != "UNKNOWN" for row in roles)
    )
    return {
        "status": "RESOLVED_COMPANIONS" if resolved else "UNRESOLVED_CANDIDATES",
        "selected_id": primaries[0]["id"] if resolved else None,
        "candidates": roles,
    }


def _candidate_pages(root, symbol, candidates, frozen):
    from pypdf import PdfReader

    documents = {}
    for candidate in candidates:
        if candidate["id"] in documents:
            raise ValueError("Duplicate candidate document")
        receipt = read(checked_path(root, candidate["receipt"], frozen[candidate["receipt"]]))
        expected = {
            "id": candidate["id"],
            "title": candidate["title"],
            "catalog_date": candidate["catalog_date"],
            "path": candidate["source"],
            "sha256": candidate["source_sha256"],
            "published_code": symbol[-6:],
            "status": "RECEIVED",
        }
        if any(receipt.get(k) != v for k, v in expected.items()) or symbol not in receipt.get(
            "requested_symbols", []
        ):
            raise ValueError("Candidate receipt identity differs")
        pdf = checked_path(root, candidate["source"], candidate["source_sha256"])
        documents[candidate["id"]] = (
            [page.extract_text() or "" for page in PdfReader(pdf).pages]
            if pdf.suffix.lower() == ".pdf"
            else []
        )
    return documents


def registered_inputs(paths, protocol_path):
    protocol_path = protocol_path.resolve()
    if not protocol_path.is_relative_to(paths.root / "configs/protocols"):
        raise ValueError("Distribution protocol must stay under configs/protocols")
    manifest = read(paths.manifest)["files"]
    if manifest.get(protocol_path.relative_to(paths.root).as_posix()) != sha(protocol_path):
        raise ValueError("Distribution protocol is not frozen")
    protocol = read(protocol_path)
    if protocol["name"] != "distribution_repair" or set(protocol["allowed_fact_changes"]) != FIELDS:
        raise ValueError("Unsupported distribution repair protocol")
    if protocol.get("candidate_resolution") not in (None, "price_adjustment_companions"):
        raise ValueError("Unsupported candidate resolution rule")
    inputs = {}
    for key, item in protocol["source_inputs"].items():
        inputs[key] = read(checked_path(paths.root, item["path"], item["sha256"]))
    return protocol, inputs


def _manual_reviews(inputs, actions):
    facts = {}
    for review in inputs["primary_reviews"]["events"]:
        key = (review["symbol"], review["ex_date"])
        if review["stored_action"] != actions[key]:
            raise ValueError("Manual review belongs to another action")
        facts[key] = review
    followup = inputs["prior_followup"]["cash_dividend"]
    key = (followup["symbol"], followup["ex_date"])
    facts[key] = {
        **followup,
        "source": {
            **followup["source"],
            "catalog_date": re.search(
                r"/finalpage/(\d{4}-\d{2}-\d{2})/", followup["source"]["url"]
            )[1],
        },
        "reviewed_facts": followup,
    }
    return facts


def review_distributions(paths, protocol):
    """Freeze the result for every registered action, including unsupported cases."""
    from importlib.metadata import version

    from pypdf import PdfReader

    if version("pypdf") != "6.9.2":
        raise ValueError("Install the registered disclosures extra (pypdf==6.9.2)")
    integrity = check_integrity(paths)
    extractor_hash = sha(Path(__file__))
    frozen = read(paths.manifest)["files"]
    protocol_path = paths.root / protocol
    protocol, inputs = registered_inputs(paths, protocol_path)
    target = (paths.root / protocol["review"]).resolve()
    if not target.is_relative_to(paths.root / "artifacts/checks") or target.exists():
        raise ValueError("Review output must be a new registered check file")
    bundle_path = checked_path(
        paths.root, protocol["source_bundle"], protocol["source_bundle_sha256"]
    )
    bundle = load_snapshot(paths, bundle_path, protocol["source_data_cutoff"])
    actions = {(a["symbol"], a["ex_date"]): a for a in bundle["actions"]}
    if len(actions) != len(bundle["actions"]):
        raise ValueError("Ambiguous action identities")
    for key in ("event_index", "structured_actions", "primary_reviews"):
        if inputs[key]["source_bundle_sha256"] != sha(bundle_path):
            raise ValueError("Review input refers to another bundle")
    index = {(r["symbol"], r["ex_date"]): r for r in inputs["event_index"]["records"]}
    structured = {(r["symbol"], r["ex_date"]): r for r in inputs["structured_actions"]["records"]}
    if set(index) != set(actions) or set(structured) != set(actions):
        raise ValueError("Source audit must cover the entire action inventory")
    manual = _manual_reviews(inputs, actions)
    records, facts = [], []
    for key, action in actions.items():
        if structured[key]["stored_action"] != action:
            raise ValueError("Structured review belongs to another action")
        base = {"symbol": key[0], "ex_date": key[1], "original_action_sha256": digest(action)}
        row = {**base, "verified_fields": {}}
        candidate = None
        if index[key]["status"] == "ONE_TEXT_CANDIDATE":
            candidate = index[key]["candidates"][0]
        elif (
            index[key]["status"] == "MULTIPLE_TEXT_CANDIDATES"
            and protocol.get("candidate_resolution") == "price_adjustment_companions"
        ):
            candidates = index[key]["candidates"]
            documents = _candidate_pages(paths.root, key[0], candidates, frozen)
            resolution = resolve_companion_notices(key[0], candidates, documents)
            row["candidate_resolution"] = resolution
            candidate = next((c for c in candidates if c["id"] == resolution["selected_id"]), None)
        if key in manual:
            review = manual[key]
            if review["review_status"] != "FULL_TEXT_AND_RELEVANT_PAGE_IMAGES_REVIEWED":
                raise ValueError("Incomplete manual review")
            values = review["reviewed_facts"]
            if values["record_date"] != action["record_date"] or abs(
                Decimal(str(values["share_multiplier"])) - Decimal(str(action["share_multiplier"]))
            ) > Decimal("1e-12"):
                raise ValueError("Manual review changes an unregistered entitlement")
            source = review["source"]
            checked_path(paths.root, source["path"], source["sha256"])
            known = dt.date.fromisoformat(source["catalog_date"]) + dt.timedelta(days=1)
            if str(known) > action["ex_date"]:
                raise ValueError("Manual filing is unavailable at the event opening")
            fields = {k: v for k, v in values.items() if k in FIELDS and v is not None}
            row.update(
                status="VERIFIED_FIELDS",
                method="MANUAL_PRIMARY_REVIEW",
                verified_fields=fields,
                source=source,
                evidence_pages=review["evidence_pages"],
                known_at=known.isoformat() + "T00:00:00+08:00",
            )
        elif candidate is None:
            row["status"] = index[key]["status"]
        elif structured[key]["status"] not in (
            "STRUCTURED_DATE_CANDIDATE",
            "STRUCTURED_EVENT_MATCH",
        ):
            row["status"] = structured[key]["status"]
        else:
            known = source_available(candidate, action)
            row["status"] = "UNSUPPORTED_TITLE_OR_LATE_SOURCE"
            if known:
                provider = structured[key]["candidate"]
                checked_path(paths.root, provider["source"], provider["source_sha256"])
                pdf = checked_path(paths.root, candidate["source"], candidate["source_sha256"])
                receipt_path = paths.root / candidate["receipt"]
                receipt = read(
                    checked_path(paths.root, candidate["receipt"], frozen[candidate["receipt"]])
                )
                if action["symbol"] not in receipt["requested_symbols"] or receipt["sha256"] != sha(
                    pdf
                ):
                    raise ValueError("Primary filing issuer binding differs")
                evidence = extract_terms(
                    [page.extract_text() or "" for page in PdfReader(pdf).pages]
                )
                fields, status = verified_terms(action, provider, evidence)
                row.update(
                    status=status,
                    verified_fields=fields,
                    method="LABELLED_PRIMARY_AND_STRUCTURED",
                    source={
                        "path": candidate["source"],
                        "sha256": candidate["source_sha256"],
                        "url": receipt["url"],
                        "catalog_date": candidate["catalog_date"],
                        "receipt": candidate["receipt"],
                        "receipt_sha256": sha(receipt_path),
                    },
                    known_at=known,
                    evidence=evidence,
                    evidence_pages=sorted(
                        {
                            page
                            for items in evidence.values()
                            for item in items
                            for page in item.get("pages", [item["page"]])
                        }
                    ),
                    structured_source=provider,
                )
        if row["verified_fields"]:
            facts.append(row)
        records.append(row)
        if len(records) % 100 == 0:
            log("Distribution sources reviewed", events=len(records), total=len(actions))
    review = {
        "status": "REVIEWED_FIELDS_WITH_UNRESOLVED_EVENTS",
        "completed_at": now(),
        "protocol_sha256": sha(protocol_path),
        "data_snapshot_sha256": sha(bundle_path),
        "extractor_sha256": extractor_hash,
        "pypdf_version": version("pypdf"),
        "source_integrity": integrity,
        "source_inputs": protocol["source_inputs"],
        "original_actions_sha256": digest(bundle["actions"]),
        "counts": dict(Counter(r["status"] for r in records)),
        "events": len(records),
        "facts": facts,
        "records": records,
        "facts_applied": False,
        "scope": "Field-scoped source review, not certification of every corporate-action term. Unsupported layouts and conflicting facts remain unresolved. No returns or forecasts used.",
    }
    if sha(Path(__file__)) != extractor_hash:
        raise ValueError("Extractor changed during source review")
    save(target, review, immutable=True)
    manifest = read(paths.manifest)
    manifest["files"][target.relative_to(paths.root).as_posix()] = sha(target)
    save(paths.manifest, manifest)
    return {k: review[k] for k in ("status", "counts", "events", "facts_applied")}
