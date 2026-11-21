"""Freeze independent old/new wealth-label expectations before fitting."""

import ast
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import digest, dump_gzip, now, read, save, sha
from quantfolio.validation import opening_errors


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
checks = root / p["checks_output"]
require(not (root / p["training_output"]).exists(), "Training precedes independent labels")
parent = load_bundle(paths)
require(sha(paths.bundle) == p["source_bundle_sha256"], "Parent changed")
actions = read(checks / "expected_actions.json")
scope = read(checks / "action_scope.json")
require(digest(actions) == scope["actions_sha256"], "Independent actions changed")
changes = read(checks / "expected_action_changes.json")
symbols = sorted({row["symbol"] for row in changes})
table = parent["table"]
rows = table.loc[table.eligible & table.symbol.isin(symbols)]
calendar = [str(day.date()) for day in parent["calendar"]]


def rounded(value, operation, trace):
    """Round an exact rational operation to binary64, retaining its audit trace."""
    result = float(value)
    trace.append({"operation": operation, "exact": str(value), "binary64": result.hex()})
    return Fraction.from_float(result)


def quote(symbol, index, side):
    key = symbol, index, side
    if key not in quote_cache:
        day = old["calendar"][index]
        raw = old["raws"][symbol]
        price = float(raw.at[day, "open"]) if day in raw.index else 0
        errors = opening_errors(
            {"symbol": symbol, "date": calendar[index], "side": side, "price": price}, old
        )
        quote_cache[key] = None if errors else Fraction(str(price))
    return quote_cache[key]


def outcome(symbol, index, horizon):
    start, target = index + 1, index + 1 + horizon
    if target >= len(calendar):
        return [None, -1, 3, 0], []
    buy = quote(symbol, start, "BUY")
    if buy is None:
        return [None, -1, 2, 0], []
    entry, events = calendar[start], groups[symbol]
    for end in range(target, len(calendar)):
        sell = quote(symbol, end, "SELL")
        if sell is None:
            continue
        exit_date = calendar[end]
        if any(entry < a["ex_date"] <= exit_date and not a.get("record_date") for a in events):
            return [None, -1, 4, end - target], []
        relevant = sorted(
            (
                a
                for a in events
                if a.get("record_date")
                and entry <= a["record_date"] < exit_date
                and a["ex_date"] <= exit_date
            ),
            key=lambda a: a["ex_date"],
        )
        shares, cash = Fraction(1), Fraction(0)
        quantity64, cash64, trace = Fraction(1), Fraction(0), []
        buy64, sell64 = Fraction.from_float(float(buy)), Fraction.from_float(float(sell))
        previous, latest = None, None
        for event in relevant:
            if previous is not None and event["record_date"] < previous:
                return [None, -1, 4, end - target], relevant
            cash += shares * Fraction(str(event["cash_per_share"]))
            cash_term = Fraction.from_float(float(event["cash_per_share"]))
            cash64 = rounded(
                cash64 + rounded(quantity64 * cash_term, "cash entitlement", trace),
                "cash accumulation",
                trace,
            )
            multiplier64 = Fraction.from_float(float(event["share_multiplier"]))
            delta64 = rounded(multiplier64 - 1, "share increment", trace)
            quantity64 = rounded(
                quantity64 + rounded(quantity64 * delta64, "bonus entitlement", trace),
                "share accumulation",
                trace,
            )
            multiplier = Fraction(str(event["share_multiplier"]))
            shares *= multiplier
            if abs(multiplier - 1) > Fraction(1, 10**10):
                if event.get("share_list_date") is None:
                    return [None, -1, 4, end - target], relevant
                latest = max(latest or event["share_list_date"], event["share_list_date"])
            previous = event["ex_date"]
        if latest is not None and exit_date < latest:
            continue
        ideal = float(np.float32(float((shares * sell + cash) / buy - 1)))
        wealth64 = rounded(
            rounded(quantity64 * sell64, "exit stock value", trace) + cash64,
            "exit gross wealth",
            trace,
        )
        return64 = rounded(
            rounded(wealth64 / buy64, "gross return ratio", trace) - 1,
            "net gross-wealth return",
            trace,
        )
        value = float(np.float32(float(return64)))
        if ideal != value:
            rounding_discrepancies.append(
                {
                    "symbol": symbol,
                    "date_index": index,
                    "horizon": horizon,
                    "ideal_decimal": ideal,
                    "rounded_binary64": value,
                    "entry_hex": float(buy64).hex(),
                    "exit_hex": float(sell64).hex(),
                    "events": relevant,
                    "operations": trace,
                }
            )
        return [value, end, 0, end - target], relevant
    return [None, -1, 3, len(calendar) - target], []


