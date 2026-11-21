"""Independently reconstruct source amounts, clocks and portfolio exposure summaries."""

from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import pandas as pd

from quantfolio.dataset import load_bundle
from quantfolio.funds import exposure_report, reported_components
from quantfolio.industries import industry_at, metadata_at
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha
from quantfolio.validation import checked_fund_exposure, checked_industries, fund_exposure_equal

root = Path.cwd()
pfile = root / "configs/protocols/etf_integration.json"
p = read(pfile)
checks = root / p["checks_output"]
snapshots = read(checks / "snapshots.json")
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
assert sources == snapshots["source_sha256"]
origin = root / "data/processed/etf_exposures_2026-11-21"
profiles = load_gzip(origin / "equity_profiles.json.gz")
session_rows = load_gzip(origin / "session_profiles.json.gz")
financial = load_gzip(origin / "financial_observations.json.gz")
by_report = {row["source"]: row for row in financial}
assert len(by_report) == len(financial) == 528
assert sum(len(table["rows"]) for row in financial for table in row["contracts"]["tables"]) == 214
expected_profiles = {}
holdings_checked = 0
for index, profile in enumerate(profiles):
    nav = Fraction(profile["reported_nav_cny"])
    assert nav > 0
    matched = defaultdict(Fraction)
    unmatched = Fraction()
    for row in profile["named_stocks"]:
        amount = Fraction(row["market_value_cny"])
        if row["division"] is None:
            unmatched += amount
        else:
            matched[row["division"]] += amount
        holdings_checked += 1
    assert sum(matched.values()) == Fraction(profile["matched_named"]["amount_cny"])
    assert unmatched == Fraction(profile["unmatched_named"]["amount_cny"])
    assert dict(matched) == {
        key: Fraction(value["amount_cny"]) for key, value in profile["matched_divisions"].items()
    }
    undisclosed = (
        Fraction(profile["source_sector_stocks"]["amount_cny"]) - sum(matched.values()) - unmatched
    )
    assert undisclosed == Fraction(profile["undisclosed_stocks"]["amount_cny"]) >= 0
    observed = by_report[profile["source_report"]]
    assert observed["source_sha256"] == profile["source_sha256"]
    contracts = [
        dict(row, source_unit=table["unit"])
        for table in observed["contracts"]["tables"]
        for row in table["rows"]
    ]
    index_contracts = [row for row in contracts if row["kind"] == "stock_index_futures"]
    known = {key: value / nav for key, value in matched.items()}
    components = {
        "unmatched_named": unmatched / nav,
        "undisclosed_stocks": undisclosed / nav,
        "residual_uncertainty": abs(
            Fraction(profile["signed_equity_sector_residual"]["amount_cny"])
        )
        / nav,
        "reported_index_contracts": sum(
            (abs(Fraction(row["reported_market_value_cny"])) for row in index_contracts), Fraction()
        )
        / nav,
    }
    expected_profiles[index] = (known, components, index_contracts, contracts)
assert len(expected_profiles) == 752 and holdings_checked == 49535

