"""Integer-share next-open simulation for the retained non-recovering strategy."""

from __future__ import annotations

import collections
import math

import numpy as np

from .allocation import allocate
from .ledger import Ledger, buy_quantity, fees_cents
from .market import opening_price, regime
from .risk import RiskState, healthy_market
from .rules import config_for


def post_buy_limits(values, meta, symbol, purchase, after_fee_nav, cap):
    """Guard every holding/industry and total exposure after actual prior sells."""
    if after_fee_nav <= 0:
        return False
    proposed = dict(values)
    proposed[symbol] = proposed.get(symbol, 0.0) + purchase
    if any(v > 0.15 * after_fee_nav + 1e-8 for v in proposed.values()):
        return False
    if sum(proposed.values()) > cap * after_fee_nav + 1e-8:
        return False
    sectors = collections.defaultdict(float)
    for s, v in proposed.items():
        sectors[meta[s]["industry"]] += v
    return all(v <= 0.30 * after_fee_nav + 1e-8 for v in sectors.values())


def buy_ceiling(meta, raw, previous, date, actions, atr):
    """Limit acceptable opening gaps using prior-close ATR and ex-date reference adjustments."""
    reference = float(raw.at[previous, "close"])
    for a in actions:
        if a["ex_date"] == str(date.date()):
            reference = (reference - a.get("ex_reference_cash_per_share", a["cash_per_share"])) / a[
                "share_multiplier"
            ]
            if a.get("price_limit_reference") is not None:
                reference = a["price_limit_reference"]
    if not np.isfinite(atr):
        return None
    return reference * (1 + float(np.clip(atr, 0.015, 0.05)))


