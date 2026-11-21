"""Integer-share, cent-cash research ledger. No account or order integration.

Corporate cash is receivable until its opening availability date. Bonus shares are restricted
until a known listing date. Unavailable dates are never silently invented.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

import pandas as pd


def cents(x):
    """Convert currency to integer cents using decimal half-up rounding."""
    return int((Decimal(str(x)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tick_price(x, tick, up=False):
    """Round a price down or up to the instrument's tick size."""
    d = Decimal(str(tick))
    return float(
        (Decimal(str(x)) / d).quantize(Decimal("1"), rounding=ROUND_CEILING if up else ROUND_FLOOR)
        * d
    )


def nearest_tick(x, tick):
    """Round a reference price to the nearest valid tick using decimal arithmetic."""
    d = Decimal(str(tick))
    return float((Decimal(str(x)) / d).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * d)


def lot_rule(m):
    """Return the minimum buy size and quantity increment for an instrument."""
    if m["kind"] == "stock" and m["code"].startswith(("688", "689")):
        return 200, 1
    if m["kind"] == "stock" and m["code"].startswith(("920", "8", "4")):
        return 100, 1
    return 100, 100


def buy_quantity(budget, price, m, commission=0.0003, minimum=5, transfer=0.00001, *, slippage=0.0):
    """Find an affordable legal buy quantity including minimum commissions."""
    if budget <= 0 or price <= 0:
        return 0
    minimum_qty, step = lot_rule(m)
    tr = transfer if m["kind"] == "stock" else 0
    q = max(0, math.floor((budget - minimum) / (price * (1 + commission + tr + slippage))))
    if step > 1:
        q = q // step * step
    if q < minimum_qty:
        return 0
    while q and cents(price * q) + fees_cents(
        price * q, m, "BUY", commission, minimum, transfer
    ) + slippage_cents(cents(price * q) / 100, slippage) > cents(budget):
        q -= step
    return q if q >= minimum_qty else 0


def fees_cents(gross, m, side, commission=0.0003, minimum=5, transfer=0.00001, stamp=0.0005):
    """Calculate the realized fee in cents for a buy or sell."""
    if gross <= 0:
        return 0
    return (
        cents(max(minimum, gross * commission))
        + cents(gross * transfer if m["kind"] == "stock" else 0)
        + cents(gross * stamp if side == "SELL" and m["kind"] == "stock" else 0)
    )


def slippage_cents(gross, rate):
    """Debit one declared cost on proxy gross; the quote itself is not displaced."""
    if not math.isfinite(rate) or rate < 0 or not math.isfinite(gross) or gross < 0:
        raise ValueError("Slippage rate and gross must be finite and nonnegative")
    return cents(gross * rate)


@dataclass
class Lot:
    """A share lot with purchase and availability dates for T+1 and bonus-share accounting."""

    id: int
    quantity: int
    bought: pd.Timestamp
    available: pd.Timestamp | None
    stop_cost: float


