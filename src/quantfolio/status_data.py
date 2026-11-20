"""Collect retrospective daily security flags for independent source review."""

from __future__ import annotations

import contextlib
import importlib.metadata
import io
import socket
import time

import numpy as np
import pandas as pd

from .dataset import load_bundle
from .storage import dump_gzip, load_gzip, log, now, read, save, sha

FIELDS = ("date", "code", "open", "high", "low", "close", "volume", "amount", "tradestatus", "isST")
PROVIDER_VERSION = "0.9.4"


def load_status_review(paths):
    """Read the frozen eligibility finding for this exact active snapshot, if configured."""
    configured = paths.config.get("historical_status_review")
    if configured is None:
        return None
    file = (paths.root / configured).resolve()
    parent = (paths.root / "artifacts/checks").resolve()
    if not parent.is_relative_to(paths.root) or not file.is_relative_to(parent):
        raise ValueError("Historical status review must stay inside artifacts/checks")
    relative = file.relative_to(paths.root).as_posix()
    if sha(file) != read(paths.manifest)["files"].get(relative):
        raise ValueError("Historical status review is not frozen or its hash changed")
    review = read(file)
    if review["source_bundle_sha256"] != sha(paths.bundle):
        raise ValueError("Historical status review belongs to another snapshot")
    decisions = {
        "KNOWN_HISTORICAL_ST_ELIGIBILITY_DEFECT": True,
        "REGISTERED_STATUS_REPAIR_VERIFIED": False,
    }
    if (
        type(review["blocking"]) is not bool
        or review["status"] not in decisions
        or decisions[review["status"]] != review["blocking"]
    ):
        raise ValueError("Historical status review has an invalid decision")
    return {**review, "path": relative, "sha256": sha(file)}