def simulate(
    bundle, start, end, forecasts, slippage=0.001, detail=True, mode="latched", rules="baseline"
):
    """Replay next-open executions with prior-close signals and non-recovering risk control."""
    assert mode == "latched", "V6 uses frozen non-recovering risk only"
    meta = bundle["meta"]
    raws = bundle["raws"]
    panels = bundle["panels"]
    calendar = bundle["calendar"]
    actions = bundle["actions"]
    bydate = collections.defaultdict(list)
    byrecord = collections.defaultdict(list)
    bysymbol = collections.defaultdict(list)
    for a in actions:
        bydate[a["ex_date"]].append(a)
        bysymbol[a["symbol"]].append(a)
        if a.get("record_date"):
            byrecord[a["record_date"]].append(a)
    table = bundle["table"]
    atr = bundle.get("atr_lookup")
    if atr is None:
        atr = {
            (int(d), str(s)): float(v)
            for d, s, v in zip(table.date_index, table.symbol, table.atr_pct)
        }
        bundle["atr_lookup"] = atr
    ledger = Ledger(meta)
    last = {}
    curve = []
    quality = []
    decisions = []
    intermediate = []
    risk = RiskState(mode=mode)
    for index in range(start, end + 1):
        date = calendar[index]
        previous = calendar[index - 1]
        ds = str(date.date())
        active_config = config_for(rules, index - 1)
        prior = ledger.nav(last)
        prior_values = ledger.values(last)
        state = risk.update(prior, healthy_market(panels, previous))
        dd = state["drawdown"]
        level = state["level"]
        exposure = min(regime(panels, previous)[0], state["cap"])
        cap = exposure
        stopped = {s for s in prior_values if last[s] / ledger.stop_cost(s) - 1 <= -0.08}
        scheduled = (index - start) % 5 == 0
        forced = state["changed"] or sum(prior_values.values()) / prior > cap + 0.01
        planned = scheduled or forced
        valid = {
            s: v
            for s, v in forecasts.get(index - 1, {}).items()
            if panels["eligible"].at[previous, s]
            and np.isfinite(v["mu"])
            and np.isfinite(v["margin"])
        }
        if planned:
            weights = {s: v / prior for s, v in prior_values.items()}
            sellable = {s: ledger.available(s, date) * last[s] / prior for s in prior_values}
            target_weights, decision = allocate(
                meta,
                valid,
                weights,
                exposure,
                level,
                panels["adv20"].loc[previous].to_dict(),
                prior,
                sellable,
                panels["ret1"].loc[:previous].tail(60),
                slippage,
                sum(x["cents"] for x in ledger.receivables) / 100 / prior,
                config=active_config,
            )
        else:
            target_weights = {}
            decision = {
                "status": "HOLD_BETWEEN_REBALANCES",
                "cap": cap,
                "level": level,
                "selected": [],
            }
        decision.update(
            signal_date=str(previous.date()),
            execution_date=ds,
            drawdown=dd,
            scheduled=scheduled,
            forced=forced,
        )
        decision["recovery"] = state
        decision["rule_config"] = active_config
        decisions.append(decision)
        for a in bydate[ds]:
            if ledger.quantity(a["symbol"]) and (
                not a.get("record_date") or a["record_date"] >= ds
            ):
                quality.append(
                    {"date": ds, "symbol": a["symbol"], "problem": "UNSUPPORTED_RECORD_DATE_ORDER"}
                )
        ledger.apply_actions(date, bydate[ds])
        for a in bydate[ds]:
            s = a["symbol"]
            if s in last:
                last[s] = (last[s] - a["cash_per_share"]) / a["share_multiplier"]
        prices = {}
        for s in ledger.lots:
            o = raws[s].at[date, "open"] if date in raws[s].index else np.nan
            prices[s] = o if np.isfinite(o) else last.get(s, ledger.stop_cost(s))
            if not np.isfinite(o):
                quality.append({"date": ds, "symbol": s, "problem": "STALE_MARK"})
        nav = ledger.nav(prices)
        values = ledger.values(prices)
        targets = {s: nav * w for s, w in target_weights.items()} if planned else dict(values)
        if planned:
            for s in values:
                targets.setdefault(s, 0.0)
        for s in stopped:
            targets[s] = 0.0
        if level == 3:
            targets = {s: 0.0 for s in values}

        def quote(s, side):
            return opening_price(
                meta[s], raws[s], date, previous, bysymbol[s], side, slippage, True
            )

        def observe():
            vals = ledger.values(prices)
            wealth = ledger.nav(prices)
            intermediate.append(
                {
                    "date": ds,
                    "after_fill": len(ledger.trades),
                    "max_nav_weight": max(vals.values(), default=0) / wealth,
                    "gross_exposure": sum(vals.values()) / wealth,
                    "risk_cap": cap,
                    "max_industry_weight": max(
                        (
                            sum(v for x, v in vals.items() if meta[x]["industry"] == sector)
                            for sector in {meta[x]["industry"] for x in vals}
                        ),
                        default=0,
                    )
                    / wealth,
                }
            )

        for s in list(ledger.lots):
            current = ledger.quantity(s) * prices[s]
            desired = targets.get(s, 0.0)
            if desired >= current - 100:
                continue
            price, reason = quote(s, "SELL")
            if reason:
                ledger.reject(date, s, "SELL", reason)
                continue
            adv = panels["adv20"].at[previous, s]
            if not np.isfinite(adv):
                ledger.reject(date, s, "SELL", "MISSING_LIQUIDITY")
                continue
            q = min(ledger.quantity(s), math.floor(min(current - desired, adv * 0.01) / price))
            if desired == 0:
                q = min(ledger.quantity(s), math.floor(adv * 0.01 / price))
            ledger.sell(date, s, q, price, previous)
            prices[s] = price
            observe()
        if planned and state["can_buy"]:
            for s, desired in targets.items():
                if desired <= 0 or s in stopped:
                    continue
                price, reason = quote(s, "BUY")
                if reason:
                    ledger.reject(date, s, "BUY", reason)
                    continue
                current = ledger.quantity(s) * price
                if desired <= current + 100:
                    continue
                ceiling = buy_ceiling(
                    meta[s], raws[s], previous, date, bysymbol[s], atr.get((index - 1, s), np.nan)
                )
                if ceiling is None or price > ceiling:
                    ledger.reject(date, s, "BUY", "VOLATILITY_GAP_CEILING")
                    continue
                prices[s] = price
                adv = panels["adv20"].at[previous, s]
                if not np.isfinite(adv):
                    ledger.reject(date, s, "BUY", "MISSING_LIQUIDITY")
                    continue
                vals = ledger.values(prices)
                wealth = ledger.nav(prices)
                industry = sum(
                    v for x, v in vals.items() if meta[x]["industry"] == meta[s]["industry"]
                )
                budget = min(
                    max(0.0, desired - current),
                    adv * 0.01,
                    ledger.cash / 100,
                    max(0.0, 0.15 * wealth - current),
                    max(0.0, 0.30 * wealth - industry),
                    max(0.0, cap * wealth - sum(vals.values())),
                )
                q = buy_quantity(budget, price, meta[s]) if budget >= 100 else 0
                if not q:
                    ledger.reject(date, s, "BUY", "LOT_BUDGET_TOO_SMALL")
                    continue
                minimum = (
                    200
                    if meta[s]["kind"] == "stock" and meta[s]["code"].startswith(("688", "689"))
                    else 100
                )
                step = (
                    1
                    if meta[s]["kind"] == "stock"
                    and meta[s]["code"].startswith(("688", "689", "920", "8", "4"))
                    else 100
                )
                if any(v > 0.15 * wealth + 1e-8 for x, v in vals.items() if x != s):
                    ledger.reject(date, s, "BUY", "OTHER_HOLDING_ABOVE_NAV_CAP")
                    continue
                initial = q
                while q >= minimum:
                    fee = fees_cents(q * price, meta[s], "BUY") / 100
                    after = wealth - fee
                    if (
                        post_buy_limits(vals, meta, s, q * price, after, cap)
                        and q * price + fee <= ledger.cash / 100 + 1e-8
                    ):
                        break
                    q -= step
                if q < minimum:
                    ledger.reject(date, s, "BUY", "RESIZED_BELOW_MINIMUM")
                    continue
                estimated_fee = (
                    fees_cents(q * price, meta[s], "BUY") + fees_cents(q * price, meta[s], "SELL")
                ) / 100
                edge = valid.get(s, {"mu": -0.05, "margin": 0})
                if edge["mu"] - edge["margin"] <= estimated_fee / (q * price) + 2 * slippage:
                    ledger.reject(date, s, "BUY", "EXACT_FEES_EXCEED_EDGE")
                    continue
                if ledger.buy(date, s, q, price, previous):
                    if q != initial:
                        decisions[-1].setdefault("resized_buys", []).append(
                            {"symbol": s, "from": initial, "to": q}
                        )
                    observe()
        closing = {}
        for s in ledger.lots:
            c = raws[s].at[date, "close"] if date in raws[s].index else np.nan
            closing[s] = c if np.isfinite(c) else last.get(s, prices[s])
            last[s] = closing[s]
        ledger.snapshot_record_dates(date, byrecord[ds])
        vals = ledger.values(closing)
        wealth = ledger.nav(closing)
        receivables = sum(x["cents"] for x in ledger.receivables) / 100
        positions = {
            s: {
                "quantity": ledger.quantity(s),
                "available": ledger.available(s, date),
                "close": closing[s],
                "value": vals[s],
            }
            for s in vals
        }
        curve.append(
            {
                "date": ds,
                "prior_close_nav": prior,
                "nav": wealth,
                "cash": ledger.cash / 100,
                "holdings_value": sum(vals.values()),
                "receivables": receivables,
                "positions": positions,
                "risk_level": level,
                "max_nav_weight": max(vals.values(), default=0) / wealth,
                "risk_cap": cap,
                "risk_phase": state["phase"],
                "risk_peak": state["peak"],
                "risk_drawdown": dd,
                "can_buy": state["can_buy"],
                "exposure_above_target": sum(vals.values()) / wealth > cap + 0.01,
            }
        )
        if ledger.cash < 0:
            raise ValueError("Negative cash")
    navs = np.r_[1e6, [r["nav"] for r in curve]]
    daily = np.diff(navs) / navs[:-1]
    worst = float(np.quantile(daily, 0.05))
    unresolved = [
        e
        for e in ledger.events
        if e["type"] == "CORPORATE_ACTION"
        and (
            e["unknown_cash_pay_date"]
            or e["unknown_share_list_date"]
            or e["bonus_tax_not_modelled"]
            or e["fractional_shares_discarded"] > 0
        )
    ]
    summary = {
        "start": curve[0]["date"],
        "end": curve[-1]["date"],
        "sessions": len(curve),
        "return": navs[-1] / 1e6 - 1,
        "max_drawdown": float(np.min(navs / np.maximum.accumulate(navs) - 1)),
        "es95": float(daily[daily <= worst].mean()),
        "fees": sum(t["fee"] for t in ledger.trades),
        "trades": len(ledger.trades),
        "turnover": sum(t["gross"] for t in ledger.trades) / 1e6,
        "ending_cash": ledger.cash / 100,
        "ending_receivables": receivables,
        "unresolved_events": len(unresolved),
        "quality_issues": len(quality),
        "rejection_counts": dict(collections.Counter(x["reason"] for x in ledger.rejections)),
        "solver_counts": dict(collections.Counter(x["status"] for x in decisions)),
        "mean_exposure": float(np.mean([r["holdings_value"] / r["nav"] for r in curve])),
        "target_exposure_breach_days": sum(r["exposure_above_target"] for r in curve),
        "risk_mode": mode,
        "rule_config_days": dict(collections.Counter(d["rule_config"] for d in decisions)),
        "phase_days": dict(collections.Counter(r["risk_phase"] for r in curve)),
        "recovery_transitions": [
            {"date": d["execution_date"], **d["recovery"]}
            for d in decisions
            if d["recovery"]["changed"]
        ],
        "probe_entries": sum(
            d["recovery"]["changed"] and d["recovery"]["phase"] == "PROBE20" for d in decisions
        ),
        "probe_failures": sum(d["recovery"]["reason"] == "PROBE_LOSS_1" for d in decisions),
    }
    full = {
        "curve": curve,
        "trades": ledger.trades,
        "events": ledger.events,
        "rejections": ledger.rejections,
        "quality": quality,
        "decisions": decisions,
        "intermediate_weights": intermediate,
    }
    return summary, full if detail else None


def episodes(
    bundle,
    signal_start,
    signal_end,
    forecasts,
    slippage=0.001,
    detail=False,
    mode="latched",
    rules="baseline",
):
    """Run reset-capital ten-session accounts and retain the final partial account separately."""
    out = []
    for start in range(signal_start + 1, signal_end + 2, 10):
        end = min(start + 9, signal_end + 1)
        if end >= len(bundle["calendar"]):
            break
        summary, ledger = simulate(bundle, start, end, forecasts, slippage, detail, mode, rules)
        out.append(
            {"summary": summary, "ledger": ledger, "complete_10_sessions": end - start + 1 == 10}
        )
    return out
