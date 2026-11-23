"""Integer-share next-open simulation with non-recovering risk controls."""

from __future__ import annotations

import collections
import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .allocation import allocate
from .funds import (
    concentration_values,
    exposure_report,
    purchase_capacity,
    uses_reported_exposure,
)
from .industries import metadata_at, require_industries
from .ledger import Ledger, buy_quantity, cents, fees_cents, lot_rule, slippage_cents
from .market import EXECUTION_MODEL, opening_price, regime, risk_warning
from .metrics import account_stats
from .models import Forecast
from .risk import RISK_MODES, RiskState, healthy_market
from .rules import config_for, turnover_for


def execution_targets(
    weights: dict[str, float],
    positions: dict[str, float],
    nav: float,
    stopped: Iterable[str],
    *,
    planned: bool,
    halted: bool,
) -> dict[str, float]:
    """Convert weights to opening CNY targets, then apply position exits and risk stops."""
    targets = (
        {symbol: nav * weight for symbol, weight in weights.items()} if planned else dict(positions)
    )
    if planned:
        for symbol in positions:
            targets.setdefault(symbol, 0.0)
    for symbol in stopped:
        targets[symbol] = 0.0
    if halted:
        targets = {symbol: 0.0 for symbol in positions}
    return targets


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
    if uses_reported_exposure(meta):
        return all(
            value <= 0.30 * after_fee_nav + 1e-8
            for value in concentration_values(meta, proposed).values()
        )
    sectors: dict[str, float] = collections.defaultdict(float)
    for s, v in proposed.items():
        sectors[meta[s]["industry"]] += v
    return all(v <= 0.30 * after_fee_nav + 1e-8 for v in sectors.values())


def buy_ceiling(raw, previous, date, actions, atr):
    """Limit acceptable opening gaps using prior-close ATR and ex-date reference adjustments."""
    if previous not in raw.index:
        return None
    reference = float(raw.at[previous, "close"])
    for a in actions:
        if a["ex_date"] == str(date.date()):
            reference = (
                reference - a.get("ex_reference_cash_per_share", a["cash_per_share"])
            ) / a.get("ex_reference_share_multiplier", a["share_multiplier"])
            if a.get("price_limit_reference") is not None:
                reference = a["price_limit_reference"]
    if not np.isfinite(atr):
        return None
    return reference * (1 + float(np.clip(atr, 0.015, 0.05)))


