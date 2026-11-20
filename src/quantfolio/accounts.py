"""Manual paper-account snapshots and independent opening-fill reconciliation."""

import math
import re
from collections import defaultdict
from dataclasses import asdict
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from .ledger import Ledger, Lot, cents
from .risk import RiskState
from .storage import now


def date_only(value):
    """Require an unambiguous ISO date, without silently discarding a time or timezone."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD")
    return pd.Timestamp(value)


def timestamp(value):
    """Require timezone-aware evidence and compare it in the project's trading timezone."""
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise ValueError("Evidence timestamps require an explicit timezone")
    return result.tz_convert("Asia/Shanghai")


def number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid {name}")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"Invalid {name}")
    return value


def integer(value, name, *, positive=False):
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return number(value, name, positive=positive)


def money(value, name):
    number(value, name)
    if abs(cents(value) / 100 - value) > 1e-8:
        raise ValueError(f"{name} must have cent precision")
    return value


def new_account(as_of, account_id):
    """Create a fresh virtual account, with no assertion about a real broker balance."""
    date_only(as_of)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", account_id):
        raise ValueError("Use a short account identifier containing letters, digits, _ or -")
    return {
        "schema_version": 1,
        "account_id": account_id,
        "kind": "paper",
        "created_at": now(),
        "as_of": as_of,
        "initial_date": as_of,
        "initial_cash": 1_000_000.0,
        "cash": 1_000_000.0,
        "lots": [],
        "next_lot_id": 1,
        "receivables": [],
        "entitlements": {},
        "risk": asdict(RiskState()),
        "sessions_completed": 0,
        "parent": None,
    }


def restore_account(account, meta):
    """Reject incomplete holdings or risk history instead of assuming cash or normal risk."""
    if account["schema_version"] != 1 or account["kind"] not in ("paper", "manual"):
        raise ValueError("Unsupported account schema or kind")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", account["account_id"]):
        raise ValueError("Invalid account identifier")
    as_of = date_only(account["as_of"])
    initial = date_only(account["initial_date"])
    if initial > as_of:
        raise ValueError("Account inception is after its valuation date")
    timestamp(account["created_at"])
    if money(account["initial_cash"], "initial cash") != 1_000_000:
        raise ValueError("The registered paper competition starts with 1,000,000 CNY")
    book = Ledger(meta, initial=account["initial_cash"])
    book.cash = cents(money(account["cash"], "cash"))
    ids = set()
    for item in account["lots"]:
        symbol = item["symbol"]
        if symbol not in meta:
            raise ValueError(f"Holding is outside the reviewed universe: {symbol}")
        lot_id = integer(item["id"], "lot id", positive=True)
        quantity = integer(item["quantity"], "quantity", positive=True)
        if lot_id in ids:
            raise ValueError("Duplicate lot identifier")
        ids.add(lot_id)
        bought = date_only(item["bought"])
        available = None if item["available"] is None else date_only(item["available"])
        if not initial <= bought <= as_of or (available is not None and available <= bought):
            raise ValueError("Invalid purchase or T+1 availability date")
        cost = number(item["stop_cost"], "adjusted stop cost", positive=True)
        book.lots.setdefault(symbol, []).append(Lot(lot_id, quantity, bought, available, cost))
    events = set()
    for item in account["receivables"]:
        if item["symbol"] not in meta or item["event_id"] in events:
            raise ValueError("Unknown or duplicate receivable")
        events.add(item["event_id"])
        integer(item["cents"], "receivable cents", positive=True)
        pay, available = item["pay_date"], item["available_date"]
        if pay is not None:
            date_only(pay)
        if available is not None:
            date_only(available)
            if pay is None or available <= pay or available <= account["as_of"]:
                raise ValueError("Receivable availability is inconsistent with the closing account")
        book.receivables.append(dict(item))
    for event_id, rights in account["entitlements"].items():
        seen = set()
        stored = []
        for lot_id, quantity, cost, bought in rights:
            integer(lot_id, "entitlement lot id", positive=True)
            integer(quantity, "entitled quantity", positive=True)
            number(cost, "entitled stop cost", positive=True)
            if lot_id in seen or not initial <= date_only(bought) <= as_of:
                raise ValueError("Invalid entitlement lot")
            seen.add(lot_id)
            stored.append((lot_id, quantity, cost, date_only(bought)))
        book.entitlements[event_id] = stored
    book.next_id = integer(account["next_lot_id"], "next lot id", positive=True)
    previous_ids = ids | {item[0] for rights in book.entitlements.values() for item in rights}
    if book.next_id <= max(previous_ids, default=0):
        raise ValueError("Next lot identifier would reuse a holding or entitlement identifier")
    state = account["risk"]
    if set(state) != set(asdict(RiskState())) or state["mode"] != "latched":
        raise ValueError("A complete non-recovering risk state is required")
    if state["phase"] not in ("NORMAL", "CAUTION45", "FROZEN20", "HALTED0"):
        raise ValueError("Invalid risk phase")
    number(state["peak"], "original NAV high-water mark", positive=True)
    if state["peak"] < account["initial_cash"] or state["probe_anchor"] is not None:
        raise ValueError("Original high-water mark cannot be reset")
    for key in ("age", "healthy_streak", "observations"):
        integer(state[key], key)
    completed = integer(account["sessions_completed"], "sessions completed")
    if state["observations"] != completed or max(state["age"], state["healthy_streak"]) > completed:
        raise ValueError("Risk observations and completed sessions disagree")
    if completed == 0 and (
        state["phase"] != "NORMAL"
        or book.lots
        or book.receivables
        or book.entitlements
        or book.cash != book.initial
    ):
        raise ValueError("A fresh account must start with the registered cash balance")
    return book, RiskState(**state)


