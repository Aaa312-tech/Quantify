"""Bind dates independently and audit gross-wealth labels before rebuilding or fitting."""

import ast
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.dataset import load_snapshot
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha
from quantfolio.validation import opening_errors

root = Path.cwd()
pfile = root / "configs/protocols/declared_payments.json"
p = read(pfile)
paths = Paths(root)
checks = root / p["checks_output"]
assert not (root / p["training_output"]).exists()
assert sha(paths.configuration_path) == p["active_configuration_sha256"]
manifest = read(paths.manifest)
references = [
    p[key] for key in ("review_protocol", "review_basis", "source_acceptance", "independent_review")
]
references += list(p["source_closure"].values())
for reference in references:
    assert (
        manifest["files"][reference["path"]] == sha(root / reference["path"]) == reference["sha256"]
    )
acceptance = read(root / p["source_acceptance"]["path"])
independent = read(root / p["independent_review"]["path"])
review = read(root / p["review"])
assert review["extractor_sha256"] == sha(root / "src/quantfolio/distributions.py")
assert review["protocol_sha256"] == p["review_protocol"]["sha256"]
assert review["data_snapshot_sha256"] == p["review_basis"]["sha256"]
assert sha(root / p["review"]) == independent["source_review_sha256"] == p["review_sha256"]
assert acceptance["status"] == "PASS_SOURCE_ONLY" and independent["status"] == "PASS"
assert acceptance["facts_applied"] is False
assert independent["events"] == len(independent["records"]) == 28
assert independent["failed_events"] == 0
assert acceptance["independent_field_checks"] == independent["field_checks"] == 112
assert acceptance["evidence_sha256"]["field_acceptance.json"] == p["independent_review"]["sha256"]
preserved = independent["preserved_fields"]
assert len(preserved) == acceptance["prior_supported_fields_preserved"] == 1386
assert len({tuple(row[:3]) for row in preserved}) == len(preserved)
assert (
    len({tuple(row[:2]) for row in preserved})
    == acceptance["prior_supported_events_preserved"]
    == 1359
)
assert set(p["source_closure"]) == {"amendment_review.json", "visual_review.json"}
assert acceptance["active_state"]["bundle_sha256"] == p["source_bundle_sha256"]
for name, reference in p["source_closure"].items():
    assert acceptance["evidence_sha256"][name] == reference["sha256"]
    assert read(root / reference["path"])["status"] == "PASS"
old = load_snapshot(paths, root / p["source_bundle"], p["source_data_cutoff"])
basis = load_snapshot(paths, root / p["review_basis"]["path"], p["source_data_cutoff"])
assert digest(basis["actions"]) == review["original_actions_sha256"]
old_index = {(a["symbol"], a["ex_date"]): a for a in old["actions"]}
basis_index = {(a["symbol"], a["ex_date"]): a for a in basis["actions"]}
review_index = {(a["symbol"], a["ex_date"]): a for a in review["records"]}
facts = {(a["symbol"], a["ex_date"]): a for a in review["facts"]}
assert len(old_index) == len(basis_index) == len(review_index) == 1839
assert old_index.keys() == basis_index.keys() == review_index.keys()
for key, action in basis_index.items():
    assert review_index[key]["original_action_sha256"] == digest(action)

calendar = [str(day.date()) for day in old["calendar"]]
actions = deepcopy(old["actions"])
new_index = {(a["symbol"], a["ex_date"]): a for a in actions}
economic = (
    "symbol",
    "code",
    "id",
    "record_date",
    "ex_date",
    "cash_per_share",
    "share_multiplier",
    "bonus_per_share",
    "capitalization_per_share",
    "ex_reference_cash_per_share",
    "ex_reference_share_multiplier",
    "cash_rounding",
)
records, seen, affected = [], set(), set()
for row in independent["records"]:
    key = row["symbol"], row["ex_date"]
    assert key not in seen and row["passed"] and all(c["passed"] for c in row["checks"].values())
    seen.add(key)
    assert set(row["checks"]) == {
        "issuer",
        "title",
        "record_date",
        "ex_date",
        "cash_pay_date",
        "cash_per_share",
    }
    assert set(row["added_fields"]) == {"cash_pay_date"}
    fact, before, original, after = facts[key], old_index[key], basis_index[key], new_index[key]
    assert row["source"] == fact["source"] and fact["status"] == "VERIFIED_FIELDS"
    assert all(before.get(field) == original.get(field) for field in economic)
    source = fact["source"]
    assert sha(root / source["path"]) == source["sha256"]
    assert sha(root / source["receipt"]) == source["receipt_sha256"]
    value = row["added_fields"]["cash_pay_date"]
    assert value == fact["verified_fields"]["cash_pay_date"]
    assert str(pd.Timestamp(value).date()) == value and value >= before["ex_date"]
    known = before.get("cash_pay_date")
    assert known is None or known == value
    records.append({"symbol": key[0], "ex_date": key[1], "date": value, "missing": known is None})
    if known is not None:
        continue
    affected.add(key)
    after["cash_pay_date"] = value
    following = [day for day in calendar if day > value]
    after["cash_available_date"] = following[0] if following else None
    after.setdefault("distribution_date_reviews", []).append(
        {
            "method": fact["method"],
            "fields": ["cash_pay_date"],
            "source": source,
            "pages": fact["evidence_pages"],
            "known_at": fact["known_at"],
            "date_basis": {
                "review_sha256": p["review_sha256"],
                "review_basis_sha256": p["review_basis"]["sha256"],
                "source_acceptance_sha256": p["source_acceptance"]["sha256"],
                "independent_review_sha256": p["independent_review"]["sha256"],
                "reviewed_action_sha256": digest(original),
                "current_action_sha256": digest(before),
            },
        }
    )
