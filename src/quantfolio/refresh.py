"""Turn reviewed raw collections into an immutable, historically stable snapshot."""

import datetime as dt
from copy import deepcopy
from pathlib import Path

import joblib
import pandas as pd

from .daily_sources import baostock_bars, compare_target, quote_entries, tencent_beijing_bar
from .dataset import check_integrity, load_snapshot
from .market_data import append_bars, inspect_bars, parse_bars
from .preprocessing import build_dataset, compare_history
from .snapshots import label_changes, next_opening, repair_actions
from .storage import array_sha, digest, dump_gzip, load_gzip, now, read, save, sha


def _checked_path(paths, relative, expected):
    path = (paths.root / relative).resolve()
    if not path.is_relative_to(paths.root) or sha(path) != expected:
        raise ValueError(f"Registered source changed or escaped the project: {relative}")
    return path


def reviewed_actions(actions, calendar, reviews, meta):
    """Apply dated evidence and add announced events without inventing payment availability."""
    updated = deepcopy(actions)
    for review in reviews:
        if review["mode"] == "amend":
            updated, _ = repair_actions(updated, calendar, review["facts"], "cash_pay_date")
            continue
        if review["mode"] != "append":
            raise ValueError("Unknown corporate-action review mode")
        for fact in review["facts"]:
            symbol = fact["symbol"]
            if symbol not in meta or any(
                (a["symbol"], a["ex_date"]) == (symbol, fact["ex_date"]) for a in updated
            ):
                raise ValueError(f"Unknown or duplicate reviewed event: {symbol}")
            if not (
                fact["notice_date"] <= str(calendar[-1].date())
                and fact["notice_date"]
                <= fact["record_date"]
                <= fact["ex_date"]
                <= fact["cash_pay_date"]
                and fact["cash_per_share"] >= 0
                and fact["share_multiplier"] > 0
            ):
                raise ValueError(f"Invalid dates or entitlements in reviewed event: {symbol}")
            action = {
                key: value
                for key, value in fact.items()
                if key
                not in {
                    "source",
                    "simulation_cash_available_date",
                    "evidence_pages",
                    "review_status",
                    "availability_status",
                }
            }
            source = fact["source"]
            action.update(
                id=f"{symbol}:{fact['ex_date']}",
                code=meta[symbol]["code"],
                name=meta[symbol]["name"],
                source=source["source"],
                verified_source=source["source"],
                date_status="PRIMARY_FILING_REVIEWED",
                source_evidence={
                    "path": source["local_pdf"],
                    "sha256": source["sha256"],
                    "pages": fact["evidence_pages"],
                },
            )
            updated.append(action)
    for action in updated:
        action["cash_available_date"] = next_opening(calendar, action.get("cash_pay_date"))
    return updated


def _collection_metadata(paths, protocol, parent, collection_parent):
    """Permit only explicitly reviewed status additions to older collection metadata."""
    if set(parent["raws"]) != set(collection_parent["raws"]):
        raise ValueError("Collection raw universe differs from the processed parent")
    if parent["meta"] != collection_parent["meta"]:
        item = protocol.get("collection_metadata_review")
        if item is None:
            raise ValueError("Collection universe differs from the processed parent")
        review = read(_checked_path(paths, item["path"], item["sha256"]))
        if (
            review["status"] != "DATED_STATUS_ADDITIONS_VERIFIED"
            or review["before_meta_sha256"] != digest(collection_parent["meta"])
            or review["after_meta_sha256"] != digest(parent["meta"])
            or review["before_bundle_sha256"] != protocol["collection_source_sha256"]
            or review["after_bundle_sha256"] != protocol["source_bundle_sha256"]
        ):
            raise ValueError("Collection metadata review belongs to another input")
        _checked_path(paths, review["source_review"], review["source_review_sha256"])
        stripped = deepcopy(parent["meta"])
        for symbol, periods in review["added_risk_warning_periods"].items():
            if (
                symbol not in stripped
                or stripped[symbol].pop("risk_warning_periods", None) != periods
            ):
                raise ValueError("Collection metadata status additions differ from review")
        if stripped != collection_parent["meta"]:
            raise ValueError("Unreviewed collection metadata difference")
    for symbol, raw in parent["raws"].items():
        pd.testing.assert_frame_equal(raw, collection_parent["raws"][symbol], check_exact=True)


