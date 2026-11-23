"""Manual paper-account snapshots and independent opening-fill reconciliation."""

import math
import re
from collections import defaultdict
from dataclasses import asdict
from decimal import ROUND_HALF_UP, Decimal
from typing import NotRequired, TypedDict, cast

import pandas as pd

from .ledger import Ledger, Lot, Receivable, cents
from .risk import RiskSnapshot, RiskState
from .storage import now
from .validation import checked_fund_exposure, checked_industries, fund_exposure_equal


class AccountLot(TypedDict):
    id: int
    symbol: str
    quantity: int
    bought: str
    available: str | None
    stop_cost: float


class AccountSnapshot(TypedDict):
    """Closing JSON account: cash/principal in CNY, lots in shares, receivables in cents."""

    schema_version: int
    account_id: str
    kind: str
    created_at: str
    as_of: str
    initial_date: str
    initial_cash: float
    cash: float
    lots: list[AccountLot]
    next_lot_id: int
    receivables: list[Receivable]
    entitlements: dict[str, list[list[int | float | str]]]
    risk: RiskSnapshot
    sessions_completed: int
    parent: dict[str, str] | None
    valuation: NotRequired[dict]


def date_only(value: object) -> pd.Timestamp:
    """Parse a YYYY-MM-DD date without a time or timezone."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD")
    return pd.Timestamp(value)


def timestamp(value):
    """Parse a zoned timestamp and convert it to Asia/Shanghai."""
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise ValueError("Evidence timestamps require an explicit timezone")
    return result.tz_convert("Asia/Shanghai")


def number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid {name}")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"Invalid {name}")
    return value


def integer(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return cast(int, number(value, name, positive=positive))


def money(value: float, name: str) -> float:
    number(value, name)
    if abs(cents(value) / 100 - value) > 1e-8:
        raise ValueError(f"{name} must have cent precision")
    return value


def new_account(as_of: str, account_id: str) -> AccountSnapshot:
    """Create a cash-only paper account."""
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
        "risk": cast(RiskSnapshot, asdict(RiskState())),
        "sessions_completed": 0,
        "parent": None,
    }


def restore_account(account: AccountSnapshot, meta: dict) -> tuple[Ledger, RiskState]:
    """Validate a closing snapshot and restore its holdings and risk state."""
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
    for receivable in account["receivables"]:
        if receivable["symbol"] not in meta or receivable["event_id"] in events:
            raise ValueError("Unknown or duplicate receivable")
        events.add(receivable["event_id"])
        integer(receivable["cents"], "receivable cents", positive=True)
        pay, cash_available = receivable["pay_date"], receivable["available_date"]
        if pay is not None:
            date_only(pay)
        if cash_available is not None:
            date_only(cash_available)
            if pay is None or cash_available <= pay or cash_available <= account["as_of"]:
                raise ValueError("Receivable availability is inconsistent with the closing account")
        book.receivables.append(cast(Receivable, dict(receivable)))
    for event_id, rights in account["entitlements"].items():
        seen = set()
        stored = []
        for stored_id, shares, stop_cost, purchase_date in rights:
            entitled_id = integer(stored_id, "entitlement lot id", positive=True)
            entitled_quantity = integer(shares, "entitled quantity", positive=True)
            entitled_cost = number(stop_cost, "entitled stop cost", positive=True)
            if entitled_id in seen or not initial <= date_only(purchase_date) <= as_of:
                raise ValueError("Invalid entitlement lot")
            seen.add(entitled_id)
            stored.append((entitled_id, entitled_quantity, entitled_cost, date_only(purchase_date)))
        book.entitlements[event_id] = stored
    book.next_id = integer(account["next_lot_id"], "next lot id", positive=True)
    previous_ids = ids | {item[0] for rights in book.entitlements.values() for item in rights}
    if book.next_id <= max(previous_ids, default=0):
        raise ValueError("Next lot identifier would reuse a holding or entitlement identifier")
    return book, _restore_risk(account, book)


def _restore_risk(account: AccountSnapshot, book: Ledger) -> RiskState:
    """Validate carried risk observations and the fresh-account boundary."""
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
    return RiskState(**state)


def snapshot_account(
    book: Ledger,
    original: AccountSnapshot,
    as_of: str,
    risk: RiskState,
    sessions_completed: int,
    parent: dict[str, str] | None,
) -> AccountSnapshot:
    """Serialize holdings, cash, entitlements and carried risk state."""
    if book.initial != cents(original["initial_cash"]):
        raise ValueError("Ledger principal differs from the original account")
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
        "risk": cast(RiskSnapshot, asdict(risk)),
        "sessions_completed": sessions_completed,
        "parent": parent,
    }


def _check_buy_exposure(meta, groups, values, nav, cap, information_at):
    """Check post-fee capital and reported fund limits independently of execution."""
    sectors: dict[str, float] = defaultdict(int)
    for s, value in values.items():
        if groups[s] is None:
            raise ValueError("Paper purchase has an unknown industry")
        sectors[groups[s]] += value
    if (
        any(v > 0.15 * nav + 1e-6 for v in values.values())
        or sum(values.values()) > cap * nav + 1e-6
        or any(v > 0.30 * nav + 1e-6 for v in sectors.values())
    ):
        raise ValueError("Paper purchase breaches post-fee concentration or exposure")
    if any("fund_history" in instrument for instrument in meta.values()):
        report = checked_fund_exposure(meta, values, nav, information_at)
        if report["max_reported_equity_weight"] > 0.30 + 1e-7:
            raise ValueError("Paper purchase breaches the reported equity proxy cap")


def audit_fills(opening, result, meta, date, rate, cap):
    """Recompute fees, cash, shares, availability and concentration without ledger helpers."""

    def amount(value):
        return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    information_at = (
        opening["as_of"] + "T16:00:00+08:00"
        if result["status"] == "REFERENCE_QUOTE_PREVIEW"
        else date + "T09:25:00+08:00"
    )
    groups = checked_industries(meta, information_at)
    cash = amount(opening["cash"])
    quantities: dict[str, int] = defaultdict(int)
    available: dict[str, int] = defaultdict(int)
    for lot in opening["lots"]:
        quantities[lot["symbol"]] += lot["quantity"]
        if lot["available"] is not None and lot["available"] <= date:
            available[lot["symbol"]] += lot["quantity"]
    if any(groups[symbol] is None for symbol, quantity in quantities.items() if quantity):
        raise ValueError("Paper account has an unknown held industry")
    checks = 0
    prices = result["opening_marks"]
    receivables = sum(r["cents"] for r in opening["receivables"])
    marks = {row["after_fill"]: row for row in result["intermediate_weights"]}
    for index, trade in enumerate(result["trades"], 1):
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
            _check_buy_exposure(meta, groups, values, nav, cap, information_at)
        else:
            raise ValueError("Unknown paper trade side")
        if cash < 0 or cash != amount(trade["cash_after"]):
            raise ValueError("Paper cash balance reconciliation failed")
        if result.get("fund_mode") == "reported":
            values = {s: amount(q * prices[s]) for s, q in quantities.items() if q}
            nav = cash + sum(values.values()) + receivables
            expected = checked_fund_exposure(meta, values, nav, information_at)
            if not fund_exposure_equal(marks[index].get("fund_exposure"), expected):
                raise ValueError("Paper fund exposure mark does not reconcile")
            checks += 1
        checks += 9
    closing = result["opening_account"]
    actual: dict[str, int] = defaultdict(int)
    for lot in closing["lots"]:
        actual[lot["symbol"]] += lot["quantity"]
    if cash != amount(closing["cash"]) or {s: q for s, q in quantities.items() if q} != dict(
        actual
    ):
        raise ValueError("Paper ending cash or shares do not reconcile")
    if result.get("fund_mode") == "reported":
        values = {s: amount(q * prices[s]) for s, q in quantities.items() if q}
        nav = cash + sum(values.values()) + receivables
        expected = checked_fund_exposure(meta, values, nav, information_at, sources=True)
        if not fund_exposure_equal(result.get("fund_exposure"), expected):
            raise ValueError("Paper ending fund exposure does not reconcile")
        checks += 1
    return {
        "status": "PASS",
        "checks": checks + 2,
        "scope": "Opening balances after reviewed corporate actions, through all paper fills.",
    }
