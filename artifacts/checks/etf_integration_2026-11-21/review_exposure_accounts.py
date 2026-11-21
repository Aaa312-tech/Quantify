"""Measure reported-risk and source changes on every account, without inventing daily holdings."""

from collections import Counter
from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha
from quantfolio.validation import checked_fund_exposure, checked_fund_profiles

root = Path.cwd()
p = read(root / "configs/protocols/etf_integration.json")
setting = p["variants"]["reported"]
paths = Paths(root, setting["configuration"])
bundle = load_bundle(paths)
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert sources == read(root / p["checks_output"] / "snapshot_review.json")["source_sha256"]
meta = bundle["meta"]
fund_meta = {s: m for s, m in meta.items() if m["kind"] == "etf"}
changes = {}
previous = None
for date in bundle["calendar"]:
    day = str(date.date())
    current = checked_fund_profiles(fund_meta, day + "T09:25:00+08:00")
    indices = {s: None if item is None else item["profile_index"] for s, item in current.items()}
    if previous is not None:
        changed = {s for s in indices if indices[s] != previous[s]}
        if changed:
            changes[day] = changed
    previous = indices

arms = {}
for arm, directory, filename in (
    ("retrained", root / setting["evaluation_output"], "evaluation_summary.json"),
    ("fixed", root / setting["fixed_forecast_output"], "summary.json"),
):
    summary = read(directory / filename)
    counters = Counter()
    metrics, passive = [], []
    for relative, expected_sha in summary["artifacts"].items():
        assert sha(directory / relative) == expected_sha
        data = load_gzip(directory / relative)
        for index, account in enumerate(data if isinstance(data, list) else [data]):
            counters["accounts"] += 1
            ledger = account["ledger"]
            location = {"path": relative, "account_index": index}
            record = {
                **location,
                "maximum_closing_proxy": 0.0,
                "maximum_uncertified_fund_capital": 0.0,
                "maximum_report_age_days": 0,
                "closing_proxy_above_30pct_sessions": 0,
                "fund_holding_sessions": 0,
                "reported_proxy_buy_violations": 0,
            }
            marks = {
                (row["date"], row["after_fill"]): row for row in ledger["intermediate_weights"]
            }
            for trade_index, trade in enumerate(ledger["trades"], 1):
                observation = marks[trade["date"], trade_index]["fund_exposure"]
                assert observation["complete_economic_exposure_certified"] is False
                assert observation["economic_exposure_upper_bound"] is None
                if trade["side"] == "BUY":
                    counters["buys_checked"] += 1
                    record["reported_proxy_buy_violations"] += (
                        observation["max_reported_equity_weight"] > 0.30 + 1e-7
                    )
            prior = None
            for row in ledger["curve"]:
                observation = row["fund_exposure"]
                record["maximum_closing_proxy"] = max(
                    record["maximum_closing_proxy"], observation["max_reported_equity_weight"]
                )
                record["maximum_uncertified_fund_capital"] = max(
                    record["maximum_uncertified_fund_capital"],
                    observation["uncertified_fund_weight"],
                )
                record["closing_proxy_above_30pct_sessions"] += (
                    observation["max_reported_equity_weight"] > 0.30 + 1e-7
                )
                record["fund_holding_sessions"] += observation["uncertified_fund_weight"] > 0
                if observation["reports"]:
                    record["maximum_report_age_days"] = max(
                        record["maximum_report_age_days"],
                        max(item["report_age_days"] for item in observation["reports"].values()),
                    )
                day = row["date"]
                if prior is not None and set(prior["positions"]) & changes.get(day, set()):
                    values = {s: position["value"] for s, position in prior["positions"].items()}
                    subset = {s: meta[s] for s in values}
                    before = checked_fund_exposure(
                        subset, values, prior["nav"], prior["date"] + "T16:00:00+08:00"
                    )
                    after = checked_fund_exposure(
                        subset, values, prior["nav"], day + "T09:25:00+08:00"
                    )
                    passive.append(
                        {
                            **location,
                            "date": day,
                            "symbols": sorted(set(values) & changes[day]),
                            "valuation_date": prior["date"],
                            "prior_nav": prior["nav"],
                            "before": before,
                            "after": after,
                            "scope": "Same carried shares, previous-close prices and NAV; only source information advances. Not an opening trade or a claimed current economic bound.",
                        }
                    )
                prior = row
            assert record["reported_proxy_buy_violations"] == 0
            metrics.append(record)
    assert counters["accounts"] == 1425
    arms[arm] = {
        "counts": dict(counters),
        "passive_source_change_occurrences": len(passive),
        "passive_source_changes": passive,
        "account_metrics": metrics,
    }
assert sources == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
save(
    root / p["checks_output"] / "reported/exposure_review.json",
    {
        "status": "PASS",
        "completed_at": now(),
        "source_sha256": sources,
        "bundle_sha256": sha(paths.bundle),
        "arms": arms,
        "complete_economic_exposure_certified": False,
        "economic_exposure_upper_bound": None,
        "scope": "All costs and policies. Passive closing concentration may exceed the buy limit; it is not erased or labelled a new purchase violation. Uncertified fund capital is reported separately.",
    },
    immutable=True,
)
print(
    "ALL REPORTED EXPOSURE ACCOUNTS REVIEWED",
    {key: value["counts"] for key, value in arms.items()},
    flush=True,
)