def _daily_collection(paths, item, date, parent_sha, provider):
    registration = read(
        _checked_path(paths, f"{item['path']}/protocol.json", item["protocol_sha256"])
    )
    collection = read(
        _checked_path(paths, f"{item['path']}/collection.json", item["collection_sha256"])
    )
    if (
        registration["target_date"] != date
        or registration["source_bundle_sha256"] != parent_sha
        or registration["provider"] != provider
        or collection["status"] != "COMPLETE"
        or not collection["records"]
    ):
        raise ValueError("Daily fallback collection identity or status differs")
    if "collector_sha256" in registration:
        _checked_path(
            paths, f"{item['path']}/collector_source.py", registration["collector_sha256"]
        )
    return registration, collection


def _closing_quotes(paths, item, protocol):
    registration, collection = _daily_collection(
        paths,
        item,
        protocol["completed_date"],
        protocol["collection_source_sha256"],
        "tencent_closing_quote",
    )
    quotes, sources = {}, {}
    for record in collection["records"]:
        acquired = dt.datetime.fromisoformat(record["requested_at"])
        if acquired.tzinfo is None:
            raise ValueError("Closing-quote acquisition timestamp needs a timezone")
        acquired = acquired.astimezone(dt.timezone(dt.timedelta(hours=8)))
        if (
            record["status"] != "RECEIVED"
            or acquired.date().isoformat() < protocol["completed_date"]
            or (acquired.date().isoformat() == protocol["completed_date"] and acquired.hour < 16)
        ):
            raise ValueError("Closing quotes were incomplete or collected before market close")
        parsed = quote_entries(_checked_path(paths, record["file"], record["sha256"]).read_bytes())
        if (
            list(parsed) != record["symbols"]
            or list(parsed) != record["returned_symbols"]
            or set(parsed) & set(quotes)
        ):
            raise ValueError("Closing-quote batch membership differs")
        quotes.update(parsed)
        sources.update({s: record["sha256"] for s in parsed})
    if set(quotes) != set(registration["symbols"]):
        raise ValueError("Closing-quote collection does not cover its registered universe")
    return quotes, sources


def _fallback_raws(paths, protocol, parent, received):
    file = _checked_path(
        paths, protocol["fallback_source_bundle"], protocol["fallback_source_sha256"]
    )
    collection_parent = load_snapshot(paths, file, protocol["source_data_cutoff"])
    if (
        collection_parent["meta"] != parent["meta"]
        or set(collection_parent["raws"]) != set(parent["raws"])
        or not collection_parent["calendar"].equals(parent["calendar"])
    ):
        raise ValueError("Fallback collection metadata differs from the processed parent")
    for symbol, frame in parent["raws"].items():
        pd.testing.assert_frame_equal(frame, collection_parent["raws"][symbol], check_exact=True)
    date = protocol["completed_date"]
    records, used_providers = [], set()
    for item in protocol["fallback_collections"]:
        provider = item["provider"]
        expected = {"baostock_daily": "baostock", "tencent_beijing": "tencent_daily_bars"}
        if provider not in expected or provider in used_providers:
            raise ValueError("Unknown or duplicate fallback provider")
        used_providers.add(provider)
        registration, collection = _daily_collection(
            paths, item, date, protocol["fallback_source_sha256"], expected[provider]
        )
        selected = set(item["use_symbols"])
        if (
            len(selected) != len(item["use_symbols"])
            or not selected <= set(parent["meta"])
            or selected & set(received)
        ):
            raise ValueError("Fallback selection duplicates or replaces an existing symbol")
        converted, hashes = {}, {}
        if provider == "baostock_daily":
            kinds = set()
            for record in collection["records"]:
                if record["kind"] in kinds or record["error_code"] != "0":
                    raise ValueError("Duplicate or unsuccessful BaoStock daily batch")
                kinds.add(record["kind"])
                raw = load_gzip(_checked_path(paths, record["response"], record["response_sha256"]))
                if (
                    len(raw["rows"]) != record["rows"]
                    or raw["fields"] != record["fields"]
                    or raw["kind"] != record["kind"]
                ):
                    raise ValueError("BaoStock batch metadata differs from its response")
                parsed = baostock_bars(raw, parent["meta"], date)
                if set(parsed) & set(converted):
                    raise ValueError("Duplicate BaoStock symbol across daily batches")
                converted.update(parsed)
                hashes.update({s: {"response_sha256": record["response_sha256"]} for s in parsed})
            expected_symbols = {s for s, m in parent["meta"].items() if s.startswith(("sh", "sz"))}
            if kinds != {"stocks", "funds"} or set(converted) != expected_symbols:
                raise ValueError("BaoStock feed does not cover the Shanghai/Shenzhen universe")
        else:
            quotes, quote_hashes = _closing_quotes(paths, item["closing_quotes"], protocol)
            for record in collection["records"]:
                symbol = record["symbol"]
                if (
                    record["status"] != "RECEIVED"
                    or symbol not in parent["meta"]
                    or symbol in converted
                ):
                    raise ValueError("Unknown, duplicate or unsuccessful Tencent daily symbol")
                raw = _checked_path(
                    paths, f"{item['path']}/{symbol}.txt", record["response_sha256"]
                )
                converted[symbol] = tencent_beijing_bar(
                    raw.read_bytes(),
                    quotes[symbol],
                    parent["meta"][symbol],
                    registration["start"],
                    date,
                    parent["raws"][symbol],
                )
                hashes[symbol] = {
                    "response_sha256": record["response_sha256"],
                    "closing_quote_sha256": quote_hashes[symbol],
                }
            expected_symbols = {s for s in parent["meta"] if s.startswith("bj")}
            if (
                set(converted) != expected_symbols
                or set(registration["symbols"]) != expected_symbols
            ):
                raise ValueError("Tencent daily feed does not cover the Beijing universe")
        if selected != set(converted) - set(received):
            raise ValueError("Fallback use differs from the fixed missing-symbol selection")
        for symbol, (frame, details) in converted.items():
            if symbol in received:
                differences = compare_target(
                    received[symbol].loc[pd.Timestamp(date)], details, provider
                )
                if differences:
                    raise ValueError(f"Conflicting fallback target fields: {symbol}/{differences}")
            else:
                received[symbol] = append_bars(parent["raws"][symbol], frame, date)
            records.append(
                {
                    "symbol": symbol,
                    "provider": provider,
                    "used": symbol in selected,
                    **hashes[symbol],
                    **details,
                }
            )
    return records


