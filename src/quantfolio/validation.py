"""Independent account reconciliation and execution checks."""

from __future__ import annotations

import collections
import datetime as dt
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

import pandas as pd

from .risk import CAPS, LEVELS


def money(value):
    """Convert currency independently to cents for reconciliation."""
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def reconcile(detail, summary, meta, actions):
    """Reconstruct cash, shares, distributions and marked equity independently of the simulator."""
    cash = 100_000_000
    receivables = 0
    lots = collections.defaultdict(list)
    rights = {}
    fees = 0
    previous = 100_000_000
    events = collections.defaultdict(list)
    trades = collections.defaultdict(list)
    byaction = {(a["symbol"], a["ex_date"]): a for a in actions}
    checks = 0
    errors = []

    def check(condition, context):
        nonlocal checks
        checks += 1
        if not condition:
            errors.append(context)

    for x in detail["events"]:
        events[x["date"]].append(x)
    for x in detail["trades"]:
        trades[x["date"]].append(x)
    for row in detail["curve"]:
        day = row["date"]
        check(money(row["prior_close_nav"]) == previous, day + " prior NAV")
        for event in events[day]:
            s = event["symbol"]
            if event["type"] == "CASH_DIVIDEND_PAID":
                amount = money(event["amount"])
                cash += amount
                receivables -= amount
                continue
            action = byaction[(s, day)]
            owned = rights.get((s, action.get("record_date")), [])
            qty = sum(x["quantity"] for x in owned)
            check(event["entitled_quantity"] == qty, day + " record-date entitlement " + s)
            gross = money(qty * action["cash_per_share"])
            reserve = money(gross / 100 * 0.2) if meta[s]["kind"] == "stock" else 0
            check(money(event["cash_gross"]) == gross, day + " gross cash distribution")
            check(money(event["cash_tax_reserve"]) == reserve, day + " dividend tax reserve")
            check(money(event["net_receivable"]) == gross - reserve, day + " net receivable")
            receivables += gross - reserve
            multiplier = Decimal(str(action["share_multiplier"]))
            if multiplier >= 1:
                extra = int(
                    (Decimal(qty) * (multiplier - 1)).to_integral_value(rounding=ROUND_FLOOR)
                )
                check(event["share_quantity_change"] == extra, day + " bonus aggregate quantity")
                if extra:
                    lots[s].append({"quantity": extra, "available": action.get("share_list_date")})
            else:
                check(
                    sum(x["quantity"] for x in lots[s]) == qty,
                    day + " reverse split existing quantity",
                )
                exact = [Decimal(x["quantity"]) * multiplier for x in lots[s]]
                quantities = [int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in exact]
                rounding = (
                    ROUND_CEILING if action.get("fractional_rounding") == "CEILING" else ROUND_FLOOR
                )
                total = int(sum(exact).to_integral_value(rounding=rounding))
                for j in sorted(
                    range(len(exact)), key=lambda j: exact[j] - quantities[j], reverse=True
                )[: total - sum(quantities)]:
                    quantities[j] += 1
                check(
                    event["share_quantity_change"] == total - qty,
                    day + " reverse split aggregate quantity",
                )
                for j, lot in enumerate(lots[s]):
                    lot["quantity"] = quantities[j]
                    listing = action.get("share_list_date")
                    lot["available"] = (
                        max(lot["available"], listing) if lot["available"] and listing else None
                    )
        for trade in trades[day]:
            s = trade["symbol"]
            m = meta[s]
            q = trade["quantity"]
            gross = money(q * trade["price"])
            check(isinstance(q, int) and q > 0, day + " integer shares")
            check(trade["signal_date"] < day, day + " signal timing")
            check(gross == money(trade["gross"]), day + " gross trade value")
            tick = Decimal(".001" if m["kind"] == "etf" else ".01")
            check(Decimal(str(trade["price"])) % tick == 0, day + " price tick")
            fee = money(max(5.0, gross / 100 * 0.0003))
            if m["kind"] == "stock":
                fee += money(gross / 100 * 0.00001) + (
                    money(gross / 100 * 0.0005) if trade["side"] == "SELL" else 0
                )
            check(fee == money(trade["fee"]), day + " fees")
            fees += fee
            if trade["side"] == "BUY":
                minimum = (
                    200 if m["kind"] == "stock" and m["code"].startswith(("688", "689")) else 100
                )
                step = (
                    1
                    if m["kind"] == "stock"
                    and m["code"].startswith(("688", "689", "920", "8", "4"))
                    else 100
                )
                check(q >= minimum and q % step == 0, day + " lot rule")
                cash -= gross + fee
                lots[s].append(
                    {
                        "quantity": q,
                        "available": (
                            dt.date.fromisoformat(day) + dt.timedelta(days=1)
                        ).isoformat(),
                    }
                )
            else:
                available = sum(
                    x["quantity"] for x in lots[s] if x["available"] and x["available"] <= day
                )
                check(q <= available, day + " available shares/T+1")
                remaining = q
                for lot in lots[s]:
                    if lot["available"] and lot["available"] <= day:
                        used = min(lot["quantity"], remaining)
                        lot["quantity"] -= used
                        remaining -= used
                cash += gross - fee
            check(cash == money(trade["cash_after"]) and cash >= 0, day + " cash after fill")
        value = 0
        for s, p in row["positions"].items():
            q = sum(x["quantity"] for x in lots[s])
            avail = sum(x["quantity"] for x in lots[s] if x["available"] and x["available"] <= day)
            check(q == p["quantity"] and avail == p["available"], day + " position " + s)
            v = money(q * p["close"])
            check(v == money(p["value"]), day + " position value")
            value += v
        check(
            {s for s in lots if sum(x["quantity"] for x in lots[s]) > 0} == set(row["positions"]),
            day + " position coverage",
        )
        check(
            cash == money(row["cash"]) and receivables == money(row["receivables"]),
            day + " closing cash/receivables",
        )
        check(value == money(row["holdings_value"]), day + " holdings value")
        check(cash + value + receivables == money(row["nav"]), day + " NAV identity")
        for s in lots:
            rights[(s, day)] = [dict(x) for x in lots[s] if x["quantity"] > 0]
        previous = money(row["nav"])
    check(fees == money(summary["fees"]), "fee total")
    check(abs(summary["return"] - (previous / 100_000_000 - 1)) < 1e-12, "terminal return")
    return {"status": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors}