@dataclass
class Execution:
    """Opening fills with costs charged separately."""

    ledger: Ledger
    bundle: dict
    index: int
    prices: dict[str, float]
    actions: dict[str, list[dict]]
    slippage: float
    cap: float
    marks: list[dict]

    def __post_init__(self):
        self.date = self.bundle["calendar"][self.index]
        self.previous = self.bundle["calendar"][self.index - 1]
        information_at = self.bundle.get("information_at") or (
            self.date.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
        )
        self.ledger.meta = metadata_at(self.ledger.meta, information_at)
        require_industries(self.ledger.meta, self.ledger.lots)

    def _quote(self, symbol, side):
        return opening_price(
            self.ledger.meta[symbol],
            self.bundle["raws"][symbol],
            self.date,
            self.previous,
            self.actions[symbol],
            side,
            status=self.bundle.get("opening_status", {}).get(str(self.date.date()), {}).get(symbol),
            as_of=self.bundle.get("information_at"),
            calendar=self.bundle["calendar"],
        )

    def _observe(self):
        position_values = self.ledger.values(self.prices)
        nav = self.ledger.nav(self.prices)
        self.marks.append(
            {
                "date": str(self.date.date()),
                "after_fill": len(self.ledger.trades),
                "max_nav_weight": max(position_values.values(), default=0) / nav,
                "gross_exposure": sum(position_values.values()) / nav,
                "risk_cap": self.cap,
                "max_industry_weight": max(
                    (
                        sum(
                            v
                            for x, v in position_values.items()
                            if self.ledger.meta[x]["industry"] == sector
                        )
                        for sector in {self.ledger.meta[x]["industry"] for x in position_values}
                    ),
                    default=0,
                )
                / nav,
            }
        )
        if self.bundle.get("fund_mode") == "reported":
            information_at = self.bundle.get("information_at") or (
                self.date.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
            )
            self.marks[-1]["fund_exposure"] = exposure_report(
                self.ledger.meta, position_values, nav, information_at
            )

    def sell(self, targets: dict[str, float]) -> None:
        """Reduce positions to CNY target values using sellable opening lots."""
        for symbol in list(self.ledger.lots):
            current = self.ledger.quantity(symbol) * self.prices[symbol]
            desired = targets.get(symbol, 0.0)
            if desired >= current - 100:
                continue
            price, reason = self._quote(symbol, "SELL")
            if reason:
                self.ledger.reject(self.date, symbol, "SELL", reason)
                continue
            adv = self.bundle["panels"]["adv20"].at[self.previous, symbol]
            if not np.isfinite(adv):
                self.ledger.reject(self.date, symbol, "SELL", "MISSING_LIQUIDITY")
                continue
            quantity = min(
                self.ledger.quantity(symbol), math.floor(min(current - desired, adv * 0.01) / price)
            )
            if desired == 0:
                quantity = min(self.ledger.quantity(symbol), math.floor(adv * 0.01 / price))
            self.ledger.sell(
                self.date, symbol, quantity, price, self.previous, slippage=self.slippage
            )
            self.prices[symbol] = price
            self._observe()

    def buy(
        self,
        targets: dict[str, float],
        stopped: list[str],
        valid: dict[str, Forecast],
        decision: dict,
    ) -> None:
        """Fund CNY target values after sells, checking each settled fill."""
        for symbol, desired in targets.items():
            if desired <= 0 or symbol in stopped:
                continue
            if self.ledger.meta[symbol]["industry"] is None:
                self.ledger.reject(self.date, symbol, "BUY", "UNKNOWN_INDUSTRY")
                continue
            price, reason = self._quote(symbol, "BUY")
            if reason:
                self.ledger.reject(self.date, symbol, "BUY", reason)
                continue
            current = self.ledger.quantity(symbol) * price
            if desired <= current + 100:
                continue
            ceiling = buy_ceiling(
                self.bundle["raws"][symbol],
                self.previous,
                self.date,
                self.actions[symbol],
                self.bundle["atr_lookup"].get((self.index - 1, symbol), np.nan),
            )
            if ceiling is None or price > ceiling:
                self.ledger.reject(self.date, symbol, "BUY", "VOLATILITY_GAP_CEILING")
                continue
            self.prices[symbol] = price
            adv = self.bundle["panels"]["adv20"].at[self.previous, symbol]
            if not np.isfinite(adv):
                self.ledger.reject(self.date, symbol, "BUY", "MISSING_LIQUIDITY")
                continue
            position_values = self.ledger.values(self.prices)
            nav = self.ledger.nav(self.prices)
            industry = sum(
                v
                for x, v in position_values.items()
                if self.ledger.meta[x]["industry"] == self.ledger.meta[symbol]["industry"]
            )
            budget = min(
                max(0.0, desired - current),
                adv * 0.01,
                self.ledger.cash / 100,
                max(0.0, 0.15 * nav - current),
                max(0.0, 0.30 * nav - industry),
                max(0.0, self.cap * nav - sum(position_values.values())),
            )
            if uses_reported_exposure(self.ledger.meta):
                budget = min(
                    budget, purchase_capacity(self.ledger.meta, position_values, symbol, nav)
                )
            quantity = (
                buy_quantity(budget, price, self.ledger.meta[symbol], slippage=self.slippage)
                if budget >= 100
                else 0
            )
            if not quantity:
                self.ledger.reject(self.date, symbol, "BUY", "LOT_BUDGET_TOO_SMALL")
                continue
            minimum, step = lot_rule(self.ledger.meta[symbol])
            if any(v > 0.15 * nav + 1e-8 for x, v in position_values.items() if x != symbol):
                self.ledger.reject(self.date, symbol, "BUY", "OTHER_HOLDING_ABOVE_NAV_CAP")
                continue
            initial = quantity
            while quantity >= minimum:
                gross = cents(quantity * price) / 100
                fee = fees_cents(gross, self.ledger.meta[symbol], "BUY") / 100
                impact = slippage_cents(gross, self.slippage) / 100
                after_fee_nav = nav - fee - impact
                if (
                    post_buy_limits(
                        position_values,
                        self.ledger.meta,
                        symbol,
                        gross,
                        after_fee_nav,
                        self.cap,
                    )
                    and gross + fee + impact <= self.ledger.cash / 100 + 1e-8
                ):
                    break
                quantity -= step
            if quantity < minimum:
                self.ledger.reject(self.date, symbol, "BUY", "RESIZED_BELOW_MINIMUM")
                continue
            estimated_fee = (
                fees_cents(quantity * price, self.ledger.meta[symbol], "BUY")
                + fees_cents(quantity * price, self.ledger.meta[symbol], "SELL")
            ) / 100
            edge = valid.get(symbol, {"mu": -0.05, "margin": 0})
            if (
                edge["mu"] - edge["margin"]
                <= estimated_fee / (quantity * price) + 2 * self.slippage
            ):
                self.ledger.reject(self.date, symbol, "BUY", "EXACT_FEES_EXCEED_EDGE")
                continue
            if self.ledger.buy(
                self.date, symbol, quantity, price, self.previous, slippage=self.slippage
            ):
                if quantity != initial:
                    decision.setdefault("resized_buys", []).append(
                        {"symbol": symbol, "from": initial, "to": quantity}
                    )
                self._observe()