def _reviewed_raws(paths, protocol, parent, collection_parent):
    _collection_metadata(paths, protocol, parent, collection_parent)
    received, records = {}, []
    for source in protocol["collections"]:
        registration = read(
            _checked_path(paths, f"{source['path']}/protocol.json", source["protocol_sha256"])
        )
        collection = read(
            _checked_path(paths, f"{source['path']}/collection.json", source["collection_sha256"])
        )
        if (
            registration["source_bundle_sha256"] != protocol["collection_source_sha256"]
            or registration["requested_completed_date"] != protocol["completed_date"]
            or collection["requested_completed_date"] != protocol["completed_date"]
            or registration["adjustment"] != "unadjusted"
            or collection["status"] not in {"COMPLETE", "PARTIAL"}
        ):
            raise ValueError("Raw collection identity, adjustment or terminal status differs")
        for record in collection["records"]:
            if record["status"] == "FAILED":
                continue
            symbol = record["symbol"]
            if symbol not in parent["meta"] or symbol in received:
                raise ValueError("Unknown or duplicate completed response")
            response = _checked_path(
                paths, f"{source['path']}/{symbol}.json", record["response_sha256"]
            ).read_bytes()
            arguments = (
                response,
                parent["meta"][symbol],
                registration["historical_overlap_start"],
                protocol["completed_date"],
            )
            audit = inspect_bars(*arguments, collection_parent["raws"][symbol])
            if any(audit[key] != record[key] for key in audit):
                raise ValueError(f"Recomputed collection audit differs: {symbol}")
            if not audit["target_present"] or any(
                change["field"] not in protocol["approved_overlap_fields"]
                for change in audit["historical_revisions"]
            ):
                raise ValueError(f"Unreviewed price revision or missing target: {symbol}")
            frame = parse_bars(*arguments)
            # A suspended security may end before the parent cutoff; do not backfill its tail.
            frame = frame.loc[frame.index > parent["calendar"][-1]]
            received[symbol] = append_bars(
                parent["raws"][symbol], frame, protocol["completed_date"]
            )
            records.append(
                {
                    "symbol": symbol,
                    "response_sha256": record["response_sha256"],
                    "retained_overlap_revisions": len(audit["historical_revisions"]),
                    "added_bars": len(received[symbol]) - len(parent["raws"][symbol]),
                }
            )
    if protocol.get("fallback_collections"):
        assigned = {r["symbol"]: "eastmoney" for r in records}
        fallback_records = _fallback_raws(paths, protocol, parent, received)
        names = {"baostock_daily": "baostock", "tencent_beijing": "tencent"}
        assigned.update({r["symbol"]: names[r["provider"]] for r in fallback_records if r["used"]})
        item = protocol["source_assignment"]
        assignment = read(_checked_path(paths, item["path"], item["sha256"]))
        planned = {r["symbol"]: r["provider"] for r in assignment["assignments"]}
        if (
            assignment["parent_bundle_sha256"] != protocol["source_bundle_sha256"]
            or len(planned) != len(assignment["assignments"])
            or planned != assigned
        ):
            raise ValueError("Actual source assignment differs from registration")
        records = [{**r, "provider": "eastmoney", "used": True} for r in records] + fallback_records
    if set(received) != set(parent["meta"]):
        raise ValueError("Raw collections do not cover the complete registered universe")
    # Source insertion order is part of the frozen panel contract.
    return {symbol: received[symbol] for symbol in parent["raws"]}, records