def snapshot_account(book, original, as_of, risk, sessions_completed, parent):
    """Store cent cash, individual availability dates and the original risk high-water mark."""
    return {
        **original,
        "created_at": now(),
        "as_of": as_of,
        "cash": book.cash / 100,
        "lots": [
            {
                "id": lot.id,
                "symbol": symbol,
                "quantity": lot.quantity,
                "bought": str(lot.bought.date()),
                "available": None if lot.available is None else str(lot.available.date()),
                "stop_cost": lot.stop_cost,
            }
            for symbol, lots in book.lots.items()
            for lot in lots
        ],
        "next_lot_id": book.next_id,
        "receivables": book.receivables,
        "entitlements": {
            event_id: [[i, q, c, str(b.date())] for i, q, c, b in rights]
            for event_id, rights in book.entitlements.items()
        },
        "risk": asdict(risk),
        "sessions_completed": sessions_completed,
        "parent": parent,
    }


def audit_fills(opening, result, meta, date, rate, cap):
    """Recompute fees, cash, shares, availability and concentration without ledger helpers."""

    def amount(value):
        return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    cash = amount(opening["cash"])
    quantities, available = defaultdict(int), defaultdict(int)
    for lot in opening["lots"]:
        quantities[lot["symbol"]] += lot["quantity"]
        if lot["available"] is not None and lot["available"] <= date:
            available[lot["symbol"]] += lot["quantity"]
    checks = 0
    prices = result["opening_marks"]
    receivables = sum(r["cents"] for r in opening["receivables"])
    for trade in result["trades"]:
        symbol, quantity, price = trade["symbol"], trade["quantity"], trade["price"]
        if type(quantity) is not int or quantity <= 0 or trade["date"] != date:
            raise ValueError("Paper trade has invalid quantity or date")
        stock = meta[symbol]["kind"] == "stock"
        gross = amount(quantity * price)
        fee = amount(max(5, gross / 100 * 0.0003))
        fee += amount(gross / 100 * 0.00001) if stock else 0
        if trade["side"] == "SELL" and stock:
            fee += amount(gross / 100 * 0.0005)
        impact = amount(gross / 100 * rate)
        if (gross, fee, impact) != tuple(
            amount(trade[k]) for k in ("gross", "fee", "slippage_cost")
        ):
            raise ValueError("Paper trade cost reconciliation failed")
        code = meta[symbol]["code"]
        minimum, step = (
            (200, 1)
            if stock and code.startswith(("688", "689"))
            else ((100, 1) if stock and code.startswith(("920", "8", "4")) else (100, 100))
        )
        if trade["side"] == "SELL":
            if quantity > available[symbol] or (
                quantity < available[symbol] and (quantity < minimum or quantity % step)
            ):
                raise ValueError("Paper sale exceeds available shares or violates lot rules")
            available[symbol] -= quantity
            quantities[symbol] -= quantity
            cash += gross - fee - impact
        elif trade["side"] == "BUY":
            if quantity < minimum or quantity % step:
                raise ValueError("Paper purchase violates lot rules")
            quantities[symbol] += quantity
            cash -= gross + fee + impact
            values = {s: amount(q * prices[s]) for s, q in quantities.items() if q}
            nav = cash + sum(values.values()) + receivables
            sectors = defaultdict(int)
            for s, value in values.items():
                sectors[meta[s]["industry"]] += value
            if (
                any(v > 0.15 * nav + 1e-6 for v in values.values())
                or sum(values.values()) > cap * nav + 1e-6
                or any(v > 0.30 * nav + 1e-6 for v in sectors.values())
            ):
                raise ValueError("Paper purchase breaches post-fee concentration or exposure")
        else:
            raise ValueError("Unknown paper trade side")
        if cash < 0 or cash != amount(trade["cash_after"]):
            raise ValueError("Paper cash balance reconciliation failed")
        checks += 9
    closing = result["opening_account"]
    actual = defaultdict(int)
    for lot in closing["lots"]:
        actual[lot["symbol"]] += lot["quantity"]
    if cash != amount(closing["cash"]) or {s: q for s, q in quantities.items() if q} != dict(
        actual
    ):
        raise ValueError("Paper ending cash or shares do not reconcile")
    return {
        "status": "PASS",
        "checks": checks + 2,
        "scope": "Opening balances after reviewed corporate actions, through all paper fills.",
    }