def _signal_decision(
    bundle, ledger, last_close, risk, forecasts, index, start, slippage, rules, turnover
):
    """Calculate target weights from the preceding session's close."""
    calendar, panels = bundle["calendar"], bundle["panels"]
    date = calendar[index]
    previous = calendar[index - 1]
    meta = metadata_at(
        bundle["meta"], previous.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=16)
    )
    ledger.meta = meta
    require_industries(meta, ledger.lots)
    day = str(date.date())
    active_config = config_for(rules, index - 1)
    active_turnover = turnover_for(turnover, index - 1)
    prior = ledger.nav(last_close)
    prior_values = ledger.values(last_close)
    state = risk.update(prior, healthy_market(panels, previous))
    drawdown = state["drawdown"]
    level = state["level"]
    exposure = min(regime(panels, previous)[0], state["cap"])
    cap = exposure
    stopped = {
        symbol
        for symbol in prior_values
        if last_close[symbol] / ledger.stop_cost(symbol) - 1 <= -0.08
    }
    scheduled = (index - start) % 5 == 0
    forced = state["changed"] or sum(prior_values.values()) / prior > cap + 0.01
    planned = scheduled or forced
    forecasts_today = {
        symbol: v
        for symbol, v in forecasts.get(index - 1, {}).items()
        if panels["eligible"].at[previous, symbol]
        and meta[symbol]["industry"] is not None
        and risk_warning(
            meta[symbol],
            date,
            as_of=previous.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=16),
        )
        is None
        and np.isfinite(v["mu"])
        and np.isfinite(v["margin"])
    }
    if planned:
        weights = {symbol: v / prior for symbol, v in prior_values.items()}
        sellable = {
            symbol: ledger.available(symbol, date) * last_close[symbol] / prior
            for symbol in prior_values
        }
        target_weights, decision = allocate(
            {
                "weights": weights,
                "nav": prior,
                "sellable": sellable,
                "receivable_weight": sum(x["cents"] for x in ledger.receivables) / 100 / prior,
            },
            forecasts_today,
            {
                "meta": meta,
                "adv": panels["adv20"].loc[previous].to_dict(),
                "returns": panels["ret1"].loc[:previous].tail(60),
            },
            exposure=exposure,
            level=level,
            slippage=slippage,
            config=active_config,
            turnover=active_turnover,
            allow_probe=state["phase"] == "PROBE20",
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
        execution_date=day,
        drawdown=drawdown,
        scheduled=scheduled,
        forced=forced,
    )
    decision["recovery"] = state
    decision["rule_config"] = active_config
    if active_turnover != "legacy":
        decision["turnover_policy"] = active_turnover
    decision["target_weights"] = dict(target_weights)
    return decision, forecasts_today, stopped, prior, cap


