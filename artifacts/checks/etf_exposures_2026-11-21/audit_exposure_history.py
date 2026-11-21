"""Independently rebuild as-of source choices, joins and exact exposure partitions."""

import copy
import importlib.util
from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from pathlib import Path

import joblib

from quantfolio.storage import load_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
OUTPUT = ROOT / "data/processed/etf_exposures_2026-11-21"


def information_date(record):
    if "identity" in record and "delivery_date" in record["identity"]:
        dates = [record["catalogue_date"], record["identity"]["delivery_date"]]
    elif "agreed_fields" in record:
        dates = [record["catalogue_date"], record["agreed_fields"]["prepared_on"]]
    else:
        dates = [
            record[key]
            for key in ("catalogue_date", "notice_date", "source_directory_date")
            if key in record
        ]
    return sorted(dates)[-1]


def first_session(calendar, day):
    return next((session for session in calendar if session > day), None)


def verify_ratio(item, amount, nav):
    ratio = item["nav_fraction"]
    if (
        Fraction(item["amount_cny"]) != amount
        or Fraction(ratio["numerator"], ratio["denominator"]) != amount / nav
    ):
        raise ValueError("Exact reported-NAV partition differs")


def main():
    protocol = read(ROOT / "configs/protocols/etf_exposures.json")
    bundle = joblib.load(ROOT / protocol["baseline_bundle"])
    calendar = [d.strftime("%Y-%m-%d") for d in bundle["calendar"]]
    reports = load_gzip(ROOT / "data/processed/etf_holdings_cells_2026-11-21/observations.json.gz")
    summaries = read(ROOT / "data/processed/etf_sources_2026-11-21/reviewed_observations.json")[
        "records"
    ]
    mapping = {r["symbol"]: r for r in read(CHECKS / "identity_mapping.json")["records"]}
    links = read(ROOT / "artifacts/checks/industry_cells_2026-11-21/identity_links.json")["records"]
    releases = []
    for entry in read(ROOT / "artifacts/checks/industry_cells_2026-11-21/review.json")["sources"]:
        if not entry["aliases"]:
            releases.append(load_gzip(ROOT / entry["output"]))
    profiles = load_gzip(OUTPUT / "equity_profiles.json.gz")
    daily = load_gzip(OUTPUT / "session_profiles.json.gz")
    if len(daily) != len(calendar) * len(protocol["symbols"]):
        raise ValueError("Fund/session inventory is incomplete")
    if {(r["session"], r["symbol"]) for r in daily} != {
        (d, s) for d in calendar for s in protocol["symbols"]
    }:
        raise ValueError("Fund/session keys differ")
    counters, reasons = Counter(), Counter()
    checked_profiles = set()
    latest = []
    for entry in daily:
        day, symbol = entry["session"], entry["symbol"]
        known = [r for r in reports if r["symbol"] == symbol and information_date(r) < day]
        ordered = sorted(
            known,
            key=lambda r: (
                r["identity"]["period_end"],
                int(r["report_type"] in ("annual", "interim")),
                information_date(r),
                r["source"],
            ),
            reverse=True,
        )
        report = ordered[0]
        known_summaries = [
            r for r in summaries if r["symbol"] == symbol and information_date(r) < day
        ]
        summary = sorted(
            known_summaries,
            key=lambda r: (
                information_date(r),
                r["agreed_fields"]["prepared_on"],
                r["source_path"],
            ),
            reverse=True,
        )[0]
        classification = sorted(
            [r for r in releases if information_date(r) < day], key=lambda r: r["catalogue_date"]
        )
        release = classification[-1] if classification else None
        profile = profiles[entry["profile"]]
        if (
            profile["source_report"] != report["source"]
            or profile["source_sha256"] != report["source_sha256"]
        ):
            raise ValueError("As-of report selection differs")
        if (
            profile["identity"]["source"] != summary["source_path"]
            or profile["identity"]["source_sha256"] != summary["source_sha256"]
        ):
            raise ValueError("As-of identity selection differs")
        if (
            profile["identity"]["benchmark_id"] != mapping[symbol]["benchmark_id"]
            or profile["identity"]["benchmark_wording"] != summary["reviewed_benchmark"]
        ):
            raise ValueError("Benchmark identity differs")
        if profile["available_session"] != first_session(calendar, information_date(report)):
            raise ValueError("Report next-observed-session boundary differs")
        if profile["identity"]["available_session"] != first_session(
            calendar, information_date(summary)
        ):
            raise ValueError("Identity boundary differs")
        selected_source = profile["capco_source"]
        if (selected_source["source"] if selected_source else None) != (
            release["source"] if release else None
        ):
            raise ValueError("Latest available CAPCO release differs")
        if (
            entry["report_age_days"]
            != (date.fromisoformat(day) - date.fromisoformat(report["identity"]["period_end"])).days
        ):
            raise ValueError("Report age differs")
        counters["asof_fund_sessions"] += 1
        if day == calendar[-1]:
            latest.append(
                {
                    "symbol": symbol,
                    "period_end": report["identity"]["period_end"],
                    "report_age_days": entry["report_age_days"],
                    "matched_nav_percent": float(
                        Fraction(profile["matched_named"]["amount_cny"])
                        / Fraction(profile["reported_nav_cny"])
                        * 100
                    ),
                    "unmatched_nav_percent": float(
                        Fraction(profile["unmatched_named"]["amount_cny"])
                        / Fraction(profile["reported_nav_cny"])
                        * 100
                    ),
                    "undisclosed_stock_nav_percent": float(
                        Fraction(profile["undisclosed_stocks"]["amount_cny"])
                        / Fraction(profile["reported_nav_cny"])
                        * 100
                    ),
                    "complete_economic_exposure_certified": False,
                }
            )
        if entry["profile"] in checked_profiles:
            continue
        checked_profiles.add(entry["profile"])
        available_links = [link for link in links if information_date(link) < day]
        inventory = {r["code"]: r for r in release["records"]} if release else {}
        source_rows = [
            (gi, ri, row)
            for gi, group in enumerate(report["groups"])
            if group["kind"] == "stocks"
            for ri, row in enumerate(group["rows"])
        ]
        if len(source_rows) != len(profile["named_stocks"]):
            raise ValueError("Stock schedule row count differs")
        divisions, unmatched = defaultdict(Fraction), Fraction(0)
        for (gi, ri, raw), observed in zip(source_rows, profile["named_stocks"], strict=True):
            if (
                observed["source_group"],
                observed["source_row"],
                observed["code"],
                observed["name"],
                observed["market_value_cny"],
            ) != (gi, ri, raw["code"], raw["name"], raw["market_value_cny"]):
                raise ValueError("Named holding or sleeve identity changed")
            alternatives = set()
            for link in available_links:
                pair = [link["old_symbol"][2:], link["current_symbol"][2:]]
                if raw["code"] in pair:
                    alternatives.update(c for c in pair if c != raw["code"] and c in inventory)
            match = None
            if release is None:
                reason = "NO_AVAILABLE_CAPCO_RELEASE"
            elif raw["code"] in inventory and alternatives:
                reason = "AMBIGUOUS_DIRECT_AND_ALIAS"
            elif raw["code"] in inventory:
                match, reason = inventory[raw["code"]], "DIRECT"
            elif len(alternatives) == 1:
                match, reason = inventory[next(iter(alternatives))], "AVAILABLE_IDENTITY_LINK"
            else:
                reason = "CODE_ABSENT_FROM_CURRENT_CAPCO_RELEASE"
            group = "capco:" + match["section"] + match["division"] if match else None
            if observed["division"] != group or observed["match"] != reason:
                raise ValueError("Independent dated code/alias join differs")
            if match and observed["classification_row"] != {
                k: match[k] for k in ("code", "name", "section", "division", "page", "row")
            }:
                raise ValueError("CAPCO source cell identity differs")
            if any(
                link["information_date"] >= day or link["available_session"] > day
                for link in observed["identity_links"]
            ):
                raise ValueError("Future identity evidence was used")
            value = Fraction(raw["market_value_cny"])
            if group:
                divisions[group] += value
            else:
                unmatched += value
            reasons[reason] += 1
            counters["independent_holding_joins"] += 1
        nav = Fraction(report["reported_nav"]["amount_cny"])
        sector_total = sum(
            (
                Fraction(row["market_value_cny"])
                for group in report["groups"]
                if group["kind"] == "sectors"
                for row in group["rows"]
                if row["code"] == "TOTAL"
            ),
            Fraction(0),
        )
        named = sum(divisions.values(), Fraction(0)) + unmatched
        undisclosed = sector_total - named
        residual = (
            Fraction(report["equity_sector_residual"]["amount_cny"])
            if report["equity_sector_residual"]
            else Fraction(0)
        )
        if undisclosed < 0 or set(profile["matched_divisions"]) != set(divisions):
            raise ValueError("Named/unknown equity partition differs")
        for group, amount in divisions.items():
            verify_ratio(profile["matched_divisions"][group], amount, nav)
        for name, value in (
            ("matched_named", named - unmatched),
            ("unmatched_named", unmatched),
            ("undisclosed_stocks", undisclosed),
            ("source_sector_stocks", sector_total),
            ("signed_equity_sector_residual", residual),
        ):
            verify_ratio(profile[name], value, nav)
        if report["schedule_coverage"] == "ALL_REPORTED_STOCKS" and undisclosed:
            raise ValueError("Full stock schedule has undisclosed residual")
        counters["negative_residual_profiles"] += residual < 0
        if not source_rows and not all(report["stock_absence"][name] for name in ("fitz", "pypdf")):
            raise ValueError("Stock absence is unsupported")
        expected_non_equity = [
            {
                "section": g["section"],
                "source_row": ri,
                "name": row["name"],
                "amount_cny": row["amount_cny"],
                "percent_total_assets": row["percent_total_assets"],
            }
            for g in report["groups"]
            if g["kind"] == "assets"
            for ri, row in enumerate(g["rows"])
            if row["ordinal"] is not None and row["name"] not in ("权益投资", "合计")
        ]
        if profile["non_equity_asset_rows"] != expected_non_equity:
            raise ValueError("Non-equity amounts or source dashes changed")
        counters["exact_profile_partitions"] += 1
    if len(checked_profiles) != len(profiles):
        raise ValueError("Unreferenced profile exists")
    # The builder is used only here for adversarial behavior checks, not the audit above.
    spec = importlib.util.spec_from_file_location(
        "exposure_builder", CHECKS / "build_exposure_history.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    augmented_reports = [
        {
            key: copy.deepcopy(report[key])
            for key in ("symbol", "source", "catalogue_date", "identity", "report_type")
        }
        for report in reports
    ]
    for report in augmented_reports:
        report["information_date"] = information_date(report)
        report["available_session"] = first_session(calendar, information_date(report))
    for day in calendar:
        for symbol in protocol["symbols"]:
            all_reports = [r for r in augmented_reports if r["symbol"] == symbol]
            prefix = [r for r in all_reports if information_date(r) < day]
            altered = copy.deepcopy(all_reports)
            for item in altered:
                if item["available_session"] is None or item["available_session"] > day:
                    item["identity"]["period_end"] = "2099-12-31"
                    item["source"] = "future_poison"
            choices = [
                builder.select_report(group, day)["source"]
                for group in (all_reports, prefix, altered)
            ]
            if len(set(choices)) != 1:
                raise ValueError("Future report truncation/perturbation changes the past")
            counters["future_report_invariance_checks"] += 1
    # Delayed old reports and same-period full schedules must obey economic period order.
    base = copy.deepcopy(augmented_reports[0])
    base.update(
        source="new_quarter",
        available_session="2026-01-05",
        information_date="2026-01-04",
        report_type="quarterly",
    )
    base["identity"]["period_end"] = "2025-12-31"
    old, full = copy.deepcopy(base), copy.deepcopy(base)
    old.update(source="late_old_full", available_session="2026-01-06", report_type="annual")
    old["identity"]["period_end"] = "2024-12-31"
    full.update(source="same_period_full", available_session="2026-01-07", report_type="annual")
    if (
        builder.select_report([base, old, full], "2026-01-06")["source"] != "new_quarter"
        or builder.select_report([base, old, full], "2026-01-07")["source"] != "same_period_full"
    ):
        raise ValueError("Period-priority adversarial case failed")
    save(
        CHECKS / "history_audit.json",
        {
            "status": "PASS",
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "builder_sha256": sha(CHECKS / "build_exposure_history.py"),
            "inputs": {
                name: sha(OUTPUT / name)
                for name in ("equity_profiles.json.gz", "session_profiles.json.gz")
            },
            "counts": dict(counters),
            "holding_match_counts": dict(reasons),
            "latest_profiles": latest,
            "future_source_isolation": "Independent as-of joins use only source dates before each session; every earlier report choice survives future truncation and period/source poisoning.",
            "period_priority_cases": "PASS",
            "activated": False,
            "complete_economic_exposure_certified": False,
            "historical_publication_time_certified": False,
        },
        immutable=True,
    )
    print("PASS", dict(counters), dict(reasons), flush=True)


if __name__ == "__main__":
    main()