class Ledger:
    """Track integer shares, cent cash, receivables and independently auditable events."""

    def __init__(self, meta, initial=1_000_000.0, dividend_tax=0.20):
        self.meta = meta
        self.initial = cents(initial)
        self.cash = self.initial
        self.lots = {}
        self.receivables = []
        self.trades = []
        self.events = []
        self.rejections = []
        self.entitlements = {}
        self.next_id = 1
        self.dividend_tax = dividend_tax

    def quantity(self, s):
        return sum(x.quantity for x in self.lots.get(s, []))

    def available(self, s, date):
        return sum(
            x.quantity
            for x in self.lots.get(s, [])
            if x.available is not None and x.available <= date
        )

    def values(self, prices):
        return {
            s: cents(self.quantity(s) * prices[s]) / 100 for s in self.lots if self.quantity(s) > 0
        }

    def nav(self, prices):
        return (
            self.cash / 100
            + sum(self.values(prices).values())
            + sum(x["cents"] for x in self.receivables) / 100
        )

    def reject(self, date, s, side, reason):
        self.rejections.append(
            {"date": str(date.date()), "symbol": s, "side": side, "reason": reason}
        )

    def buy(self, date, s, quantity, price, signal_date=None, *, slippage=0.0):
        mn, step = lot_rule(self.meta[s])
        if quantity < mn or quantity % step:
            raise ValueError("Illegal buy quantity")
        gross = cents(quantity * price)
        fee = fees_cents(gross / 100, self.meta[s], "BUY")
        impact = slippage_cents(gross / 100, slippage)
        if gross + fee + impact > self.cash:
            self.reject(date, s, "BUY", "INSUFFICIENT_CASH")
            return False
        self.cash -= gross + fee + impact
        self.lots.setdefault(s, []).append(
            Lot(self.next_id, quantity, date, date + pd.Timedelta(days=1), price)
        )
        self.next_id += 1
        self.trades.append(
            {
                "date": str(date.date()),
                "signal_date": str(signal_date.date()) if signal_date is not None else None,
                "symbol": s,
                "side": "BUY",
                "quantity": quantity,
                "price": price,
                "gross": gross / 100,
                "fee": fee / 100,
                "slippage_rate": slippage,
                "slippage_cost": impact / 100,
                "cash_after": self.cash / 100,
            }
        )
        return True

    def sell(self, date, s, requested, price, signal_date=None, *, slippage=0.0):
        available = self.available(s, date)
        q = min(int(requested), available)
        mn, step = lot_rule(self.meta[s])
        if q < available:
            if step > 1:
                q = q // step * step
            if q < mn:
                q = 0
        if q <= 0:
            self.reject(date, s, "SELL", "T1_OR_MINIMUM_QUANTITY")
            return 0
        gross = cents(q * price)
        fee = fees_cents(gross / 100, self.meta[s], "SELL")
        impact = slippage_cents(gross / 100, slippage)
        if self.cash + gross - fee - impact < 0:
            self.reject(date, s, "SELL", "FEES_EXCEED_CASH")
            return 0
        remain = q
        for lot in self.lots.get(s, []):
            if remain and lot.available is not None and lot.available <= date:
                take = min(remain, lot.quantity)
                lot.quantity -= take
                remain -= take
        self.lots[s] = [x for x in self.lots[s] if x.quantity]
        if not self.lots[s]:
            self.lots.pop(s)
        self.cash += gross - fee - impact
        self.trades.append(
            {
                "date": str(date.date()),
                "signal_date": str(signal_date.date()) if signal_date is not None else None,
                "symbol": s,
                "side": "SELL",
                "quantity": q,
                "price": price,
                "gross": gross / 100,
                "fee": fee / 100,
                "slippage_rate": slippage,
                "slippage_cost": impact / 100,
                "cash_after": self.cash / 100,
            }
        )
        return q

    def snapshot_record_dates(self, date, actions):
        for a in actions:
            if a.get("record_date") == str(date.date()):
                s = a["symbol"]
                key = a["id"]
                self.entitlements[key] = [
                    (x.id, x.quantity, x.stop_cost, x.bought) for x in self.lots.get(s, [])
                ]

    def apply_actions(self, date, actions):
        ds = str(date.date())
        for a in actions:
            if a["ex_date"] != ds:
                continue
            s = a["symbol"]
            rights = self.entitlements.get(a["id"], [])
            q = sum(x[1] for x in rights)
            if not q:
                continue
            tax = self.dividend_tax if self.meta[s]["kind"] == "stock" else 0
            # In these ten-session resets, held lots are all <=1 month. This is a tax
            # scenario, not an assertion of the competition's tax policy.
            rounding = a.get("cash_rounding")
            if rounding == "FLOOR_TO_CENT":
                gross = int(
                    (Decimal(q) * Decimal(str(a["cash_per_share"])) * 100).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                )
            elif rounding is None:
                gross = cents(q * a["cash_per_share"])
            else:
                raise ValueError(f"Unsupported cash rounding: {rounding}")
            reserve = cents(gross / 100 * tax)
            net = gross - reserve
            if net:
                self.receivables.append(
                    {
                        "symbol": s,
                        "cents": net,
                        "pay_date": a.get("cash_pay_date"),
                        "available_date": a.get("cash_available_date", a.get("cash_pay_date")),
                        "event_id": a["id"],
                    }
                )
            multiplier = Decimal(str(a["share_multiplier"]))
            if multiplier <= 0:
                raise ValueError("Invalid share multiplier")
            share_ratio = multiplier - 1
            exact = [share_ratio * x[1] for x in rights]
            allocation = [int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in exact]
            total_exact = sum(exact)
            extra_total = int(total_exact.to_integral_value(rounding=ROUND_FLOOR))
            fraction = float(total_exact - extra_total)
            # Aggregate-account floor, then deterministic largest-remainder allocation to
            # lots. Actual residual-share registration is unverified and remains flagged.
            for j in sorted(
                range(len(exact)), key=lambda j: exact[j] - allocation[j], reverse=True
            )[: extra_total - sum(allocation)]:
                allocation[j] += 1
            if multiplier < 1:
                if self.quantity(s) != q:
                    raise ValueError("Unsupported reverse split after intervening share change")
                exact = [multiplier * x[1] for x in rights]
                allocation = [int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in exact]
                rounding = (
                    ROUND_CEILING if a.get("fractional_rounding") == "CEILING" else ROUND_FLOOR
                )
                new_total = int(sum(exact).to_integral_value(rounding=rounding))
                for j in sorted(
                    range(len(exact)), key=lambda j: exact[j] - allocation[j], reverse=True
                )[: new_total - sum(allocation)]:
                    allocation[j] += 1
                old = {x.id: x for x in self.lots[s]}
                new_lots = []
                for (lot_id, qty, cost, bought), new_qty in zip(rights, allocation):
                    available = (
                        pd.Timestamp(a["share_list_date"]) if a.get("share_list_date") else None
                    )
                    if new_qty:
                        new_lots.append(
                            Lot(
                                lot_id,
                                new_qty,
                                bought,
                                max(old[lot_id].available, available)
                                if old[lot_id].available is not None and available is not None
                                else None,
                                (cost - a["cash_per_share"]) / a["share_multiplier"],
                            )
                        )
                self.lots[s] = new_lots
                extra_total = new_total - q
                fraction = (
                    0.0 if a.get("fractional_rounding") else float(abs(sum(exact) - new_total))
                )
            for (lot_id, qty, cost, bought), extra in (
                [] if multiplier < 1 else zip(rights, allocation)
            ):
                adjusted_cost = max(0.0001, (cost - a["cash_per_share"]) / a["share_multiplier"])
                for lot in self.lots.get(s, []):
                    if lot.id == lot_id:
                        lot.stop_cost = adjusted_cost
                if extra:
                    available = (
                        pd.Timestamp(a["share_list_date"]) if a.get("share_list_date") else None
                    )
                    self.lots.setdefault(s, []).append(
                        Lot(self.next_id, extra, bought, available, adjusted_cost)
                    )
                    self.next_id += 1
            self.events.append(
                {
                    "date": ds,
                    "symbol": s,
                    "type": "CORPORATE_ACTION",
                    "entitled_quantity": q,
                    "cash_gross": gross / 100,
                    "cash_tax_reserve": reserve / 100,
                    "net_receivable": net / 100,
                    "share_quantity_change": extra_total,
                    "extra_shares_restricted": max(0, extra_total),
                    "fractional_shares_discarded": fraction,
                    "pay_date": a.get("cash_pay_date"),
                    "cash_available_date": a.get("cash_available_date", a.get("cash_pay_date")),
                    "share_list_date": a.get("share_list_date"),
                    "unknown_cash_pay_date": bool(net and not a.get("cash_pay_date")),
                    "unknown_cash_available_date": bool(
                        net and not a.get("cash_available_date", a.get("cash_pay_date"))
                    ),
                    "unknown_share_list_date": bool(extra_total and not a.get("share_list_date")),
                    "bonus_tax_not_modelled": bool(a.get("bonus_per_share", 0) > 0),
                }
            )
        remaining = []
        for r in self.receivables:
            if r["available_date"] and r["available_date"] <= ds:
                self.cash += r["cents"]
                self.events.append(
                    {
                        "date": ds,
                        "symbol": r["symbol"],
                        "type": "CASH_DIVIDEND_PAID",
                        "amount": r["cents"] / 100,
                        "event_id": r["event_id"],
                        "pay_date": r["pay_date"],
                        "cash_available_date": r["available_date"],
                    }
                )
            else:
                remaining.append(r)
        self.receivables = remaining

    def stop_cost(self, s):
        q = self.quantity(s)
        return sum(x.quantity * x.stop_cost for x in self.lots.get(s, [])) / q if q else None


