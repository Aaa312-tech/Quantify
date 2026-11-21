"""Independently trace accepted recipient cash and dates through every affected account."""

from collections import Counter
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import digest, load_gzip, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def round_cent(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cash_amounts(quantity, action, kind):
    """Retain the registered binary64/cent contract; record ideal-decimal differences."""
    cash = float(action["cash_per_share"])
    exact_decimal = Decimal(quantity) * Decimal(str(cash)) * 100
    ideal = int(exact_decimal.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if action.get("cash_rounding") == "FLOOR_TO_CENT":
        gross = int(exact_decimal.to_integral_value(rounding=ROUND_FLOOR))
    else:
        require(action.get("cash_rounding") is None, "Unregistered cash rounding")
        product = float(Fraction(quantity) * Fraction.from_float(cash))
        gross = round_cent(product)
    rate = 0.20 if kind == "stock" else 0.0
    gross_yuan = float(Fraction(gross, 100))
    reserve_product = float(Fraction.from_float(gross_yuan) * Fraction.from_float(rate))
    reserve = round_cent(reserve_product)
    return gross, reserve, gross - reserve, ideal


root = Path.cwd()
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
paths = Paths(root, p["configuration"])
checks = root / p["checks_output"]
bundle = load_bundle(paths)
acceptance = read(checks / "numeric_acceptance.json")
require(acceptance["status"] == "PASS", "Independent numerical acceptance missing")
require(acceptance["bundle_sha256"] == sha(paths.bundle), "Accepted data changed")
changes = read(root / p["snapshot_output"] / "action_changes.json")
affected = {(row["symbol"], row["ex_date"]) for row in changes}
actions = {(row["symbol"], row["ex_date"]): row for row in bundle["actions"]}
require(len(affected) == p["expected_changes"]["events"] == 31, "Affected event count differs")
require(digest(bundle["actions"]) == acceptance["actions_sha256"], "Accepted actions changed")
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
require(sources == acceptance["source_sha256"], "Runtime changed")
openings = [str(day.date()) for day in bundle["calendar"]]
records, counters, observed = [], Counter(), set()
for identity in sorted(affected):
    action = actions[identity]
    next_opening = next((day for day in openings if day > action["cash_pay_date"]), None)
    require(action["cash_available_date"] == next_opening, (identity, "availability"))
    require(action["share_multiplier"] >= 1, (identity, "Unexpected reverse split"))


def check(account, location):
    arm = location["arm"]
    counters[arm + "_accounts"] += 1
    ledger = account["ledger"]
    curve = {row["date"]: row for row in ledger["curve"]}
    dates = list(curve)
    require(dates == sorted(dates), "Unordered account dates")
    seen = set()
    for event in ledger["events"]:
        identity = event["symbol"], event["date"]
        if event["type"] != "CORPORATE_ACTION" or identity not in affected:
            continue
        require(identity not in seen, (location, identity, "Duplicate entitlement"))
        seen.add(identity)
        observed.add(identity)
        action = actions[identity]
        record_date = action["record_date"]
        require(record_date in curve, (location, identity, "Record date missing from account"))
        quantity = curve[record_date]["positions"].get(identity[0], {}).get("quantity", 0)
        require(quantity == event["entitled_quantity"] > 0, (location, identity, "Record quantity"))
        gross, reserve, net, ideal = cash_amounts(
            quantity, action, bundle["meta"][identity[0]]["kind"]
        )
        require(event["cash_gross"] == gross / 100, (location, identity, "Gross recipient cash"))
        require(event["cash_tax_reserve"] == reserve / 100, (location, identity, "Tax scenario"))
        require(event["net_receivable"] == net / 100, (location, identity, "Net receivable"))
        extra_exact = Decimal(quantity) * (Decimal(str(action["share_multiplier"])) - 1)
        extra = int(extra_exact.to_integral_value(rounding=ROUND_FLOOR))
        require(event["share_quantity_change"] == extra, (location, identity, "Share quantity"))
        require(event["extra_shares_restricted"] == max(extra, 0), "Restricted share quantity")
        require(
            event["fractional_shares_discarded"] == float(extra_exact - extra), "Fractional flag"
        )
        require(event["pay_date"] == action["cash_pay_date"], "Payment date changed")
        require(
            event["cash_available_date"] == action["cash_available_date"], "Availability changed"
        )
        require(event["share_list_date"] == action.get("share_list_date"), "Listing date changed")
        require(not event["unknown_cash_pay_date"], "Supported payment date still unknown")
        require(not event["unknown_cash_available_date"], "Supported availability still unknown")
        require(
            event["unknown_share_list_date"] == bool(extra and not action.get("share_list_date")),
            "Listing flag",
        )
        require(
            event["bonus_tax_not_modelled"] == bool(action.get("bonus_per_share", 0) > 0),
            "Bonus tax flag",
        )
        paid = [
            row
            for row in ledger["events"]
            if row["type"] == "CASH_DIVIDEND_PAID" and row["event_id"] == action["id"]
        ]
        available = [day for day in dates if day >= action["cash_available_date"]]
        if available and net > 0:
            require(len(paid) == 1, (location, identity, "Payment count"))
            require(paid[0]["date"] == available[0] > action["cash_pay_date"], "Premature payment")
            require(paid[0]["amount"] == net / 100, "Payment amount")
            require(paid[0]["pay_date"] == action["cash_pay_date"], "Paid date source")
            require(
                paid[0]["cash_available_date"] == action["cash_available_date"], "Paid availability"
            )
        else:
            require(not paid, (location, identity, "Unavailable or zero payment"))
        counters[arm + "_affected_event_occurrences"] += 1
        counters[arm + "_paid_event_occurrences"] += len(paid)
        counters[arm + "_ideal_decimal_differences"] += gross != ideal
        records.append(
            {
                **location,
                "event": event,
                "action_sha256": digest(action),
                "record_date": record_date,
                "record_date_quantity": quantity,
                "recipient_cash_per_share": action["cash_per_share"],
                "ex_reference_cash_per_share": action.get("ex_reference_cash_per_share"),
                "gross_cents": gross,
                "tax_reserve_cents": reserve,
                "net_cents": net,
                "ideal_decimal_gross_cents": ideal,
                "payments": paid,
            }
        )


for arm, directory, summary_file in (
    ("retrained", root / p["evaluation_output"], "evaluation_summary.json"),
    ("fixed", root / p["fixed_forecast_output"], "summary.json"),
):
    summary = read(directory / summary_file)
    for relative, expected in summary["artifacts"].items():
        file = directory / relative
        require(sha(file) == expected, (arm, relative, "Account artifact changed"))
        value = load_gzip(file)
        for index, account in enumerate(value if isinstance(value, list) else [value]):
            check(account, {"arm": arm, "path": file.relative_to(root).as_posix(), "index": index})
    require(counters[arm + "_accounts"] == 1425, (arm, "Account inventory"))
run = root / p["training_output"]
for file in sorted(run.rglob("*.ledgers.json.gz")):
    if "/inner_" not in file.relative_to(run).as_posix():
        continue
    metadata = read(file.with_name(file.name.replace(".ledgers.json.gz", ".json")))
    require(metadata["ledger_sha256"] == sha(file), "Inner ledger changed")
    value = load_gzip(file)
    for index, account in enumerate([*value["episodes"], value["continuous"]]):
        check(account, {"arm": "inner", "path": file.relative_to(root).as_posix(), "index": index})
require(
    counters["inner_accounts"] == read(checks / "training_review.json")["inner_accounts"],
    "Inner inventory",
)
require(
    sources == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))},
    "Runtime changed during audit",
)
save(
    checks / "settlement_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "numeric_acceptance_sha256": sha(checks / "numeric_acceptance.json"),
        "bundle_sha256": sha(paths.bundle),
        "source_sha256": sources,
        "counts": dict(counters),
        "observed_source_events": sorted(observed),
        "unheld_source_events": sorted(affected - observed),
        "records": records,
        "competition_tax_policy_confirmed": False,
        "stock_dividend_tax_scenario": 0.20,
        "scope": "Every affected inner, retrained and fixed-forecast entitlement is traced to record-date shares, recipient cash, original binary64/cent rounding, tax scenario, share allocation and sourced availability. Ex-reference cash is not used as recipient cash. Unheld source events are explicitly listed; repeated account occurrences are not independent events. Source aggregate conflicts and unsupported rounding remain unresolved.",
    },
    immutable=True,
)
print("SETTLEMENTS REVIEWED", dict(counters), flush=True)