def _closing_row(bundle, ledger, date, prices, last_close, record_actions, prior, state, cap):
    """Mark closing positions and record the session's risk state."""
    raws = bundle["raws"]
    day = str(date.date())
    level, drawdown = state["level"], state["drawdown"]
    closing = {}
    for symbol in ledger.lots:
        close = raws[symbol].at[date, "close"] if date in raws[symbol].index else np.nan
        closing[symbol] = close if np.isfinite(close) else last_close.get(symbol, prices[symbol])
        last_close[symbol] = closing[symbol]
    ledger.snapshot_record_dates(date, record_actions)
    position_values = ledger.values(closing)
    nav = ledger.nav(closing)
    receivables = sum(x["cents"] for x in ledger.receivables) / 100
    positions = {
        symbol: {
            "quantity": ledger.quantity(symbol),
            "available": ledger.available(symbol, date),
            "close": closing[symbol],
            "value": position_values[symbol],
        }
        for symbol in position_values
    }
    row = {
        "date": day,
        "prior_close_nav": prior,
        "nav": nav,
        "cash": ledger.cash / 100,
        "holdings_value": sum(position_values.values()),
        "receivables": receivables,
        "positions": positions,
        "risk_level": level,
        "max_nav_weight": max(position_values.values(), default=0) / nav,
        "risk_cap": cap,
        "risk_phase": state["phase"],
        "risk_peak": state["peak"],
        "risk_drawdown": drawdown,
        "can_buy": state["can_buy"],
        "exposure_above_target": sum(position_values.values()) / nav > cap + 0.01,
    }
    if bundle.get("fund_mode") == "reported":
        closing_at = date.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=16)
        row["fund_exposure"] = exposure_report(
            metadata_at(bundle["meta"], closing_at),
            position_values,
            nav,
            closing_at,
            sources=True,
        )
    return row


