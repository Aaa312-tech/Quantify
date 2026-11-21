"""Exercise sourced events in fixed synthetic holdings without fabricating historical fills."""

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import pandas as pd

from quantfolio.dataset import load_bundle
from quantfolio.ledger import Ledger, Lot
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cent(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def expected_case(action, quantity, layout, meta):
    cash = float(action["cash_per_share"])
    if action.get("cash_rounding") == "FLOOR_TO_CENT":
        gross = int(
            (Decimal(quantity) * Decimal(str(cash)) * 100).to_integral_value(rounding=ROUND_FLOOR)
        )
    else:
        require(action.get("cash_rounding") is None, "Unsupported cash rounding")
        gross = cent(float(Fraction(quantity) * Fraction.from_float(cash)))
    rate = protocol["stock_dividend_tax_scenario"] if meta["kind"] == "stock" else 0.0
    reserve = cent(
        float(Fraction.from_float(float(Fraction(gross, 100))) * Fraction.from_float(rate))
    )
    exact_shares = Fraction(quantity) * (Fraction(str(action["share_multiplier"])) - 1)
    require(exact_shares >= 0, "Reverse splits are outside this registered event set")
    extra = exact_shares.numerator // exact_shares.denominator
    available = next(day for day in dates if day > action["cash_pay_date"])
    require(available == action["cash_available_date"], "Cash availability disagrees")
    terminal = max(available, action["ex_date"], action.get("share_list_date") or available)
    terminal_index = dates.index(terminal)
    require(terminal_index + 1 < len(dates), "No subsequent opening for duplicate-credit check")
    return {
        "symbol": action["symbol"],
        "ex_date": action["ex_date"],
        "action_sha256": digest(action),
        "quantity": quantity,
        "layout": layout,
        "gross_cents": gross,
        "tax_cents": reserve,
        "net_cents": gross - reserve,
        "extra_shares": extra,
        "fractional_shares_discarded": float(exact_shares - extra),
        "cash_available_date": available,
        "end_date": dates[terminal_index + 1],
        "unknown_share_list_date": bool(extra and not action.get("share_list_date")),
        "bonus_tax_not_modelled": bool(action.get("bonus_per_share", 0) > 0),
    }


root = Path.cwd()
protocol_file = root / "configs/protocols/recipient_settlement_scenarios.json"
protocol = read(protocol_file)
parent = read(root / protocol["parent_protocol"])
checks = root / parent["checks_output"]
paths = Paths(root, parent["configuration"])
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
require(
    sha(root / protocol["parent_protocol"]) == protocol["parent_protocol_sha256"], "Parent changed"
)
require(source == protocol["source_sha256"], "Registered runtime changed")
require(sha(paths.bundle) == protocol["bundle_sha256"], "Registered data changed")
require(
    sha(checks / "settlement_review.json") == protocol["account_review_sha256"],
    "Historical audit changed",
)
bundle = load_bundle(paths)
dates = [str(day.date()) for day in bundle["calendar"]]
actions = {(a["symbol"], a["ex_date"]): a for a in bundle["actions"]}
cases = []
for key in protocol["events"]:
    action = actions[tuple(key)]
    for quantity in protocol["quantities"]:
        for layout in protocol["lot_layouts"]:
            cases.append(expected_case(action, quantity, layout, bundle["meta"][key[0]]))
    cases.append(expected_case(action, 0, "empty", bundle["meta"][key[0]]))
require(len(cases) == protocol["expected_cases"], "Registered case count differs")
expected_file = checks / "settlement_scenario_expectations.json"
save(
    expected_file,
    {
        "status": "FROZEN_BEFORE_LEDGER_EXECUTION",
        "created_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(protocol_file),
        "bundle_sha256": sha(paths.bundle),
        "cases": cases,
        "scope": "Fixed synthetic holdings; independent financial arithmetic precedes any production Ledger call.",
    },
    immutable=True,
)
records = []
for case in cases:
    symbol, quantity = case["symbol"], case["quantity"]
    action = actions[symbol, case["ex_date"]]
    record_date = pd.Timestamp(action["record_date"])
    ledger = Ledger(
        bundle["meta"],
        initial=protocol["initial_cash_yuan"],
        dividend_tax=protocol["stock_dividend_tax_scenario"],
    )
    lot_quantities = (
        []
        if not quantity
        else [quantity]
        if case["layout"] == "single"
        else [quantity // 2, quantity - quantity // 2]
    )
    ledger.lots[symbol] = [
        Lot(
            i + 1,
            q,
            record_date - pd.Timedelta(days=30),
            record_date,
            protocol["synthetic_initial_stop_cost"],
        )
        for i, q in enumerate(lot_quantities)
    ]
    ledger.next_id = len(lot_quantities) + 1
    ledger.snapshot_record_dates(record_date, [action])
    snapshots = []
    for day in [day for day in dates if action["ex_date"] <= day <= case["end_date"]]:
        moment = pd.Timestamp(day)
        ledger.apply_actions(moment, [action])
        received = case["net_cents"] if day >= case["cash_available_date"] else 0
        require(ledger.cash == protocol["initial_cash_yuan"] * 100 + received, (case, day, "Cash"))
        require(
            sum(item["cents"] for item in ledger.receivables) == case["net_cents"] - received,
            (case, day, "Receivable"),
        )
        require(
            ledger.quantity(symbol) == quantity + case["extra_shares"], (case, day, "Share count")
        )
        available_extra = (
            case["extra_shares"]
            if action.get("share_list_date") and day >= action["share_list_date"]
            else 0
        )
        require(
            ledger.available(symbol, moment) == quantity + available_extra,
            (case, day, "Share availability"),
        )
        snapshots.append(
            {
                "date": day,
                "cash_cents": ledger.cash,
                "receivable_cents": sum(item["cents"] for item in ledger.receivables),
                "shares": ledger.quantity(symbol),
                "available_shares": ledger.available(symbol, moment),
            }
        )
    event_rows = [e for e in ledger.events if e["type"] == "CORPORATE_ACTION"]
    payments = [e for e in ledger.events if e["type"] == "CASH_DIVIDEND_PAID"]
    if not quantity:
        require(not event_rows and not payments, "Empty holding received an entitlement")
    else:
        require(len(event_rows) == 1, "Duplicate or missing corporate action")
        event = event_rows[0]
        for key, expected in {
            "entitled_quantity": quantity,
            "cash_gross": case["gross_cents"] / 100,
            "cash_tax_reserve": case["tax_cents"] / 100,
            "net_receivable": case["net_cents"] / 100,
            "share_quantity_change": case["extra_shares"],
            "extra_shares_restricted": case["extra_shares"],
            "fractional_shares_discarded": case["fractional_shares_discarded"],
            "pay_date": action["cash_pay_date"],
            "cash_available_date": case["cash_available_date"],
            "share_list_date": action.get("share_list_date"),
            "unknown_cash_pay_date": False,
            "unknown_cash_available_date": False,
            "unknown_share_list_date": case["unknown_share_list_date"],
            "bonus_tax_not_modelled": case["bonus_tax_not_modelled"],
        }.items():
            require(event[key] == expected, (case, key, event[key], expected))
        require(len(payments) == int(case["net_cents"] > 0), (case, "Payment count"))
        if payments:
            require(payments[0]["date"] == case["cash_available_date"], "Payment date")
            require(payments[0]["amount"] == case["net_cents"] / 100, "Payment amount")
            require(payments[0]["event_id"] == action["id"], "Payment identity")
    records.append({"case": case, "events": ledger.events, "snapshots": snapshots})
require(
    source == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))},
    "Runtime changed during scenarios",
)
save(
    root / protocol["output"],
    {
        "status": "PASS",
        "completed_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(protocol_file),
        "source_sha256": source,
        "bundle_sha256": sha(paths.bundle),
        "expectations_sha256": sha(expected_file),
        "source_events": len(protocol["events"]),
        "synthetic_cases": len(records),
        "zero_holding_cases": sum(case["quantity"] == 0 for case in cases),
        "records": records,
        "new_historical_accounts": 0,
        "forward_observations_added": 0,
        "competition_tax_policy_confirmed": False,
        "scope": protocol["boundaries"],
    },
    immutable=True,
)
print("SYNTHETIC_SETTLEMENTS_PASSED", len(records), "cases", flush=True)