bundles = {}
records = {}
for variant, setting in p["variants"].items():
    paths = Paths(root, setting["configuration"])
    bundle = load_bundle(paths)
    assert sha(paths.bundle) == snapshots["variants"][variant]["bundle_sha256"]
    assert bundle["fund_mode"] == variant
    bundles[variant] = bundle
    mapped = {}
    for symbol, item in bundle["meta"].items():
        if item["kind"] != "etf":
            assert "fund_history" not in item
            continue
        for event in item["fund_history"]:
            profile = event["profile"]
            index = profile["profile_index"]
            original = profiles[index]
            assert symbol == original["symbol"]
            assert profile["mode"] == variant
            assert event["available_at"] == original["first_session"] + "T00:00:00+08:00"
            assert profile["economic_exposure_upper_bound"] is None
            assert profile["complete_economic_exposure_certified"] is False
            for key, value in original.items():
                if key != "named_stocks":
                    assert profile[key] == value, (variant, symbol, index, key)
            known, components, index_contracts, contracts = expected_profiles[index]
            assert reported_components(profile) == (known, components)
            assert profile["reported_index_contracts"] == index_contracts
            assert profile["other_reported_contracts"] == [
                row for row in contracts if row["kind"] != "stock_index_futures"
            ]
            observed = by_report[original["source_report"]]
            assert profile["security_lending"] == observed["security_lending"]
            assert profile["reported_nominal"] == observed["nominal"]
            assert profile["absence_clauses"] == observed["explicit_absence_clauses"]
            assert index not in mapped
            mapped[index] = profile
    assert len(mapped) == 752
    decisions = 0
    future_checks = 0
    for row in session_rows:
        day, symbol, index = row["session"], row["symbol"], row["profile"]
        source = bundle["meta"][symbol]
        expected = (
            source["industry"]
            if variant == "coverage"
            else profiles[index]["identity"]["benchmark_id"]
        )
        for clock in ("09:25", "16:00"):
            at = day + "T" + clock + ":00+08:00"
            current = metadata_at({symbol: source}, at)[symbol]
            assert (
                current["industry"] == expected == checked_industries({symbol: source}, at)[symbol]
            )
            assert current["fund_profile"] == mapped[index]
            assert industry_at(source, at) == expected
            decisions += 1
        before = metadata_at({symbol: source}, day + "T16:00:00+08:00")[symbol]
        prefix = {
            **source,
            "fund_history": [
                event
                for event in source["fund_history"]
                if event["available_at"] <= day + "T16:00:00+08:00"
            ],
        }
        poison = {
            **source,
            "fund_history": [
                event
                if event["available_at"] <= day + "T16:00:00+08:00"
                else {**event, "industry": "benchmark:future_poison", "profile": {"poison": True}}
                for event in source["fund_history"]
            ],
        }
        for modified in (prefix, poison):
            actual = metadata_at({symbol: modified}, day + "T16:00:00+08:00")[symbol]
            assert (
                actual["industry"] == before["industry"]
                and actual["fund_profile"] == before["fund_profile"]
            )
            future_checks += 1
    for symbol, item in bundle["meta"].items():
        if "fund_history" not in item:
            continue
        boundary = pd.Timestamp(item["fund_history"][0]["available_at"]) - pd.Timedelta(
            nanoseconds=1
        )
        absent = metadata_at({symbol: item}, boundary)[symbol]
        assert absent["industry"] is None and absent["fund_profile"] is None
    records[variant] = {
        "profiles": len(mapped),
        "signal_and_opening_selections": decisions,
        "future_truncation_and_poisoning": future_checks,
        "before_first_release": 22,
    }

pd.testing.assert_frame_equal(
    bundles["identity"]["table"], bundles["reported"]["table"], check_exact=True
)
for variant in ("identity", "reported"):
    before, after = bundles["coverage"], bundles[variant]
    for section in ("panels", "scores"):
        for name in before[section]:
            pd.testing.assert_frame_equal(
                before[section][name], after[section][name], check_exact=True
            )
    fixed = [c for c in before["table"] if c != "industry" and not c.startswith("industry_")]
    pd.testing.assert_frame_equal(before["table"][fixed], after["table"][fixed], check_exact=True)

bundle = bundles["reported"]
portfolio_checks = 0
for day in bundle["calendar"]:
    for clock in ("09:25", "16:00"):
        at = str(day.date()) + "T" + clock + ":00+08:00"
        current = metadata_at(bundle["meta"], at)
        values = {
            symbol: float(1000 + index * 17)
            for index, (symbol, item) in enumerate(current.items())
            if item["industry"] is not None
        }
        nav = sum(values.values()) + 12345
        expected = checked_fund_exposure(bundle["meta"], values, nav, at, sources=True)
        actual = exposure_report(current, values, nav, at, sources=True)
        assert fund_exposure_equal(actual, expected), at
        portfolio_checks += 1

assert sources == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
save(
    checks / "snapshot_review.json",
    {
        "status": "PASS",
        "completed_at": now(),
        "protocol_sha256": sha(pfile),
        "source_sha256": sources,
        "profiles": 752,
        "source_holding_rows": holdings_checked,
        "financial_reports": 528,
        "source_contract_rows": 214,
        "fund_sessions_per_arm": 15356,
        "variants": records,
        "decimal_portfolio_checks": portfolio_checks,
        "identity_reported_model_tables_exact": True,
        "complete_economic_exposure_certified": False,
        "historical_availability_certified": False,
    },
    immutable=True,
)
print("SOURCE AND SNAPSHOT AUDIT PASSED", records, "portfolio checks", portfolio_checks, flush=True)