@pd.option_context("compute.use_numexpr", False)
def simulate(
    bundle,
    start,
    end,
    forecasts: dict[int, dict[str, Forecast]],
    slippage=0.001,
    detail=True,
    mode="latched",
    rules="baseline",
    *,
    turnover="legacy",
):
    """Simulate next-open fills from prior-close signals."""
    if mode not in RISK_MODES:
        raise ValueError("Unknown risk mode")
    if not 1 <= start <= end < len(bundle["calendar"]):
        raise ValueError("Execution range needs a prior signal day and available market bars")
    if not np.isfinite(slippage) or slippage < 0:
        raise ValueError("Slippage must be finite and nonnegative")
    meta = bundle["meta"]
    raws = bundle["raws"]
    calendar = bundle["calendar"]
    actions = bundle["actions"]
    ex_dates = collections.defaultdict(list)
    record_dates = collections.defaultdict(list)
    actions_by_symbol = collections.defaultdict(list)
    for action in actions:
        ex_dates[action["ex_date"]].append(action)
        actions_by_symbol[action["symbol"]].append(action)
        if action.get("record_date"):
            record_dates[action["record_date"]].append(action)
    table = bundle["table"]
    atr = bundle.get("atr_lookup")
    if atr is None:
        atr = {
            (int(d), str(symbol)): float(v)
            for d, symbol, v in zip(table.date_index, table.symbol, table.atr_pct)
        }
        bundle["atr_lookup"] = atr
    ledger = Ledger(meta)
    last_close: dict[str, float] = {}
    curve = []
    quality = []
    decisions = []
    intermediate: list[dict] = []
    risk = RiskState(mode=mode, peak=ledger.initial / 100)
    for index in range(start, end + 1):
        date = calendar[index]
        day = str(date.date())
        decision, forecasts_today, stopped, prior, cap = _signal_decision(
            bundle, ledger, last_close, risk, forecasts, index, start, slippage, rules, turnover
        )
        state = decision["recovery"]
        level = state["level"]
        planned = decision["scheduled"] or decision["forced"]
        target_weights = decision["target_weights"]
        decisions.append(decision)
        for action in ex_dates[day]:
            if ledger.quantity(action["symbol"]) and (
                not action.get("record_date") or action["record_date"] >= day
            ):
                quality.append(
                    {
                        "date": day,
                        "symbol": action["symbol"],
                        "problem": "UNSUPPORTED_RECORD_DATE_ORDER",
                    }
                )
        ledger.apply_actions(date, ex_dates[day])
        for action in ex_dates[day]:
            symbol = action["symbol"]
            if symbol in last_close:
                last_close[symbol] = (last_close[symbol] - action["cash_per_share"]) / action[
                    "share_multiplier"
                ]
        prices = {}
        for symbol in ledger.lots:
            opening = raws[symbol].at[date, "open"] if date in raws[symbol].index else np.nan
            prices[symbol] = (
                opening
                if np.isfinite(opening)
                else last_close.get(symbol, ledger.stop_cost(symbol))
            )
            if not np.isfinite(opening):
                quality.append({"date": day, "symbol": symbol, "problem": "STALE_MARK"})
        nav = ledger.nav(prices)
        values = ledger.values(prices)
        targets = execution_targets(
            target_weights, values, nav, stopped, planned=planned, halted=level == 3
        )

        execution = Execution(
            ledger, bundle, index, prices, actions_by_symbol, slippage, cap, intermediate
        )
        execution.sell(targets)
        if planned and state["can_buy"]:
            execution.buy(targets, stopped, forecasts_today, decision)
        curve.append(
            _closing_row(
                bundle, ledger, date, prices, last_close, record_dates[day], prior, state, cap
            )
        )
        if ledger.cash < 0:
            raise ValueError("Negative cash")
    summary = account_stats(ledger, curve, decisions, quality, mode)
    full: dict = {
        "execution": {"model": EXECUTION_MODEL, "slippage_rate": slippage},
        "curve": curve,
        "trades": ledger.trades,
        "events": ledger.events,
        "rejections": ledger.rejections,
        "quality": quality,
        "decisions": decisions,
        "intermediate_weights": intermediate,
    }
    if mode != "latched":
        full["execution"]["risk_mode"] = mode
    if turnover != "legacy":
        full["execution"]["turnover_policy"] = (
            turnover if isinstance(turnover, str) else "scheduled"
        )
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
    *,
    turnover="legacy",
):
    """Run reset-capital ten-session accounts and retain the final partial account separately."""
    if not 0 <= signal_start <= signal_end < len(bundle["calendar"]) - 1:
        raise ValueError("Signal range must have a following execution day")
    out = []
    for start in range(signal_start + 1, signal_end + 2, 10):
        end = min(start + 9, signal_end + 1)
        if end >= len(bundle["calendar"]):
            break
        summary, ledger = simulate(
            bundle, start, end, forecasts, slippage, detail, mode, rules, turnover=turnover
        )
        out.append(
            {"summary": summary, "ledger": ledger, "complete_10_sessions": end - start + 1 == 10}
        )
    return out
