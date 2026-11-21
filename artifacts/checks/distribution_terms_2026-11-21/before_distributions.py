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

DATE = r"20\d{2}\s*(?:年|/|-)\s*\d{1,2}\s*(?:月|/|-)\s*\d{1,2}\s*日?"
EX_LABEL = r"除权\s*(?:\(\s*息\s*\)|除息|息)?\s*日"
NUMBER = r"[0-9]+(?:\.[0-9]+)?"
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
    parts = re.findall(r"\d+", text)
    return dt.date(*map(int, parts)).isoformat()


def extract_terms(pages):
    """Recognize only labelled dates and explicitly gross cash in supported layouts."""
    evidence = {}

    def add(field, value, page, excerpt):
        evidence.setdefault(field, []).append({"value": value, "page": page, "text": excerpt})

    history = ""
    for page, raw in enumerate(pages, 1):
        text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw))
        text = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", text)
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
            (r"A\s*股每股现金红利\s*(?:人民币\s*)?(" + NUMBER + r")\s*元\s*\(\s*含税", 1),
            (
                r"向全体股东(?:按)?每\s*10\s*股派(?:发)?\s*(?:现金(?:红利|股利)?\s*)?(?:人民币\s*)?("
                + NUMBER
                + r")\s*元(?:人民币)?\s*(?:现金)?\s*\(\s*含税",
                10,
            ),
            (
                r"本次利润分配[^。]{0,180}?每股派发现金红利\s*(?:人民币\s*)?("
                + NUMBER
                + r")\s*元\s*\(\s*含税",
                1,
            ),
        )
        for pattern, divisor in amounts:
            for match in re.finditer(pattern, text):
                if divisor == 10:
                    headings = list(
                        re.finditer(
                            r"[一二三四五六七八九十]+、([^。、；;:：]{1,60})",
                            history + text[: match.start()],
                        )
                    )
                    title = headings[-1][1].strip() if headings else ""
                    current = re.match(
                        r"(?:本次(?:实施的)?)?(?:权益分派|利润分配|分配|分红派息)方案", title
                    )
                    if not current:
                        add(
                            "unscoped_cash_per_share",
                            str(Decimal(match[1]) / divisor),
                            page,
                            match[0],
                        )
                        continue
                add("cash_per_share", str(Decimal(match[1]) / divisor), page, match[0])
        history += text + " "
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
        elif index[key]["status"] != "ONE_TEXT_CANDIDATE":
            row["status"] = index[key]["status"]
        elif structured[key]["status"] not in (
            "STRUCTURED_DATE_CANDIDATE",
            "STRUCTURED_EVENT_MATCH",
        ):
            row["status"] = structured[key]["status"]
        else:
            candidate = index[key]["candidates"][0]
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
                        {x["page"] for items in evidence.values() for x in items}
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
