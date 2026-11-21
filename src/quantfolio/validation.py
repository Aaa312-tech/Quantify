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


def checked_industries(meta, as_of):
    """Reconstruct groups independently of the production schedule lookup."""
    deadline = pd.Timestamp(as_of)
    if pd.isna(deadline) or deadline.tzinfo is None:
        raise ValueError("Industry audit requires an explicit information time")
    groups = {}
    for symbol, instrument in meta.items():
        if "industry_history" not in instrument:
            groups[symbol] = instrument["industry"]
            continue
        if instrument["kind"] != "stock" or not instrument["industry_history"]:
            raise ValueError("Invalid company industry history")
        group, previous = None, None
        for record in instrument["industry_history"]:
            published = pd.Timestamp(record["available_at"])
            if (
                pd.isna(published)
                or published.tzinfo is None
                or (previous is not None and published <= previous)
            ):
                raise ValueError("Invalid industry availability sequence")
            value = record["industry"]
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("Invalid industry key")
            if published <= deadline:
                group = value
            previous = published
        groups[symbol] = group
    return groups


def dated_industry_audit(ledger, bundle):
    """Rebuild post-fill groups and values from reconciled quantities and opening bars."""
    errors, checks = [], 0
    previous_positions = {}
    last_close = {}
    trades, events = collections.defaultdict(list), collections.defaultdict(list)
    for index, trade in enumerate(ledger["trades"], 1):
        trades[trade["date"]].append((index, trade))
    for event in ledger["events"]:
        if event["type"] == "CORPORATE_ACTION":
            events[event["date"]].append(event)
    marks = {(row["date"], row["after_fill"]): row for row in ledger["intermediate_weights"]}
    for closing in ledger["curve"]:
        day = closing["date"]
        quantities = {s: row["quantity"] for s, row in previous_positions.items()}
        for event in events[day]:
            symbol = event["symbol"]
            quantities[symbol] = quantities.get(symbol, 0) + event["share_quantity_change"]
        for action in bundle["actions"]:
            symbol = action["symbol"]
            if action["ex_date"] == day and symbol in last_close:
                last_close[symbol] = (last_close[symbol] - action["cash_per_share"]) / action[
                    "share_multiplier"
                ]
        relevant = set(quantities) | {trade["symbol"] for _, trade in trades[day]}
        groups = checked_industries(
            {s: bundle["meta"][s] for s in relevant}, day + "T09:25:00+08:00"
        )
        for symbol, quantity in quantities.items():
            checks += 1
            if quantity and groups[symbol] is None:
                errors.append(["UNKNOWN_HELD_INDUSTRY", day, symbol])
        prices = {}
        for symbol in relevant:
            raw = bundle["raws"][symbol]
            quote = raw.at[pd.Timestamp(day), "open"] if pd.Timestamp(day) in raw.index else None
            prices[symbol] = (
                float(quote)
                if quote is not None and math.isfinite(quote)
                else last_close.get(symbol)
            )
        for index, trade in trades[day]:
            symbol = trade["symbol"]
            change = trade["quantity"] * (1 if trade["side"] == "BUY" else -1)
            quantities[symbol] = quantities.get(symbol, 0) + change
            prices[symbol] = trade["price"]
            values = {s: money(q * prices[s]) / 100 for s, q in quantities.items() if q}
            sectors = collections.defaultdict(float)
            for name, value in values.items():
                sectors[groups[name]] += value
            nav = trade["cash_after"] + sum(values.values()) + closing["receivables"]
            largest = max(sectors.values(), default=0) / nav
            checks += 1
            if abs(marks[day, index]["max_industry_weight"] - largest) > 1e-10:
                errors.append(["DATED_INDUSTRY_WEIGHT_MISMATCH", day, index])
            if trade["side"] == "BUY":
                checks += 2
                if any(groups[name] is None for name in values):
                    errors.append(["BUY_WITH_UNKNOWN_INDUSTRY", day, index])
                if largest > 0.30 + 1e-7:
                    errors.append(["DATED_INDUSTRY_CAP", day, index])
        previous_positions = closing["positions"]
        last_close.update({s: row["close"] for s, row in previous_positions.items()})
    return {"checks": checks, "errors": errors}


