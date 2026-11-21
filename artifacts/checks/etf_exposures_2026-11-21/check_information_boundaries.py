"""Perturb future classification, identity and alias sources across the complete calendar."""

import copy
import importlib.util
from collections import Counter
from pathlib import Path

from quantfolio.storage import load_gzip, now, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
OUTPUT = ROOT / "data/processed/etf_exposures_2026-11-21"
spec = importlib.util.spec_from_file_location(
    "history_builder", CHECKS / "build_exposure_history.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def choose_release(records, day):
    known = [
        r for r in records if r["available_session"] is not None and r["available_session"] <= day
    ]
    return max(known, key=lambda r: r["catalogue_date"], default=None)


def main():
    protocol, calendar, reports, summaries, releases, links = builder.inputs()
    profiles = load_gzip(OUTPUT / "equity_profiles.json.gz")
    counts = Counter()
    report_lookup = {r["source"]: r for r in reports}
    by_first_session = {}
    for profile in profiles:
        by_first_session.setdefault(profile["first_session"], []).append(profile)
    compact_summaries = [
        {
            k: copy.deepcopy(r[k])
            for k in (
                "symbol",
                "source_path",
                "information_date",
                "available_session",
                "agreed_fields",
            )
        }
        for r in summaries
    ]
    for day in calendar:
        poisoned = [
            {**r, "inventory": {"future_poison": {"section": "INVALID"}}, "source": "future_poison"}
            if r["available_session"] is None or r["available_session"] > day
            else r
            for r in releases
        ]
        truncated = [
            r for r in releases if r["available_session"] and r["available_session"] <= day
        ]
        selected = [choose_release(records, day) for records in (releases, poisoned, truncated)]
        if len({r["source"] if r else None for r in selected}) != 1:
            raise ValueError("Future classification source changes prior choices")
        altered_links = [
            {**r, "old_symbol": "bj999999", "current_symbol": "bj888888"}
            if r["available_session"] is None or r["available_session"] > day
            else r
            for r in links
        ]
        link_prefix = [r for r in links if r["available_session"] and r["available_session"] <= day]
        alias_sets = [
            builder.alias_inventory(records, day) for records in (links, altered_links, link_prefix)
        ]
        if not alias_sets[0] == alias_sets[1] == alias_sets[2]:
            raise ValueError("Future aliases affect the past")
        for profile in by_first_session.get(day, []):
            report = report_lookup[profile["source_report"]]
            for release, aliases in zip(selected, alias_sets):
                actual = builder.equity_profile(report, release, aliases)
                if any(profile[key] != value for key, value in actual.items()):
                    raise ValueError("Future source perturbation changes an exposure profile")
            counts["profile_future_classification_alias_checks"] += 1
        for symbol in protocol["symbols"]:
            relevant = [r for r in compact_summaries if r["symbol"] == symbol]
            altered = [
                {
                    **r,
                    "source_path": "future_poison",
                    "agreed_fields": {"prepared_on": "2099-12-31"},
                }
                if r["available_session"] is None or r["available_session"] > day
                else r
                for r in relevant
            ]
            prefix = [
                r for r in relevant if r["available_session"] and r["available_session"] <= day
            ]
            if (
                len(
                    {
                        builder.select_summary(rows, day)["source_path"]
                        for rows in (relevant, altered, prefix)
                    }
                )
                != 1
            ):
                raise ValueError("Future summary identity affects prior selection")
            counts["summary_future_invariance_checks"] += 1
        counts["classification_alias_future_invariance_sessions"] += 1
    for record in reports + summaries + releases + links:
        information_date = record.get("information_date", record.get("catalogue_date"))
        actual = builder.next_session(calendar, information_date)
        expected = next((day for day in calendar if day > information_date), None)
        if actual != expected or (actual is not None and actual <= information_date):
            raise ValueError("Same-day or nonobserved-session availability")
        counts["availability_boundaries"] += 1
    if builder.next_session(calendar, "2024-02-08") != "2024-02-19":
        raise ValueError("Spring Festival closure boundary differs")
    sample = {"section": "C", "division": "39"}
    if builder.match_stock("000001", {"inventory": {}}, {})[0] is not None:
        raise ValueError("Missing current classifications were carried")
    if builder.match_stock("000001", None, {})[1] != "NO_AVAILABLE_CAPCO_RELEASE":
        raise ValueError("Before-first-release classification was invented")
    if (
        builder.match_stock(
            "000001",
            {"inventory": {"000001": sample, "000002": sample}},
            {"000001": {"000002": []}},
        )[1]
        != "AMBIGUOUS_DIRECT_AND_ALIAS"
    ):
        raise ValueError("Ambiguous direct/alias correspondence was accepted")
    save(
        CHECKS / "information_boundaries.json",
        {
            "status": "PASS",
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "builder_sha256": sha(CHECKS / "build_exposure_history.py"),
            "counts": dict(counts),
            "synthetic_cases": [
                "missing_current_classification_not_carried",
                "no_classification_before_first_release",
                "ambiguous_direct_and_alias_rejected",
                "holiday_delayed_availability",
                "same_day_excluded",
            ],
            "activated": False,
            "historical_intraday_time_certified": False,
        },
        immutable=True,
    )
    print("PASS", dict(counts), flush=True)


if __name__ == "__main__":
    main()