def causal_price_index(raw, actions):
    """Causal total-return bars and completed-day gaps without cumulative-scale noise."""
    mult = pd.Series(1.0, index=raw.index)
    cash = pd.Series(0.0, index=raw.index)
    for a in sorted(actions, key=lambda x: x["ex_date"]):
        d = pd.Timestamp(a["ex_date"])
        # During a suspension, the next quoted bar is the first post-action price.
        # If several actions occur while suspended, cash is per then-current share.
        if raw.index[0] <= d <= raw.index[-1]:
            d = raw.index[raw.index.searchsorted(d)]
            cash.loc[d] += a["cash_per_share"] * mult.loc[d]
            mult.loc[d] *= a["share_multiplier"]
    ret = (raw.close * mult + cash) / raw.close.shift() - 1
    ret.iloc[0] = 0
    if (ret.dropna() <= -1).any():
        raise ValueError("Invalid corporate-action total return")
    idx = (1 + ret).cumprod() * raw.close.iloc[0]
    factor = idx / raw.close
    adj = raw.copy()
    for field in ["open", "high", "low", "close"]:
        adj[field] = raw[field] * factor
    # Cancel consecutive index factors before dividing. This is a close-time feature:
    # its cash term uses today's close, not an opening-time execution reference.
    adj["opening_gap"] = (raw.open / raw.close.shift()) * (mult + cash / raw.close) - 1
    return adj, mult.cumprod()
