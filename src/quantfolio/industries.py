"""Resolve source-bound industry groups at a stated information time."""

import re
from bisect import bisect_right
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .integrity import runtime_sources


@lru_cache(maxsize=4096)
def _schedule(entries):
    times: list[int] = []
    groups = []
    for available_at, industry in entries:
        available = pd.Timestamp(available_at)
        if pd.isna(available) or available.tzinfo is None:
            raise ValueError("Industry availability requires an explicit timezone")
        if times and available.value <= times[-1]:
            raise ValueError("Industry releases must have unique increasing availability times")
        if industry is not None and (not isinstance(industry, str) or not industry.strip()):
            raise ValueError("Industry must be a nonempty key or explicit null")
        times.append(available.value)
        groups.append(industry)
    if not times:
        raise ValueError("Industry history cannot be empty")
    return times, groups


def _history(instrument):
    fund = "fund_history" in instrument
    if fund:
        if instrument["kind"] != "etf" or "industry_history" in instrument:
            raise ValueError("Fund history cannot classify a company")
        history = instrument["fund_history"]
    else:
        if instrument["kind"] != "stock":
            raise ValueError("Company industry history cannot classify an ETF")
        history = instrument["industry_history"]
    if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
        raise ValueError("Industry history must be a list of dated records")
    if fund and any((row["industry"] is None) != (row["profile"] is None) for row in history):
        raise ValueError("Fund group and profile coverage disagree")
    return _schedule(tuple((row["available_at"], row["industry"]) for row in history))


def _information_time(as_of):
    value = pd.Timestamp(as_of)
    if pd.isna(value) or value.tzinfo is None:
        raise ValueError("Industry lookup requires a timezone-aware information time")
    return value.value


def _current(instrument, information_time):
    if not ("industry_history" in instrument or "fund_history" in instrument):
        return instrument["industry"]
    times, groups = _history(instrument)
    index = bisect_right(times, information_time) - 1
    return groups[index] if index >= 0 else None


def industry_at(instrument, as_of):
    """An omitted or not-yet-published classification has no static-label fallback."""
    return _current(instrument, _information_time(as_of))


def metadata_at(meta, as_of):
    """Return a dated view while retaining histories for a later opening-time check."""
    if not any(
        "industry_history" in instrument or "fund_history" in instrument
        for instrument in meta.values()
    ):
        return meta
    information_time = _information_time(as_of)
    result = {
        symbol: {**instrument, "industry": _current(instrument, information_time)}
        for symbol, instrument in meta.items()
    }
    for instrument in result.values():
        if "fund_history" in instrument:
            times, _ = _history(instrument)
            index = bisect_right(times, information_time) - 1
            instrument["fund_profile"] = (
                instrument["fund_history"][index]["profile"] if index >= 0 else None
            )
    return result


def require_industries(meta, holdings):
    missing = sorted(symbol for symbol in holdings if meta[symbol]["industry"] is None)
    if missing:
        raise ValueError(f"Unknown industry for existing holding: {missing}")


def industry_series(instrument, calendar):
    """Use each completed session's 16:00 information set, without future backfill."""
    if not ("industry_history" in instrument or "fund_history" in instrument):
        return pd.Series(instrument["industry"], index=calendar, dtype=object)
    times, groups = _history(instrument)
    closing = calendar.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=16)
    indices = [bisect_right(times, value) - 1 for value in closing.asi8]
    return pd.Series(
        [groups[index] if index >= 0 else None for index in indices], index=calendar, dtype=object
    )