assert len(records) == 28 and len(affected) == 25
allowed = {"cash_pay_date", "cash_available_date", "distribution_date_reviews"}
for before, after in zip(old["actions"], actions, strict=True):
    assert {k: v for k, v in before.items() if k not in allowed} == {
        k: v for k, v in after.items() if k not in allowed
    }
for symbol, ex_date, field, value in preserved:
    assert facts[symbol, ex_date]["verified_fields"][field] == value
    assert old_index[symbol, ex_date][field] == new_index[symbol, ex_date][field]
market_tree = ast.parse((root / "src/quantfolio/market.py").read_text(encoding="utf-8"))
functions = {n.name: n for n in market_tree.body if isinstance(n, ast.FunctionDef)}
# Walk every module-local dependency, including the imported tick function.
ledger_tree = ast.parse((root / "src/quantfolio/ledger.py").read_text(encoding="utf-8"))
functions.update({n.name: n for n in ledger_tree.body if isinstance(n, ast.FunctionDef)})
dependencies, pending = [], ["legal_label"]
while pending:
    name = pending.pop()
    if name in dependencies:
        continue
    dependencies.append(name)
    function = functions[name]
    literals = {
        n.value
        for n in ast.walk(function)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert not allowed & literals
    pending.extend(
        n.func.id
        for n in ast.walk(function)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in functions
    )
dependencies.sort()
groups = defaultdict(list)
for action in old["actions"]:
    groups[action["symbol"]].append(action)
quote_cache = {}
rounding_discrepancies = []


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


symbols = {key[0] for key in affected}
counts, mismatches, event_records = Counter(), [], []
table = old["table"]
for row in table[table.eligible & table.symbol.isin(symbols)].itertuples():
    for horizon in (1, 3, 5):
        observed = [getattr(row, field + str(horizon)) for field in ("y", "end", "status", "delay")]
        observed = [None if pd.isna(v) else v for v in observed]
        expected, events = outcome(row.symbol, row.date_index, horizon)
        counts[str(horizon)] += 1
        if observed != expected:
            mismatches.append(
                {
                    "row": row.Index,
                    "symbol": row.symbol,
                    "horizon": horizon,
                    "stored": observed,
                    "independent": expected,
                }
            )
        affected_events = [a["ex_date"] for a in events if (row.symbol, a["ex_date"]) in affected]
        if affected_events:
            event_records.append(
                {
                    "row": row.Index,
                    "symbol": row.symbol,
                    "horizon": horizon,
                    "events": affected_events,
                    "independent": expected,
                }
            )
result = {
    "status": "PASS" if not mismatches else "FAIL",
    "checked_at": now(),
    "protocol_sha256": sha(pfile),
    "checker_sha256": sha(Path(__file__)),
    "prior_failure_sha256": sha(checks / "input_failure.json"),
    "prior_checker_sha256": sha(checks / "check_inputs.py"),
    "prior_log_sha256": sha(checks / "input_scope.log"),
    "preserved_source_fields_exact": len(preserved),
    "preserved_runtime_fields_exact": len(preserved),
    "source_sha256": {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))},
    "data_sha256": sha(root / p["source_bundle"]),
    "actions_sha256": digest(actions),
    "added_facts": 28,
    "changed_payments": 25,
    "already_matching": 3,
    "label_change_expectation": 0,
    "records": records,
    "label_symbols": sorted(symbols),
    "independent_label_checks": dict(counts),
    "mismatches": mismatches,
    "ideal_decimal_discrepancies": rounding_discrepancies,
    "affected_event_label_records": event_records,
    "label_dependencies_reviewed": dependencies,
    "training_started": False,
    "scope": "All eligible 1/3/5-day label tuples for the affected securities are checked with independent opening validation, rational gross wealth and exact per-operation binary64 rounding. Ideal-decimal discrepancies are retained; stored float32 values must match without tolerance. Neither production legal_label nor gross_entitlement is called. Cash payment timing and appended provenance are absent from their transitive action dependencies. Full reconstruction must additionally preserve every table cell and every other input.",
}
save(checks / "expected_actions.json", actions, immutable=True)
save(checks / "input_scope.json", result, immutable=True)
assert not mismatches, (len(mismatches), mismatches[:3])
print("PAYMENT AND LABEL SCOPE PASSED", len(symbols), dict(counts), len(event_records), flush=True)
