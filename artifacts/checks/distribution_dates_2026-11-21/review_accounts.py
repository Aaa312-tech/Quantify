"""Compare complete accounts and preserve all outer quality and reclassification exposure."""

from collections import Counter, defaultdict
from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.feasibility import account_differences
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha
from quantfolio.validation import checked_industries

root = Path.cwd()
p = read(root / "configs/protocols/distribution_availability.json")
paths = Paths(root, p["configuration"])
bundle = load_bundle(paths)
checks, evaluation = root / p["checks_output"], root / p["evaluation_output"]
validation = read(checks / "training_validation.json")
assert validation["status"] == "PASS"
previous = root / p["baseline_evaluation"]
summaries = {
    "before": read(previous / "evaluation_summary.json"),
    "fixed": read(root / p["fixed_forecast_output"] / "summary.json"),
    "after": read(evaluation / "evaluation_summary.json"),
}
for name, directory in [
    ("before", previous),
    ("fixed", root / p["fixed_forecast_output"]),
    ("after", evaluation),
]:
    assert summaries[name]["accounts"] == 1425
    for relative, checksum in summaries[name]["artifacts"].items():
        assert sha(directory / relative) == checksum
comparisons = {}
for name, before, after in [
    ("data_execution", previous, root / p["fixed_forecast_output"]),
    ("retraining_selection", root / p["fixed_forecast_output"], evaluation),
    ("combined", previous, evaluation),
]:
    diff = account_differences(before, after, summaries["after"]["artifacts"])
    save(checks / (name + "_accounts.json"), diff, immutable=True)
    comparisons[name] = {
        "accounts": len(diff),
        "full_accounts_exact": sum(row["full_account_equal"] for row in diff),
        "returns_changed": sum(row["before"]["return"] != row["after"]["return"] for row in diff),
    }
status_protocol = read(root / "configs/protocols/historical_status_repair.json")
facts = read(root / status_protocol["review"])["facts"]
groups = {}
changes = {}
previous_groups = None
for date in bundle["calendar"]:
    day = str(date.date())
    current = checked_industries(bundle["meta"], day + "T09:25:00+08:00")
    groups[day] = current
    if previous_groups is not None:
        changed = {
            s: {"before": previous_groups[s], "after": current[s]}
            for s in current
            if current[s] != previous_groups[s]
        }
        if changed:
            changes[day] = (changed, previous_groups)
    previous_groups = current

arms = {}
for name, directory in [("fixed", root / p["fixed_forecast_output"]), ("after", evaluation)]:
    counts = Counter()
    quality, unresolved, warning, passive = [], [], [], []
    rejected = Counter()
    summary = summaries[name]
    for relative, checksum in summary["artifacts"].items():
        stored = load_gzip(directory / relative)
        for index, account in enumerate(stored if isinstance(stored, list) else [stored]):
            location = {"path": relative, "account_index": index}
            counts["accounts"] += 1
            counts["summary_unresolved"] += account["summary"]["unresolved_events"]
            ledger = account["ledger"]
            if ledger["quality"]:
                quality.append({**location, "quality": ledger["quality"]})
                counts["quality_occurrences"] += len(ledger["quality"])
            for event in ledger["events"]:
                if event["type"] != "CORPORATE_ACTION":
                    continue
                flags = [
                    key
                    for key in (
                        "unknown_cash_pay_date",
                        "unknown_cash_available_date",
                        "unknown_share_list_date",
                        "bonus_tax_not_modelled",
                    )
                    if event.get(key)
                ]
                if event.get("fractional_shares_discarded", 0) > 0:
                    flags.append("fractional_shares_discarded")
                if flags:
                    unresolved.append({**location, "flags": flags, "event": event})
            for rejection in ledger["rejections"]:
                if rejection["side"] == "BUY":
                    rejected[rejection["reason"]] += 1
            for trade in ledger["trades"]:
                if trade["side"] == "BUY" and any(
                    trade["symbol"] == fact["symbol"]
                    and fact["start"] <= trade["date"] < fact["end"]
                    for fact in facts
                ):
                    warning.append({**location, "trade": trade})
            prior = None
            for row in ledger["curve"]:
                day = row["date"]
                if prior is not None and day in changes:
                    changed, old_groups = changes[day]
                    affected = sorted(set(prior["positions"]) & set(changed))
                    if affected:
                        grouped_before, grouped_after = defaultdict(float), defaultdict(float)
                        for symbol, position in prior["positions"].items():
                            grouped_before[old_groups[symbol]] += position["value"] / prior["nav"]
                            grouped_after[groups[day][symbol]] += position["value"] / prior["nav"]
                        decision = next(
                            item for item in ledger["decisions"] if item["execution_date"] == day
                        )
                        passive.append(
                            {
                                **location,
                                "date": day,
                                "symbols": {s: changed[s] for s in affected},
                                "valuation_date": prior["date"],
                                "prior_close_nav": prior["nav"],
                                "largest_industry_before": max(grouped_before.values(), default=0),
                                "largest_industry_after": max(grouped_after.values(), default=0),
                                "scheduled": decision["scheduled"],
                                "forced": decision["forced"],
                                "same_day_buys": sum(
                                    t["side"] == "BUY" and t["date"] == day
                                    for t in ledger["trades"]
                                ),
                            }
                        )
                prior = row
    assert counts["accounts"] == 1425 and not warning
    assert counts["summary_unresolved"] == len(unresolved)
    arms[name] = {
        "accounts": counts["accounts"],
        "warning_buy_occurrences": 0,
        "quality_occurrences": counts["quality_occurrences"],
        "quality_accounts": quality,
        "unresolved_occurrences": len(unresolved),
        "unresolved_events": unresolved,
        "buy_rejection_counts": dict(rejected),
        "held_reclassification_occurrences": len(passive),
        "reclassification_records": passive,
        "reclassification_scope": "Isolate the classification change on carried quantities at the previous close prices and NAV, before any new execution. These weights are not actual opening exposure or cap violations; new buys are independently audited at opening prices.",
    }
save(
    checks / "account_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "operation": p["operation"],
        "bundle_sha256": sha(paths.bundle),
        "source_sha256": validation["source_sha256"],
        "comparisons": comparisons,
        "arms": arms,
        "status_source_sha256": status_protocol["review_sha256"],
        "scope": "All 19 policies and three costs, including unselected rules. Full account differences are preserved; reconciliation passes do not clear unsupported event terms or stale marks.",
    },
    immutable=True,
)
print(
    "OUTER ACCOUNT REVIEW PASSED",
    comparisons,
    {
        name: {
            key: value
            for key, value in result.items()
            if key
            in (
                "accounts",
                "quality_occurrences",
                "unresolved_occurrences",
                "held_reclassification_occurrences",
            )
        }
        for name, result in arms.items()
    },
    flush=True,
)