def opening_errors(trade, bundle):
    """Check the proxy independently; deliberately do not call the quote generator."""
    symbol = trade["symbol"]
    date = pd.Timestamp(trade["date"])
    previous = bundle["calendar"][bundle["calendar"].get_loc(date) - 1]
    meta = bundle["meta"][symbol]
    raw = bundle["raws"][symbol]
    status = bundle.get("opening_status", {}).get(trade["date"], {}).get(symbol)
    if date not in raw.index:
        return ["MISSING_OPENING_QUOTE"]
    if previous not in raw.index:
        # Independently reconstruct the full suspension gap; do not reuse market.py.
        try:
            anchor = pd.Timestamp(status["reference_date"])
            observations = raw.loc[raw.index < date]
            gap = bundle["calendar"][(bundle["calendar"] > anchor) & (bundle["calendar"] < date)]
            evidence = status["suspension_evidence"]
            if (
                trade["side"] != "SELL"
                or observations.empty
                or anchor != observations.index[-1]
                or not len(gap)
                or gap[-1] != previous
                or [e["date"] for e in evidence] != [str(d.date()) for d in gap]
                or any(
                    a["symbol"] == symbol and str(anchor.date()) < a["ex_date"] < trade["date"]
                    for a in bundle["actions"]
                )
            ):
                return ["UNVERIFIED_RESUMPTION_REFERENCE"]
            for item, day in zip(evidence, gap):
                known = pd.Timestamp(item["known_at"])
                deadline = day.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
                if pd.isna(known) or known.tzinfo is None or known > deadline:
                    return ["UNVERIFIED_RESUMPTION_REFERENCE"]
            previous = anchor
        except (KeyError, TypeError, ValueError):
            return ["MISSING_OPENING_QUOTE"]
    opening = float(raw.at[date, "open"])
    reference = float(raw.at[previous, "close"])
    if not math.isfinite(opening) or opening <= 0 or not math.isfinite(reference) or reference <= 0:
        return ["INVALID_OPENING_REFERENCE"]
    failures = []
    if trade["price"] != opening:
        failures.append("NOT_OPENING_PROXY")
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
    deadline = date.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
    for period in meta.get("risk_warning_periods", ()):
        start_known = pd.Timestamp(period["start_available_at"])
        end_known = pd.Timestamp(period["end_available_at"])
        if start_known.tzinfo is None or end_known.tzinfo is None:
            failures.append("INVALID_RISK_WARNING_AVAILABILITY")
            continue
        if period["start"] <= trade["date"] and start_known <= deadline:
            if trade["date"] < period["end"] or end_known > deadline:
                limit = period["limit"]
                if trade["side"] == "BUY":
                    failures.append("BUY_DURING_RISK_WARNING")
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
            if action.get("cash_rounding") == "FLOOR_TO_CENT":
                cash_claim = Decimal(str(action["cash_per_share"])) * Decimal(qty)
                gross = int((cash_claim * 100).quantize(Decimal("1"), rounding=ROUND_FLOOR))
            else:
                check(action.get("cash_rounding") is None, day + " supported cash rounding")
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
    state_age, healthy_streak, probe_anchor = 0, 0, None
    execution = ledger.get("execution")
    if execution is None or (
        execution.get("model") != "opening_proxy_separate_cost"
        or not math.isfinite(execution.get("slippage_rate", float("nan")))
        or execution["slippage_rate"] < 0
    ):
        return {"status": "FAIL", "checks": checks, "errors": [["INVALID_EXECUTION_MODEL"]]}
    mode = execution.get("risk_mode", "latched")
    if mode not in ("latched", "staged_restore", "probe_restore"):
        return {"status": "FAIL", "checks": checks, "errors": [["INVALID_RISK_MODE"]]}
    panels = bundle["panels"]
    eligible = panels["eligible"]
    breadth = ((panels["trend20"] > 0) & eligible).sum(axis=1) / eligible.sum(axis=1).clip(lower=1)
    benchmark = panels["close"]["sh510300"].dropna()
    rising = (benchmark / benchmark.shift(5) > 1).astype("boolean")
    rising = rising.reindex(eligible.index).ffill().fillna(False)
    market_health = (panels["trend20"]["sh510300"] > 0) & (breadth >= 0.55) & rising
    for index, (row, decision) in enumerate(zip(ledger["curve"], ledger["decisions"])):
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
        if mode == "latched" and LEVELS[state["phase"]] < LEVELS[previous_phase]:
            errors.append(["RISK_LEVEL_RESTORED", row["date"]])
        # Reconstruct transitions from NAV and market data, never by calling RiskState.
        date = pd.Timestamp(row["date"])
        prior_date = bundle["calendar"][bundle["calendar"].get_loc(date) - 1]
        healthy = bool(market_health.loc[prior_date])
        age_before = state_age + int(index > 0)
        streak_before = healthy_streak + 1 if healthy else 0
        drawdown = 1 - row["prior_close_nav"] / peak
        phase, reason = previous_phase, "UNCHANGED"
        if previous_phase == "HALTED0":
            pass
        elif drawdown >= 0.08 - 1e-12:
            phase, reason = "HALTED0", "GLOBAL_DRAWDOWN_8"
        elif drawdown >= 0.06 - 1e-12 and LEVELS[previous_phase] < 2:
            phase, reason = "FROZEN20", "GLOBAL_DRAWDOWN_6"
        elif drawdown >= 0.04 - 1e-12 and LEVELS[previous_phase] < 1:
            phase, reason = "CAUTION45", "GLOBAL_DRAWDOWN_4"
        elif previous_phase == "PROBE20" and drawdown >= 0.07 - 1e-12:
            phase, reason = "FROZEN20", "PROBE_DRAWDOWN_7"
        elif mode != "latched" and age_before >= 5 and streak_before >= 3:
            if previous_phase in ("FROZEN20", "PROBE20") and drawdown < 0.05 - 1e-12:
                phase, reason = "CAUTION45", "RESTORE_BELOW_5"
            elif previous_phase == "CAUTION45" and drawdown < 0.025 - 1e-12:
                phase, reason = "NORMAL", "RESTORE_BELOW_2_5"
            elif (
                mode == "probe_restore"
                and previous_phase == "FROZEN20"
                and age_before >= 10
                and streak_before >= 5
                and drawdown < 0.07 - 1e-12
            ):
                phase, reason = "PROBE20", "PROBE_AFTER_HEALTHY_MARKET"
        changed = phase != previous_phase
        anchor_before = probe_anchor
        if changed:
            state_age, healthy_streak = 0, 0
            probe_anchor = row["prior_close_nav"] if phase == "PROBE20" else None
        else:
            state_age, healthy_streak = age_before, streak_before
        expected = {
            "phase": phase,
            "reason": reason,
            "level": LEVELS[phase],
            "cap": CAPS[phase],
            "can_buy": phase not in ("FROZEN20", "HALTED0"),
            "changed": changed,
            "healthy": healthy,
            "mode": mode,
            "state_age_before": age_before,
            "healthy_streak_before": streak_before,
            "state_age": state_age,
            "healthy_streak": healthy_streak,
            "probe_anchor_before": anchor_before,
            "probe_anchor": probe_anchor,
        }
        for name, value in expected.items():
            checks += 1
            if state.get(name) != value:
                errors.append(["RISK_EVIDENCE_MISMATCH", row["date"], name])
        checks += 3
        if (
            row["risk_phase"] != phase
            or row["risk_level"] != LEVELS[phase]
            or row["can_buy"] != expected["can_buy"]
        ):
            errors.append(["RISK_CURVE_MISMATCH", row["date"]])
        previous_phase = state["phase"]
    for i, trade in enumerate(ledger["trades"]):
        checks += 3
        date = pd.Timestamp(trade["date"])
        prior = bundle["calendar"][bundle["calendar"].get_loc(date) - 1]
        if trade["signal_date"] != str(prior.date()):
            errors.append(["NONPRIOR_SIGNAL", trade])
        adv = float(bundle["panels"]["adv20"].at[prior, trade["symbol"]])
        if not math.isfinite(adv) or adv <= 0 or trade["gross"] > adv * 0.01 + 0.02:
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
    if any("industry_history" in instrument for instrument in bundle["meta"].values()):
        industries = dated_industry_audit(ledger, bundle)
        checks += industries["checks"]
        errors.extend(industries["errors"])
    return {"status": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors}