def parse_history(payload, symbol, calendar):
    """Reject ambiguous flags and malformed prices; never invent publication times."""
    if (
        payload.get("error_code") != "0"
        or payload.get("adjustment") != "3"
        or payload.get("fields") != list(FIELDS)
    ):
        raise ValueError("Status response must contain successful unadjusted daily data")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or any(len(row) != len(FIELDS) for row in rows):
        raise ValueError("Empty or malformed status history")
    frame = pd.DataFrame(rows, columns=FIELDS)
    expected = symbol[:2] + "." + symbol[2:]
    if not frame.code.eq(expected).all():
        raise ValueError("Status response belongs to another security")
    if not frame.tradestatus.isin(["0", "1"]).all() or not frame.isST.isin(["0", "1"]).all():
        raise ValueError("Missing or unrecognized daily status flag")
    frame["date"] = pd.to_datetime(frame.date, format="%Y-%m-%d", errors="raise")
    frame = frame.set_index("date")
    if (
        frame.index.has_duplicates
        or not frame.index.is_monotonic_increasing
        or not frame.index.isin(calendar).all()
    ):
        raise ValueError("Status history contains duplicate, unordered or unknown sessions")
    prices = ["open", "high", "low", "close"]
    # The observed provider response uses empty quantity fields on suspended days.
    # Preserve those missing values; they are not evidence of a zero-volume auction.
    for column in ("volume", "amount"):
        if (frame[column].eq("") & frame.tradestatus.ne("0")).any():
            raise ValueError("Missing traded quantities on an active session")
        frame[column] = frame[column].replace("", np.nan)
    for column in [*prices, "volume", "amount"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    quantities = frame[["volume", "amount"]].to_numpy(dtype=float)
    if (
        not np.isfinite(frame[prices].to_numpy(dtype=float)).all()
        or np.isinf(quantities).any()
        or (frame[["volume", "amount"]].isna().any(axis=1) & frame.tradestatus.ne("0")).any()
        or (frame[prices] <= 0).any().any()
        or (frame[["volume", "amount"]] < 0).any().any()
        or (frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
        or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise ValueError("Invalid daily prices or traded quantities in status history")
    return frame


def fetch_status_history(paths, output, retry=None):
    """Archive all selected Shanghai/Shenzhen stocks; stop on the first provider error."""
    target = (paths.root / output).resolve()
    parent = (paths.root / "data/raw").resolve()
    if (
        not parent.is_relative_to(paths.root)
        or target == parent
        or not target.is_relative_to(parent)
    ):
        raise ValueError("Status collection must be a named directory under data/raw")
    if target.exists():
        raise FileExistsError(f"Status collection already exists: {target}")
    try:
        import baostock as provider
    except ImportError as exc:
        raise ValueError("Install the optional status-data dependencies before collection") from exc
    if importlib.metadata.version("baostock") != PROVIDER_VERSION:
        raise ValueError(f"Status collection requires baostock {PROVIDER_VERSION}")
    bundle = load_bundle(paths)
    calendar = bundle["calendar"]
    symbols = sorted(
        s for s, m in bundle["meta"].items() if m["kind"] == "stock" and s.startswith(("sh", "sz"))
    )
    retry_source = None
    if retry is not None:
        previous = (paths.root / retry).resolve()
        if previous == parent or not previous.is_relative_to(parent):
            raise ValueError("Status retry source must stay inside data/raw")
        registration = read(previous / "protocol.json")
        completed = read(previous / "collection.json")
        if registration["source_bundle_sha256"] != sha(paths.bundle):
            raise ValueError("Status retry belongs to another dataset")
        requested = registration["symbols"]
        if len(set(requested)) != len(requested) or not set(requested) <= set(symbols):
            raise ValueError("Status retry has an invalid requested universe")
        accepted, seen = set(), set()
        for record in completed["records"]:
            symbol = record["symbol"]
            if symbol not in requested or symbol in seen:
                raise ValueError("Unexpected or duplicate status retry record")
            seen.add(symbol)
            if record["status"] == "PARSED":
                response = previous / f"{symbol}.json.gz"
                if sha(response) != record["response_sha256"]:
                    raise ValueError("Status retry response hash mismatch")
                parse_history(load_gzip(response), symbol, calendar)
                accepted.add(symbol)
        symbols = sorted(set(requested) - accepted)
        if not symbols:
            raise ValueError("No missing status histories to retry")
        retry_source = {
            "path": previous.relative_to(paths.root).as_posix(),
            "protocol_sha256": sha(previous / "protocol.json"),
            "collection_sha256": sha(previous / "collection.json"),
        }
    start, end = str(calendar[0].date()), str(calendar[-1].date())
    save(
        target / "protocol.json",
        {
            "registered_at": now(),
            "source_bundle_sha256": sha(paths.bundle),
            "collector_sha256": sha(__file__),
            "provider": "baostock",
            "provider_version": PROVIDER_VERSION,
            "provider_reference": "https://pypi.org/project/baostock/0.9.4/",
            "start": start,
            "end": end,
            "symbols": symbols,
            "excluded": sorted(set(bundle["meta"]) - set(symbols)),
            "selection": "All Shanghai/Shenzhen stocks in the frozen selected universe",
            "fields": list(FIELDS),
            "adjustment": "3",
            "volume_unit": "shares",
            "amount_unit": "CNY",
            "socket_timeout_seconds": 30,
            "request_spacing_seconds": 0.5,
            "stop_on_first_provider_error": True,
            "retries": 0,
            "retry_source": retry_source,
            "purpose": "Retrospective screening; daily flags do not establish pre-open availability",
            "activated": False,
        },
        immutable=True,
    )
    previous_timeout = socket.getdefaulttimeout()
    records, messages = [], io.StringIO()
    failure = None
    logged_in = False
    socket.setdefaulttimeout(30)
    try:
        with contextlib.redirect_stdout(messages):
            login = provider.login()
        if login.error_code != "0":
            raise ValueError(f"Status provider login failed: {login.error_code} {login.error_msg}")
        logged_in = True
        for symbol in symbols:
            record = {"symbol": symbol, "requested_at": now()}
            with contextlib.redirect_stdout(messages):
                response = provider.query_history_k_data_plus(
                    symbol[:2] + "." + symbol[2:],
                    ",".join(FIELDS),
                    start_date=start,
                    end_date=end,
                    frequency="d",
                    adjustflag="3",
                )
                rows = []
                while response.error_code == "0" and response.next():
                    rows.append(response.get_row_data())
            payload = {
                "symbol": symbol,
                "fields": response.fields,
                "rows": rows,
                "error_code": response.error_code,
                "error_msg": response.error_msg,
                "adjustment": "3",
                "retrieved_at": now(),
            }
            destination = target / f"{symbol}.json.gz"
            dump_gzip(destination, payload)
            record.update(response_sha256=sha(destination), rows=len(rows))
            try:
                frame = parse_history(payload, symbol, calendar)
                record.update(
                    status="PARSED",
                    st_sessions=int(frame.isST.eq("1").sum()),
                    suspended_sessions=int(frame.tradestatus.eq("0").sum()),
                )
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status="REJECTED", error=f"{type(exc).__name__}: {exc}")
            records.append(record)
            if response.error_code != "0":
                failure = f"Provider error: {response.error_code} {response.error_msg}"
                break
            if len(records) % 25 == 0:
                log(
                    "Daily status histories collected",
                    requested=len(records),
                    expected=len(symbols),
                )
            time.sleep(0.5)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        if logged_in:
            try:
                with contextlib.redirect_stdout(messages):
                    provider.logout()
            except (OSError, ValueError, TypeError, KeyError) as exc:
                failure = failure or f"Logout: {type(exc).__name__}: {exc}"
        socket.setdefaulttimeout(previous_timeout)
        result = {
            "status": "COMPLETE" if len(records) == len(symbols) and failure is None else "PARTIAL",
            "completed_at": now(),
            "expected": len(symbols),
            "requested": len(records),
            "parsed": sum(r["status"] == "PARSED" for r in records),
            "failure": failure,
            "provider_messages": messages.getvalue(),
            "activated": False,
            "records": records,
        }
        save(target / "collection.json", result, immutable=True)
    return {k: v for k, v in result.items() if k not in {"records", "provider_messages"}}


def status_periods(dates, calendar):
    """Group observed dates only; a missing session breaks an interval."""
    positions = calendar.get_indexer(dates)
    if (positions < 0).any() or (np.diff(positions) <= 0).any():
        raise ValueError("Status dates must be ordered, unique and inside the calendar")
    periods = []
    for group in np.split(positions, np.flatnonzero(np.diff(positions) != 1) + 1):
        if len(group):
            periods.append(
                {
                    "first": str(calendar[group[0]].date()),
                    "last": str(calendar[group[-1]].date()),
                    "sessions": len(group),
                }
            )
    return periods


def inspect_history(frame, raw, eligible, calendar):
    """Count source disagreements and historical eligibility without changing either input."""
    overlap = raw.index.intersection(frame.index)
    mismatches = []
    for field in ("open", "high", "low", "close"):
        old = raw.loc[overlap, field].to_numpy(dtype=float)
        new = frame.loc[overlap, field].to_numpy(dtype=float)
        for index in np.flatnonzero(~np.isclose(old, new, atol=1e-8, rtol=0)):
            mismatches.append(
                {
                    "date": str(overlap[index].date()),
                    "field": field,
                    "frozen": float(old[index]) if np.isfinite(old[index]) else None,
                    "provider": float(new[index]),
                }
            )
    internal = calendar[(calendar >= raw.index.min()) & (calendar <= raw.index.max())]
    gaps = internal.difference(raw.index)
    suspended = frame.index[frame.tradestatus.eq("0")]
    st = frame.index[frame.isST.eq("1")]
    st_eligible = st.intersection(eligible.index[eligible])
    return {
        "provider_rows": len(frame),
        "overlap_sessions": len(overlap),
        "ohlc_comparisons": len(overlap) * 4,
        "ohlc_mismatches": mismatches,
        "raw_sessions_without_status": len(raw.index.difference(frame.index)),
        "internal_gaps": len(gaps),
        "gaps_flagged_suspended": len(gaps.intersection(suspended)),
        "gaps_flagged_active": len(gaps.intersection(frame.index[frame.tradestatus.eq("1")])),
        "gaps_without_status": len(gaps.difference(frame.index)),
        "raw_sessions_flagged_suspended": len(raw.index.intersection(suspended)),
        "st_sessions": len(st),
        "st_periods": status_periods(st, calendar),
        "st_eligible_sessions": len(st_eligible),
        "st_eligible_dates": [str(d.date()) for d in st_eligible],
        "suspended_periods": status_periods(suspended, calendar),
    }


def audit_status_history(paths, collections, output):
    """Join finished provider batches with frozen inputs; no flag becomes an opening status."""
    target = paths.output_path(output, "checks")
    bundle = load_bundle(paths)
    frames, records, sources = {}, [], []
    parent = (paths.root / "data/raw").resolve()
    for location in collections:
        source = (paths.root / location).resolve()
        if (
            source == parent
            or not source.is_relative_to(parent)
            or not parent.is_relative_to(paths.root)
        ):
            raise ValueError("Status audit source must stay inside data/raw")
        protocol, collection = read(source / "protocol.json"), read(source / "collection.json")
        if protocol["source_bundle_sha256"] != sha(paths.bundle) or collection["status"] not in {
            "COMPLETE",
            "PARTIAL",
        }:
            raise ValueError("Status collection is unfinished or belongs to another dataset")
        symbols = protocol["symbols"]
        if (
            len(set(symbols)) != len(symbols)
            or collection["expected"] != len(symbols)
            or collection["requested"] > len(symbols)
            or (collection["status"] == "COMPLETE" and collection["requested"] != len(symbols))
        ):
            raise ValueError("Status collection has inconsistent coverage counts")
        seen = set()
        for record in collection["records"]:
            symbol = record["symbol"]
            if symbol not in symbols or symbol in seen or symbol not in bundle["meta"]:
                raise ValueError("Unknown or duplicate security in status collection")
            seen.add(symbol)
            response = source / f"{symbol}.json.gz"
            if sha(response) != record["response_sha256"]:
                raise ValueError("Status response hash mismatch")
            if record["status"] != "PARSED":
                continue
            if symbol in frames:
                raise ValueError("Overlapping successful status histories need separate review")
            frame = parse_history(load_gzip(response), symbol, bundle["calendar"])
            frames[symbol] = frame
            detail = inspect_history(
                frame,
                bundle["raws"][symbol],
                bundle["panels"]["eligible"][symbol],
                bundle["calendar"],
            )
            records.append({"symbol": symbol, "name": bundle["meta"][symbol]["name"], **detail})
        if len(seen) != collection["requested"]:
            raise ValueError("Status collection request count is inconsistent")
        sources.append(
            {
                "path": source.relative_to(paths.root).as_posix(),
                "protocol_sha256": sha(source / "protocol.json"),
                "collection_sha256": sha(source / "collection.json"),
            }
        )
    fields = (
        "provider_rows",
        "overlap_sessions",
        "ohlc_comparisons",
        "raw_sessions_without_status",
        "internal_gaps",
        "gaps_flagged_suspended",
        "gaps_flagged_active",
        "gaps_without_status",
        "raw_sessions_flagged_suspended",
        "st_sessions",
        "st_eligible_sessions",
    )
    summary = {
        "status": "SOURCE_SCREENED_NOT_CERTIFIED",
        "checked_at": now(),
        "source_bundle_sha256": sha(paths.bundle),
        "sources": sources,
        "screened_securities": len(frames),
        "universe_securities": len(bundle["meta"]),
        "unscreened_symbols": sorted(set(bundle["meta"]) - set(frames)),
        "st_securities": sum(r["st_sessions"] > 0 for r in records),
        "ohlc_mismatches": sum(len(r["ohlc_mismatches"]) for r in records),
        **{field: sum(r[field] for r in records) for field in fields},
        "activated": False,
        "known_at_established": False,
        "limitations": [
            "Daily flags are retrospective vendor observations, not dated exchange announcements.",
            "Current selected securities cannot certify historical universe or industry membership.",
            "A suspended flag does not establish what was publicly known before the opening auction.",
            "Price agreement supports identity checks, not the accuracy or completeness of every flag.",
        ],
    }
    save(target / "summary.json", summary, immutable=True)
    dump_gzip(target / "securities.json.gz", sorted(records, key=lambda r: r["symbol"]))
    return summary