def execution_audit(ledger, bundle):
    """Independently inspect preceding-close signals, high-water marks and post-fill limits."""
    errors = []
    checks = 1
    if [row["date"] for row in ledger["curve"]] != [
        decision["execution_date"] for decision in ledger["decisions"]
    ]:
        return {"status": "FAIL", "checks": checks, "errors": [["DECISION_DATES_MISMATCH"]]}
    peak = 1e6
    marks = {(x["date"], x["after_fill"]): x for x in ledger["intermediate_weights"]}
    decisions = {decision["execution_date"]: decision for decision in ledger["decisions"]}
    previous_phase = "NORMAL"
    for row, decision in zip(ledger["curve"], ledger["decisions"]):
        state = decision["recovery"]
        if state["phase"] not in CAPS:
            return {
                "status": "FAIL",
                "checks": checks,
                "errors": [["UNKNOWN_RISK_PHASE", row["date"]]],
            }
        peak = max(peak, row["prior_close_nav"])
        checks += 6
        if abs(state["peak"] - peak) > 0.0001:
            errors.append(["PEAK_RESET", row["date"]])
        if abs(state["drawdown"] - (1 - row["prior_close_nav"] / peak)) > 1e-10:
            errors.append(["DRAWDOWN_MISMATCH", row["date"]])
        if state["previous_phase"] != previous_phase:
            errors.append(["STATE_DISCONTINUITY", row["date"]])
        if previous_phase == "HALTED0" and state["phase"] != "HALTED0":
            errors.append(["RESTORED_AFTER_8", row["date"]])
        if state["drawdown"] >= 0.08 - 1e-12 and state["phase"] != "HALTED0":
            errors.append(["8_ALERT_MISSED", row["date"]])
        if row["risk_cap"] > CAPS[state["phase"]] + 1e-9:
            errors.append(["RISK_CAP_MISMATCH", row["date"]])
        checks += 1
        if LEVELS[state["phase"]] < LEVELS[previous_phase]:
            errors.append(["RISK_LEVEL_RESTORED", row["date"]])
        previous_phase = state["phase"]
    for i, trade in enumerate(ledger["trades"]):
        checks += 3
        date = pd.Timestamp(trade["date"])
        prior = bundle["calendar"][bundle["calendar"].get_loc(date) - 1]
        if trade["signal_date"] != str(prior.date()):
            errors.append(["NONPRIOR_SIGNAL", trade])
        if (
            trade["gross"]
            > float(bundle["panels"]["adv20"].at[prior, trade["symbol"]]) * 0.01 + 0.02
        ):
            errors.append(["ADV_LIMIT", trade])
        if trade["cash_after"] < 0:
            errors.append(["NEGATIVE_CASH", trade])
        if trade["side"] == "BUY":
            checks += 4
            weights = marks[trade["date"], i + 1]
            if not decisions[trade["date"]]["recovery"]["can_buy"]:
                errors.append(["BUY_WHILE_FROZEN", trade])
            if weights["max_nav_weight"] > 0.15 + 1e-7:
                errors.append(["POSTBUY_SINGLE_CAP", trade])
            if weights["max_industry_weight"] > 0.30 + 1e-7:
                errors.append(["POSTBUY_SECTOR_CAP", trade])
            if weights["gross_exposure"] > weights["risk_cap"] + 1e-7:
                errors.append(["POSTBUY_TOTAL_CAP", trade])
    return {"status": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors}