def refresh_snapshot(paths, output, protocol_path):
    """Build a new registered snapshot; activation and model fitting remain separate steps."""
    target = (paths.root / output).resolve()
    directory = (paths.root / "data/processed").resolve()
    if (
        not directory.is_relative_to(paths.root)
        or not target.is_relative_to(directory)
        or target == directory
    ):
        raise ValueError("Snapshot must be a named directory under data/processed")
    if target.exists():
        raise FileExistsError(f"Snapshot already exists: {target}")
    protocol_path = (paths.root / protocol_path).resolve()
    if not protocol_path.is_relative_to((paths.root / "configs/protocols").resolve()):
        raise ValueError("Refresh protocol must stay under configs/protocols")
    integrity = check_integrity(paths)
    protocol = read(protocol_path)
    parent_path = _checked_path(paths, protocol["source_bundle"], protocol["source_bundle_sha256"])
    source = load_snapshot(paths, parent_path, protocol["source_data_cutoff"])
    collection_path = _checked_path(
        paths, protocol["collection_source_bundle"], protocol["collection_source_sha256"]
    )
    collection_source = load_snapshot(paths, collection_path, protocol["source_data_cutoff"])
    if protocol["completed_date"] <= protocol["source_data_cutoff"]:
        raise ValueError("Refresh must extend the processed cutoff")
    for name, checksum in protocol["review_evidence_sha256"].items():
        _checked_path(paths, name, checksum)
    reviews = []
    for item in protocol["action_reviews"]:
        review = read(_checked_path(paths, item["path"], item["sha256"]))
        if item["mode"] == "amend" and review["data_snapshot_sha256"] != sha(parent_path):
            raise ValueError("Amended action review belongs to a different parent")
        for fact in review["facts"]:
            _checked_path(paths, fact["source"]["local_pdf"], fact["source"]["sha256"])
        reviews.append({"mode": item["mode"], "facts": review["facts"]})
    raws, collection_audit = _reviewed_raws(paths, protocol, source, collection_source)
    actions = reviewed_actions(source["actions"], raws["sh510300"].index, reviews, source["meta"])
    provenance = {
        "registered_at": now(),
        "protocol_sha256": sha(protocol_path),
        "source_bundle_sha256": sha(parent_path),
        "source_sha256": {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
    }
    save(target / "provenance.json", provenance, immutable=True)
    result = build_dataset(
        source["meta"],
        raws,
        actions,
        quarantined=protocol["quarantined"],
        opening_status=source.get("opening_status"),
    )
    prefix = compare_history(source, result, labels=False)
    previous_rows = result["table"].loc[result["table"].date_index < len(source["calendar"])]
    changes, details = label_changes(source["table"], previous_rows)
    dump_gzip(target / "label_changes.json.gz", details)
    result["provenance"] = {
        "schema_version": 1,
        "purpose": "reviewed_market_extension",
        "protocol_sha256": sha(protocol_path),
        "source_bundle_sha256": sha(parent_path),
        "created_at": now(),
        "quarantined": protocol["quarantined"],
        "historical_status_point_in_time_certified": False,
        "industry_point_in_time_certified": False,
    }
    joblib.dump(result, target / "bundle.joblib", compress=3)
    audit = {
        "status": "PASS",
        "scope": "RAW_BUILD_AND_HISTORICAL_PARITY",
        "completed_at": now(),
        "activated": False,
        "data_cutoff": result["cutoff"],
        "source_integrity": integrity,
        "bundle_sha256": sha(target / "bundle.joblib"),
        "historical_prefix": prefix,
        "rows": len(result["table"]),
        "features": len(result["features"]),
        "eligible_rows": int(result["table"].eligible.sum()),
        "feature_values_sha256": array_sha(result["table"][result["features"]].to_numpy()),
        "raw_collection": collection_audit,
        "actions": len(actions),
        "known_payment_dates": sum(bool(a.get("cash_pay_date")) for a in actions),
        "historical_label_changes": changes,
        "label_changes_sha256": sha(target / "label_changes.json.gz"),
        "quarantined": protocol["quarantined"],
        "limitations": protocol["limitations"],
    }
    save(target / "audit.json", audit, immutable=True)
    manifest = read(paths.manifest)
    for path in sorted(target.iterdir()):
        manifest["files"][path.relative_to(paths.root).as_posix()] = sha(path)
    save(paths.manifest, manifest)
    return {key: value for key, value in audit.items() if key != "raw_collection"}
