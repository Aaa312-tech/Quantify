"""Reconstruct restored labels with rational wealth and an independent opening audit."""

from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import numpy as np

from quantfolio.dataset import load_snapshot
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha
from quantfolio.validation import opening_errors

root = Path.cwd()
pfile = root / "configs/protocols/distribution_dates.json"
p = read(pfile)
checks = root / p["checks_output"]
old = load_snapshot(Paths(root), root / p["source_bundle"], p["source_data_cutoff"])
diagnostic = read(checks / "label_diagnostic.json")
facts_file = root / p["snapshot_output"] / "fact_bindings.json"
facts = read(facts_file)
facts_by_key = {(row["symbol"], row["ex_date"]): row for row in facts}
calendar = [str(day.date()) for day in old["calendar"]]
actions = deepcopy(old["actions"])
for action in actions:
    fact = facts_by_key.get((action["symbol"], action["ex_date"]))
    if fact is None:
        continue
    assert digest(action) == fact["original_action_sha256"]
    fields = fact["verified_fields"]
    assert set(fields) <= {"cash_pay_date", "share_list_date"}
    assert all(action.get(field) is None for field in fields)
    action.update(fields)
    if "cash_pay_date" in fields:
        following = [day for day in calendar if day > fields["cash_pay_date"]]
        action["cash_available_date"] = following[0] if following else None
    action.setdefault("distribution_date_reviews", []).append(
        {
            "method": fact["method"],
            "fields": sorted(fields),
            "source": fact["source"],
            "pages": fact["evidence_pages"],
            "known_at": fact["known_at"],
            "date_basis": fact["date_basis"],
        }
    )
groups = {"before": defaultdict(list), "after": defaultdict(list)}
for arm, values in (("before", old["actions"]), ("after", actions)):
    for value in values:
        groups[arm][value["symbol"]].append(value)
quote_cache = {}


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


def outcome(row, arm):
    symbol, entry_index = row["symbol"], row["date_index"] + 1
    target = entry_index + row["horizon"]
    entry, buy = calendar[entry_index], quote(symbol, entry_index, "BUY")
    assert buy is not None
    events = groups[arm][symbol]
    for end in range(target, len(calendar)):
        sell = quote(symbol, end, "SELL")
        if sell is None:
            continue
        exit_date = calendar[end]
        assert not any(
            entry < a["ex_date"] <= exit_date and not a.get("record_date") for a in events
        )
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
        latest = previous_ex = None
        used = []
        for event in relevant:
            assert previous_ex is None or event["record_date"] >= previous_ex
            cash += shares * Fraction(str(event["cash_per_share"]))
            multiplier = Fraction(str(event["share_multiplier"]))
            shares *= multiplier
            previous_ex = event["ex_date"]
            if multiplier != 1:
                if event.get("share_list_date") is None:
                    assert (symbol, event["ex_date"]) in facts_by_key
                    assert (
                        "share_list_date"
                        in facts_by_key[symbol, event["ex_date"]]["verified_fields"]
                    )
                    return [None, -1, 4, end - target], {"missing_listing_event": event["ex_date"]}
                latest = max(latest or event["share_list_date"], event["share_list_date"])
                used.append(event["ex_date"])
        if latest is not None and exit_date < latest:
            continue
        wealth = shares * sell + cash
        value = float(np.float32(float(wealth / buy - 1)))
        return [value, end, 0, end - target], {
            "entry_date": entry,
            "exit_date": exit_date,
            "entry_price": str(buy),
            "exit_price": str(sell),
            "base_share_quantity": str(shares),
            "gross_cash": str(cash),
            "gross_wealth": str(wealth),
            "listing_events": used,
            "latest_listing": latest,
        }
    raise AssertionError("Independent outcome unexpectedly right-censored")


records = []
for row in diagnostic["records"]:
    stored = old["table"].iloc[row["row"]]
    assert (
        stored.symbol == row["symbol"]
        and stored.date_index == row["date_index"]
        and stored.eligible
    )
    before, old_detail = outcome(row, "before")
    after, detail = outcome(row, "after")
    assert before == row["before"] and after == row["after"], row
    assert detail["listing_events"]
    records.append(
        {
            **row,
            "independent_before": before,
            "independent_after": after,
            "before_basis": old_detail,
            "after_basis": detail,
        }
    )
assert len({(r["row"], r["horizon"]) for r in records}) == len(records) == 106
save(
    checks / "label_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "protocol_sha256": sha(pfile),
        "data_sha256": sha(root / p["source_bundle"]),
        "actions_sha256": digest(actions),
        "diagnostic_sha256": sha(checks / "label_diagnostic.json"),
        "fact_bindings_sha256": sha(facts_file),
        "independent_reader_sha256": sha(Path(__file__)),
        "independent_opening_code_sha256": sha(root / "src/quantfolio/validation.py"),
        "changed_labels": len(records),
        "changed_by_horizon": dict(Counter(str(r["horizon"]) for r in records)),
        "records": records,
        "training_started": False,
        "scope": "All 106 restored labels independently match exact rational gross wealth cast to the stored float32 boundary, original censoring and earliest legally sellable opening. Neither legal_label nor gross_entitlement is called. All other table cells must remain exact during the full reconstruction.",
    },
    immutable=True,
)
print("INDEPENDENT LABEL REVIEW PASSED", len(records), flush=True)
