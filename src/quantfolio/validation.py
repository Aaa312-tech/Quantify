"""Independent account reconciliation and execution checks."""

from __future__ import annotations

import collections
import datetime as dt
import math
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

import pandas as pd

from .risk import CAPS, LEVELS


def money(value):
    """Convert currency independently to cents for reconciliation."""
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def opening_errors(trade, bundle):
    """Check the proxy independently; deliberately do not call the quote generator."""
    symbol = trade["symbol"]
    date = pd.Timestamp(trade["date"])
    previous = bundle["calendar"][bundle["calendar"].get_loc(date) - 1]
    meta = bundle["meta"][symbol]
    raw = bundle["raws"][symbol]
    if date not in raw.index or previous not in raw.index:
        return ["MISSING_OPENING_QUOTE"]
    opening = float(raw.at[date, "open"])
    reference = float(raw.at[previous, "close"])
    if not math.isfinite(opening) or opening <= 0 or not math.isfinite(reference) or reference <= 0:
        return ["INVALID_OPENING_REFERENCE"]
    failures = []
    if trade["price"] != opening:
        failures.append("NOT_OPENING_PROXY")
    status = bundle.get("opening_status", {}).get(trade["date"], {}).get(symbol)
    if status is not None:
        known = pd.Timestamp(status.get("known_at"))
        deadline = date.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
        if pd.isna(known) or known.tzinfo is None or known > deadline:
            failures.append("STATUS_NOT_AVAILABLE_AT_OPEN")
        if status.get("suspended") is not False:
            failures.append("SUSPENDED_OR_INVALID_STATUS")
    for action in bundle["actions"]:
        if action["symbol"] == symbol and action["ex_date"] == trade["date"]:
            reference = (
                reference - action.get("ex_reference_cash_per_share", action["cash_per_share"])
            ) / action.get("ex_reference_share_multiplier", action["share_multiplier"])
            if action.get("price_limit_reference") is not None:
                reference = action["price_limit_reference"]
    tick = Decimal(".001" if meta["kind"] == "etf" else ".01")
    if meta["kind"] == "etf":
        limit = meta.get("observed_limit", 0.20 if meta["code"] in ["159915", "588000"] else 0.10)
    elif symbol.startswith("bj"):
        limit = 0.30
    elif meta["code"].startswith(("300", "301", "688", "689")):
        limit = 0.20
    else:
        limit = 0.10
    upper = (Decimal(str(reference * (1 + limit))) / tick).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ) * tick
    lower = (Decimal(str(reference * (1 - limit))) / tick).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ) * tick
    price = Decimal(str(opening))
    if price % tick != 0 or price < lower or price > upper:
        failures.append("INVALID_PROXY_TICK_OR_LIMIT")
    if (trade["side"] == "BUY" and price >= upper) or (trade["side"] == "SELL" and price <= lower):
        failures.append("DIRECTIONAL_LIMIT_FILL")
    return failures


def reconcile(detail, summary, meta, actions):
    """Reconstruct cash, shares, distributions and marked equity independently of the simulator."""
    cash = 100_000_000
    receivables = 0
    outstanding = {}
    lots = collections.defaultdict(list)
    rights = {}
    fees = 0
    slippage = 0
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
                matches = [
                    key
                    for key, claim in outstanding.items()
                    if claim["symbol"] == s
                    and claim["cents"] == amount
                    and (not event.get("event_id") or key == event["event_id"])
                ]
                check(len(matches) == 1, day + " unique unpaid cash entitlement " + s)
                if len(matches) == 1:
                    claim = outstanding.pop(matches[0])
                    check(
                        bool(claim["available"] and claim["available"] <= day),
                        day + " cash availability " + s,
                    )
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
            if gross != reserve:
                outstanding[action["id"]] = {
                    "symbol": s,
                    "cents": gross - reserve,
                    "available": action.get("cash_available_date", action.get("cash_pay_date")),
                }
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
            impact = money(gross / 100 * trade.get("slippage_rate", 0.0))
            check(impact == money(trade.get("slippage_cost", 0.0)), day + " slippage cost")
            check(impact >= 0, day + " nonnegative slippage")
            slippage += impact
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
                cash -= gross + fee + impact
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
                cash += gross - fee - impact
            check(cash == money(trade["cash_after"]) and cash >= 0, day + " cash after fill")
        check(
            not any(
                claim["available"] and claim["available"] <= day for claim in outstanding.values()
            ),
            day + " due cash paid",
        )
        check(
            sum(claim["cents"] for claim in outstanding.values()) == receivables,
            day + " outstanding entitlements",
        )
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
    check(slippage == money(summary.get("slippage_cost", 0.0)), "slippage total")
    if "total_cost" in summary:
        check(fees + slippage == money(summary["total_cost"]), "total execution costs")
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
    execution = ledger.get("execution")
    if execution is None or (
        execution.get("model") != "opening_proxy_separate_cost"
        or not math.isfinite(execution.get("slippage_rate", float("nan")))
        or execution["slippage_rate"] < 0
    ):
        return {"status": "FAIL", "checks": checks, "errors": [["INVALID_EXECUTION_MODEL"]]}
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
        if execution is not None:
            checks += 3
            errors.extend([reason, trade] for reason in opening_errors(trade, bundle))
            if trade.get("slippage_rate") != execution["slippage_rate"]:
                errors.append(["SLIPPAGE_RATE_MISMATCH", trade])
            expected_cost = money(trade["gross"] * execution["slippage_rate"])
            if money(trade.get("slippage_cost", -1)) != expected_cost:
                errors.append(["SLIPPAGE_COST_MISMATCH", trade])
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