def compile_histories(meta, calendar, releases, aliases, variant):
    """Join reviewed source cells and retain explicit absence in each published release."""
    if variant not in ("coverage", "static", "dated"):
        raise ValueError("Unknown industry comparison")
    if len(set(aliases.values())) != len(aliases) or set(aliases) & set(aliases.values()):
        raise ValueError("Industry aliases must be one-to-one without chains")
    reverse = {new: old for old, new in aliases.items()}
    dates = [release["catalogue_date"] for release in releases]
    if not dates or dates != sorted(set(dates)):
        raise ValueError("Industry release dates must be unique and ordered")
    inventories, openings = [], []
    for release in releases:
        date = release["catalogue_date"]
        if str(pd.Timestamp(date).date()) != date:
            raise ValueError("Industry catalogue date must be an ISO date")
        next_index = calendar.searchsorted(pd.Timestamp(date), side="right")
        if next_index == len(calendar):
            raise ValueError("Industry release has no following observed session")
        openings.append(str(calendar[next_index].date()) + "T00:00:00+08:00")
        rows = {row["code"]: row for row in release["records"]}
        if len(rows) != len(release["records"]):
            raise ValueError("Duplicate security code in an industry release")
        for code, row in rows.items():
            if not re.fullmatch(r"[0-9]{6}", code):
                raise ValueError("Industry source requires a six-digit security code")
            if not re.fullmatch(r"[A-S][0-9]{2}", row["section"] + row["division"]):
                raise ValueError("Invalid published industry division")
            if any(f["problem"] != "name_mismatch" for f in row.get("taxonomy_findings", [])):
                raise ValueError("Industry code or parent findings remain unresolved")
        inventories.append(rows)

    def match(symbol, rows):
        codes = [symbol[2:]]
        if symbol in reverse:
            codes.append(reverse[symbol][2:])
        matched = [rows[code] for code in codes if code in rows]
        if len(matched) > 1:
            raise ValueError(f"Ambiguous current/old code in industry release: {symbol}")
        return matched[0] if matched else None

    def evidence(release, row):
        return {
            "path": release["source"],
            "sha256": release["source_sha256"],
            "page": row["page"] if row else None,
            "row": row["row"] if row else None,
            "source_code": row["code"] if row else None,
        }

    result = deepcopy(meta)
    for symbol, instrument in result.items():
        if "industry_history" in instrument:
            raise ValueError("Parent already contains industry history")
        if instrument["kind"] != "stock":
            continue
        latest = match(symbol, inventories[-1])
        history: list[dict] = []
        for release, rows, available_at in zip(releases, inventories, openings):
            row = match(symbol, rows)
            classification, group = None, None
            if row:
                if variant == "coverage":
                    group = instrument["industry"]
                    classification = {"kind": "parent_static_metadata"}
                else:
                    selected = latest if variant == "static" else row
                    if selected is None:
                        raise ValueError("Static control would need a future-dependent mask")
                    group = "capco:" + selected["section"] + selected["division"]
                    classification = evidence(
                        releases[-1] if variant == "static" else release, selected
                    )
            record = {
                "available_at": available_at,
                "catalogue_date": release["catalogue_date"],
                "industry": group,
                "coverage_source": evidence(release, row),
                "classification_source": classification,
            }
            # Multiple releases during a market closure share an observable boundary.
            if history and history[-1]["available_at"] == available_at:
                history[-1] = record
            else:
                history.append(record)
        instrument["industry_history"] = history
        _history(instrument)
    return result