prior_file = root / "artifacts/checks/implemented_payments_2026-11-22/check_inputs.py"
prior_tree = ast.parse(prior_file.read_text(encoding="utf-8"))
this_tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
names = {"rounded", "quote", "outcome"}
prior_functions = {
    node.name: ast.dump(node)
    for node in prior_tree.body
    if isinstance(node, ast.FunctionDef) and node.name in names
}
this_functions = {
    node.name: ast.dump(node)
    for node in this_tree.body
    if isinstance(node, ast.FunctionDef) and node.name in names
}
require(
    prior_functions == this_functions and len(this_functions) == 3, "Independent arithmetic changed"
)
expectations, records, all_rounding, mismatches = {}, [], {}, []
for phase, events in (("parent", parent["actions"]), ("corrected", actions)):
    old = dict(parent, actions=events)
    groups = defaultdict(list)
    for action in events:
        groups[action["symbol"]].append(action)
    quote_cache, rounding_discrepancies = {}, []
    values = []
    for row in rows.itertuples():
        for horizon in (1, 3, 5):
            expected, relevant = outcome(row.symbol, row.date_index, horizon)
            identity = row.Index, horizon
            if phase == "parent":
                observed = [
                    getattr(row, name + str(horizon)) for name in ("y", "end", "status", "delay")
                ]
                observed = [None if pd.isna(value) else value for value in observed]
                if observed != expected:
                    mismatches.append(
                        {
                            "row": row.Index,
                            "symbol": row.symbol,
                            "horizon": horizon,
                            "observed": observed,
                            "independent": expected,
                        }
                    )
                expectations[identity] = expected
            else:
                before = expectations[identity]
                values.append(
                    {
                        "row": row.Index,
                        "symbol": row.symbol,
                        "date_index": row.date_index,
                        "horizon": horizon,
                        "before": before,
                        "after": expected,
                    }
                )
                if before != expected:
                    records.append(values[-1])
    all_rounding[phase] = rounding_discrepancies
    print(
        "INDEPENDENT_LABEL_PHASE",
        phase,
        "tuples",
        len(rows) * 3,
        "parent_mismatches",
        len(mismatches),
        "expected_changes",
        len(records),
        flush=True,
    )
    if phase == "corrected":
        dump_gzip(checks / "label_expectations.json.gz", values)
dump_gzip(checks / "label_rounding.json.gz", all_rounding)
save(
    checks / "label_scope.json",
    {
        "status": "EXPECTED_LABELS_FROZEN" if not mismatches else "FAIL_PARENT_RECONCILIATION",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "parent_bundle_sha256": sha(paths.bundle),
        "actions_sha256": digest(actions),
        "derived_from": {
            "path": prior_file.relative_to(root).as_posix(),
            "sha256": sha(prior_file),
        },
        "independent_arithmetic_ast_preserved": True,
        "symbols": symbols,
        "eligible_rows": len(rows),
        "independent_label_tuples_per_input": len(rows) * 3,
        "parent_mismatches": mismatches,
        "expected_changed_tuples": len(records),
        "expected_changes_by_horizon": dict(Counter(str(record["horizon"]) for record in records)),
        "changed_records": records,
        "expectations_sha256": sha(checks / "label_expectations.json.gz"),
        "ideal_decimal_discrepancies_by_input": {
            key: len(value) for key, value in all_rounding.items()
        },
        "rounding_evidence_sha256": sha(checks / "label_rounding.json.gz"),
        "training_started": False,
        "scope": "Every eligible 1/3/5-day tuple for all scoped securities, with independently checked opening proxies, rational wealth and separately rounded original binary64 operations. Full old-tuple reconciliation is required; new-snapshot equality and feature/rank acceptance remain separate gates. No production legal_label, gross_entitlement or snapshot builder is called.",
    },
    immutable=True,
)
require(not mismatches, "Independent old wealth labels disagree")
print("INDEPENDENT_LABEL_EXPECTATIONS_FROZEN", len(records), flush=True)
