"""Explain the single ideal-decimal mismatch with exact binary64 operation rounding."""

from fractions import Fraction
from pathlib import Path

import numpy as np

from quantfolio.dataset import load_snapshot
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.validation import opening_errors

root = Path.cwd()
pfile = root / "configs/protocols/payment_label_precision.json"
p = read(pfile)
main = read(root / p["parent_protocol"])
checks = root / main["checks_output"]
assert sha(root / p["parent_protocol"]) == p["parent_protocol_sha256"]
assert sha(checks / "input_scope.json") == p["failed_review_sha256"]
assert sha(checks / "check_inputs.py") == p["executed_checker_sha256"]
assert sha(checks / "expected_actions.json") == p["expected_actions_sha256"]
assert not (root / main["training_output"]).exists()
failed = read(checks / "input_scope.json")
assert failed["status"] == "FAIL" and len(failed["mismatches"]) == 1
bundle = load_snapshot(Paths(root), root / main["source_bundle"], main["source_data_cutoff"])
assert sha(root / main["source_bundle"]) == p["data_sha256"]
calendar = bundle["calendar"]
traces = []
for mismatch in failed["mismatches"]:
    row = bundle["table"].iloc[mismatch["row"]]
    start = int(row.date_index) + 1
    target = start + mismatch["horizon"]
    end = mismatch["stored"][1]
    assert row.symbol == mismatch["symbol"] and row.eligible
    assert mismatch["stored"][2:] == [0, end - target]
    raw = bundle["raws"][row.symbol]
    entry, exit_date = calendar[start], calendar[end]
    buy, sell = float(raw.at[entry, "open"]), float(raw.at[exit_date, "open"])
    for index, side, price in ((start, "BUY", buy), (end, "SELL", sell)):
        assert not opening_errors(
            {
                "symbol": row.symbol,
                "date": str(calendar[index].date()),
                "side": side,
                "price": price,
            },
            bundle,
        )
    assert end == target
    events = sorted(
        (
            a
            for a in bundle["actions"]
            if a["symbol"] == row.symbol
            and str(entry.date()) <= a["record_date"] < str(exit_date.date())
            and a["ex_date"] <= str(exit_date.date())
        ),
        key=lambda a: a["ex_date"],
    )
    assert len(events) == 1 and events[0]["share_multiplier"] == 1
    operations = []

    def rounded(name, exact):
        value = float(exact)
        operations.append(
            {
                "operation": name,
                "exact_fraction": str(exact),
                "binary64_hex": value.hex(),
                "rounded_fraction": str(Fraction.from_float(value)),
            }
        )
        return Fraction.from_float(value)

    one = Fraction(1)
    quantity = one
    cash = rounded("cash_accrual", quantity * Fraction.from_float(events[0]["cash_per_share"]))
    cash = rounded("cash_balance", Fraction(0) + cash)
    multiplier_delta = rounded(
        "multiplier_minus_one", Fraction.from_float(events[0]["share_multiplier"]) - one
    )
    quantity_delta = rounded("quantity_delta", quantity * multiplier_delta)
    quantity = rounded("quantity_after_event", quantity + quantity_delta)
    equity = rounded("exit_equity", quantity * Fraction.from_float(sell))
    wealth = rounded("gross_wealth", equity + cash)
    ratio = rounded("wealth_over_entry", wealth / Fraction.from_float(buy))
    net = rounded("gross_return", ratio - one)
    stored = float(np.float32(float(net)))
    assert stored == mismatch["stored"][0] == getattr(row, "y" + str(mismatch["horizon"]))
    traces.append(
        {
            "original_mismatch": mismatch,
            "entry_price_hex": buy.hex(),
            "exit_price_hex": sell.hex(),
            "cash_per_share": events[0]["cash_per_share"],
            "cash_per_share_hex": events[0]["cash_per_share"].hex(),
            "event": {
                key: events[0][key]
                for key in (
                    "symbol",
                    "record_date",
                    "ex_date",
                    "cash_per_share",
                    "share_multiplier",
                )
            },
            "operations": operations,
            "stored_float32": stored,
            "passed": True,
        }
    )
save(
    checks / "input_closure.json",
    {
        **failed,
        "status": "PASS",
        "closed_at": now(),
        "initial_status": "FAIL",
        "initial_review_sha256": sha(checks / "input_scope.json"),
        "precision_protocol_sha256": sha(pfile),
        "closure_checker_sha256": sha(Path(__file__)),
        "retained_initial_mismatches": failed["mismatches"],
        "mismatches": [],
        "binary64_traces": traces,
        "absolute_tolerance": 0,
        "relative_tolerance": 0,
        "scope": "All 67689 affected-security label tuples are accounted for. 67688 matched ideal-decimal rational wealth initially; one exact-zero discrepancy is closed by independently rounded binary64 operations and the original float32 boundary. Original source amounts, labels, failed review and executed checker remain unchanged. This authorizes no label change.",
    },
    immutable=True,
)
print(
    "EXACT PAYMENT INPUT CLOSURE PASSED",
    sum(failed["independent_label_checks"].values()),
    len(traces),
    flush=True,
)