def build_industry_snapshot(paths, protocol_path, variant):
    """Build one registered data control; keep the active project configuration unchanged."""
    import joblib

    from .dataset import check_integrity, load_snapshot, validate_features
    from .preprocessing import build_dataset
    from .storage import load_gzip, now, read, save, sha

    def inside(name):
        file = (paths.root / name).resolve()
        if Path(name).is_absolute() or not file.is_relative_to(paths.root):
            raise ValueError("Industry paths must stay inside the project")
        return file

    protocol_file = inside(protocol_path)
    protocol = read(protocol_file)
    if protocol.get("name") != "industry_timing" or variant not in ("coverage", "static", "dated"):
        raise ValueError("Expected a registered industry comparison")
    target = inside(protocol["variants"][variant]["snapshot_output"])
    parent = (paths.root / "data/processed").resolve()
    if not target.is_relative_to(parent) or target == parent:
        raise ValueError("Industry snapshot must be a named directory under data/processed")
    if target.exists():
        raise FileExistsError("Industry snapshot output already exists")
    integrity = check_integrity(paths)
    manifest = read(paths.manifest)
    if manifest["files"].get(Path(protocol_path).as_posix()) != sha(protocol_file):
        raise ValueError("Industry protocol is not frozen")
    for name, expected in protocol["source_sha256"].items():
        if manifest["files"].get(name) != expected or sha(inside(name)) != expected:
            raise ValueError(f"Industry input changed: {name}")
    receipt_file = inside(protocol["checks_output"]) / "parent_rebuild.json"
    receipt = read(receipt_file)
    if (
        manifest["files"].get(receipt_file.relative_to(paths.root).as_posix()) != sha(receipt_file)
        or receipt["status"] != "PASS"
        or receipt["protocol_sha256"] != sha(protocol_file)
        or receipt["data_sha256"] != protocol["source_bundle_sha256"]
    ):
        raise ValueError("Industry parent rebuild receipt differs")
    source = inside(protocol["source_bundle"])
    if sha(source) != protocol["source_bundle_sha256"]:
        raise ValueError("Industry parent dataset changed")
    original = load_snapshot(paths, source, protocol["source_data_cutoff"])
    review = read(inside(protocol["source_review"]))
    if not review["source_extraction_complete"]:
        raise ValueError("Industry source extraction is incomplete")
    releases = []
    for item in review["sources"]:
        if item["status"] != "PARSED_AND_CROSS_CHECKED":
            raise ValueError("Industry source is not checked")
        if not item["aliases"]:
            if protocol["source_sha256"].get(item["output"]) != item["sha256"]:
                raise ValueError("Industry extraction is not bound to the protocol")
            releases.append(load_gzip(inside(item["output"])))
    aliases: dict[str, str] = {}
    for record in read(inside(protocol["identity_links"]))["records"]:
        old, new = record["old_symbol"], record["current_symbol"]
        if old in aliases and aliases[old] != new:
            raise ValueError("Conflicting industry identity links")
        aliases[old] = new
    meta = compile_histories(original["meta"], original["calendar"], releases, aliases, variant)
    sources = runtime_sources(Path(__file__).parent)
    configuration_sha = sha(paths.root / "configs/project.toml")
    provenance = {
        "started_at": now(),
        "purpose": "industry_timing",
        "variant": variant,
        "protocol_sha256": sha(protocol_file),
        "parent_rebuild_sha256": sha(receipt_file),
        "source_bundle_sha256": sha(source),
        "source_sha256": sources,
        "source_review_sha256": sha(inside(protocol["source_review"])),
        "availability": protocol["classification"]["availability"],
        "historical_availability_certified": False,
    }
    save(target / "provenance.json", provenance, immutable=True)
    repaired = build_dataset(
        meta, original["raws"], original["actions"], opening_status=original.get("opening_status")
    )
    pd.testing.assert_index_equal(original["calendar"], repaired["calendar"], exact=True)
    if original["actions"] != repaired["actions"] or original.get("opening_status") != repaired.get(
        "opening_status"
    ):
        raise ValueError("Industry repair changed corporate actions or opening status")
    for symbol, raw in original["raws"].items():
        pd.testing.assert_frame_equal(raw, repaired["raws"][symbol], check_exact=True)
        retained = {k: v for k, v in meta[symbol].items() if k != "industry_history"}
        if retained != original["meta"][symbol]:
            raise ValueError("Industry repair changed original security metadata")
    for name, panel in original["panels"].items():
        if name != "eligible":
            pd.testing.assert_frame_equal(panel, repaired["panels"][name], check_exact=True)
    before, after = original["table"], repaired["table"]
    if (after.eligible & ~before.eligible).any():
        raise ValueError("Industry coverage cannot add eligible rows")
    removed = before.eligible & ~after.eligible
    coverage = read(inside(protocol["source_review"]).with_name("coverage.json"))
    if int(removed.sum()) != coverage["eligible_stock_rows_without_current_release_match"]:
        raise ValueError("Industry eligibility differs from the frozen coverage review")
    labels = [f"{field}{h}" for h in (1, 3, 5) for field in ("y", "end", "status", "delay")]
    pd.testing.assert_frame_equal(
        before.loc[~removed, labels], after.loc[~removed, labels], check_exact=True
    )
    mutable = set(labels) | {
        "industry",
        "eligible",
        "breadth",
        "momentum_score",
        "lowvol_score",
        "reversal_score",
        "momentum_percentile",
    }
    fixed = [c for c in before if c not in mutable and not c.startswith(("rank_", "industry_"))]
    pd.testing.assert_frame_equal(before[fixed], after[fixed], check_exact=True)
    feature_check = validate_features(repaired)
    repaired["provenance"] = provenance
    differences = {
        name: int(
            (~(before[name].eq(after[name]) | (before[name].isna() & after[name].isna()))).sum()
        )
        for name in before
    }
    save(target / "field_changes.json", differences, immutable=True)
    joblib.dump(repaired, target / "bundle.joblib", compress=3)
    if sources != runtime_sources(Path(__file__).parent):
        raise ValueError("Implementation changed during industry construction")
    if sha(paths.root / "configs/project.toml") != configuration_sha:
        raise ValueError("Active configuration changed during industry construction")
    audit = {
        "status": "PASS",
        "completed_at": now(),
        "variant": variant,
        "source_integrity": integrity,
        "bundle_sha256": sha(target / "bundle.joblib"),
        "removed_eligible_rows": int(removed.sum()),
        "features": feature_check,
        "raw_prices_actions_calendar_original_metadata_exact": True,
        "labels_outside_removed_rows_exact": True,
        "historical_availability_certified": False,
        "activated": False,
        "limitations": protocol["limitations"],
    }
    save(target / "audit.json", audit, immutable=True)
    for file in sorted(target.iterdir()):
        manifest["files"][file.relative_to(paths.root).as_posix()] = sha(file)
    save(paths.manifest, manifest)
    return audit
