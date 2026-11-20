"""Completed-close regime classification and opening-price proxy execution."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ledger import nearest_tick

EXECUTION_MODEL = "opening_proxy_separate_cost"


def risk_warning(meta, date, *, as_of=None):
    """Return an effective warning using only notices available at the decision time."""
    periods = meta.get("risk_warning_periods", ())
    if not periods:
        return None
    day = pd.Timestamp(date)
    if day.tzinfo is not None:
        day = day.tz_convert("Asia/Shanghai").tz_localize(None)
    day = day.normalize()
    available = (
        day.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
        if as_of is None
        else pd.Timestamp(as_of)
    )
    if pd.isna(day) or pd.isna(available) or available.tzinfo is None:
        raise ValueError("Security status needs a session and timezone-aware information cutoff")
    session = str(day.date())
    for period in periods:
        if period["start"] <= session and pd.Timestamp(period["start_available_at"]) <= available:
            # A later removal notice cannot clear a warning in an earlier decision.
            if session < period["end"] or pd.Timestamp(period["end_available_at"]) > available:
                return period
    return None


def board_limit(m, date, *, as_of=None):
    """Return the daily price-limit assumption for the board and date."""
    warning = risk_warning(m, date, as_of=as_of)
    if warning is not None:
        return warning["limit"]
    code = m["code"]
    if m["kind"] == "etf":
        return m.get("observed_limit", 0.20 if code in ["159915", "588000"] else 0.10)
    if m["symbol"].startswith("bj"):
        return 0.30
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def regime(panels, date):
    """Derive the exposure ceiling from completed-close market breadth and trend."""
    bench = "sh510300"
    breadth = ((panels["trend20"].loc[date] > 0) & panels["eligible"].loc[date]).sum() / max(
        1, panels["eligible"].loc[date].sum()
    )
    above = panels["trend20"].at[date, bench] > 0
    if above and breadth >= 0.55:
        return 0.90, float(breadth), "强势"
    if (not above) and breadth < 0.35:
        return 0.30, float(breadth), "弱势"
    return 0.60, float(breadth), "震荡"


def resumption_reference(raw, date, previous, calendar, status, actions):
    """Return an observed anchor only for a completely documented suspension gap."""
    if calendar is None or not status or status.get("suspended") is not False:
        return None
    try:
        anchor = pd.Timestamp(status.get("reference_date"))
        prior = raw.index[raw.index < date]
        if not len(prior) or pd.isna(anchor) or anchor != prior[-1] or anchor >= previous:
            return None
        sessions = calendar[(calendar > anchor) & (calendar < date)]
        evidence = status.get("suspension_evidence", [])
        if (
            not len(sessions)
            or sessions[-1] != previous
            or [item["date"] for item in evidence] != [str(day.date()) for day in sessions]
        ):
            return None
        for item, day in zip(evidence, sessions):
            known = pd.Timestamp(item["known_at"])
            deadline = day.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=9, minutes=25)
            if pd.isna(known) or known.tzinfo is None or known > deadline:
                return None
        # A missing ex-date bar needs a separate reference-price review.
        if any(str(anchor.date()) < action["ex_date"] < str(date.date()) for action in actions):
            return None
        return anchor
    except (KeyError, TypeError, ValueError):
        return None


def opening_price(
    meta, raw, date, previous, actions=(), side="BUY", *, status=None, as_of=None, calendar=None
):
    """Use only the opening quote and information available by the opening auction.

    Daily volume and the eventual high/low cannot establish opening tradability.
    Without timestamped status or auction depth, a valid quote is only a proxy.
    Execution costs are charged separately by the ledger, never in this price.
    """
    if side not in {"BUY", "SELL"}:
        raise ValueError("Opening side must be BUY or SELL")
    if status is not None:
        known_at = pd.Timestamp(status.get("known_at"))
        opening_time = pd.Timestamp(date).tz_localize("Asia/Shanghai") + pd.Timedelta(
            hours=9, minutes=25
        )
        if pd.isna(known_at) or known_at.tzinfo is None or known_at > opening_time:
            return None, "STATUS_NOT_AVAILABLE_AT_OPEN"
        if not isinstance(status.get("suspended"), bool):
            return None, "INVALID_OPENING_STATUS"
        if status["suspended"]:
            return None, "SUSPENDED_AT_OPEN"
    if side == "BUY" and risk_warning(meta, date, as_of=as_of) is not None:
        return None, "RISK_WARNING_BUY_EXCLUDED"
    if date not in raw.index:
        return None, "NO_QUOTE"
    reference_date = previous
    if previous not in raw.index:
        reference_date = (
            resumption_reference(raw, date, previous, calendar, status, actions)
            if side == "SELL"
            else None
        )
        if reference_date is None:
            return None, "NO_QUOTE"
    old = float(raw.at[reference_date, "close"])
    o = float(raw.at[date, "open"])
    if not np.isfinite(o) or o <= 0 or not np.isfinite(old) or old <= 0:
        return None, "INVALID_OPENING_REFERENCE"
    today = [a for a in actions if a["ex_date"] == str(date.date())]
    ref = old
    for a in today:
        ref = (ref - a.get("ex_reference_cash_per_share", a["cash_per_share"])) / a.get(
            "ex_reference_share_multiplier", a["share_multiplier"]
        )
        if a.get("price_limit_reference") is not None:
            ref = a["price_limit_reference"]
    tick = 0.001 if meta["kind"] == "etf" else 0.01
    if not np.isfinite(ref) or ref <= 0:
        return None, "INVALID_OPENING_REFERENCE"
    if abs(o - nearest_tick(o, tick)) > tick * 1e-6:
        return None, "OFF_TICK_OPENING_QUOTE"
    lim = board_limit(meta, date, as_of=as_of)
    upper = nearest_tick(ref * (1 + lim), tick)
    lower = nearest_tick(ref * (1 - lim), tick)
    if not lower <= o <= upper:
        return None, "OPEN_OUTSIDE_ASSUMED_LIMIT"
    if side == "BUY" and o >= upper - tick * 0.51:
        return None, "OPEN_AT_UPPER_LIMIT"
    if side == "SELL" and o <= lower + tick * 0.51:
        return None, "OPEN_AT_LOWER_LIMIT"
    return o, None


def gross_entitlement(entry, exit_date, exit_price, actions):
    """Continuous base-share gross wealth, not an integer order or a reinvested index.

    End-of-record-day entitlement matters. Cash receivables retain gross value even
    if their payment follows exit. Complex overlapping record/ex schedules are
    explicitly censored rather than approximated silently.
    """
    en = str(entry.date())
    ex = str(exit_date.date())
    quantity = 1.0
    cash = 0.0
    latest_listing = None
    relevant = []
    for a in actions:
        if en < a["ex_date"] <= ex and not a.get("record_date"):
            return None, None, "UNKNOWN_RECORD_DATE"
        record = a.get("record_date")
        if record and en <= record < ex and a["ex_date"] <= ex:
            relevant.append(a)
    last_ex = None
    for a in sorted(relevant, key=lambda x: x["ex_date"]):
        if last_ex and a["record_date"] < last_ex:
            return None, None, "OVERLAPPING_RECORD_SCHEDULE"
        before = quantity
        cash += before * float(a["cash_per_share"])
        quantity += before * (float(a["share_multiplier"]) - 1)
        if abs(float(a["share_multiplier"]) - 1) > 1e-10:
            if not a.get("share_list_date"):
                return None, None, "UNKNOWN_SHARE_LIST_DATE"
            listing = pd.Timestamp(a["share_list_date"])
            latest_listing = max(latest_listing, listing) if latest_listing is not None else listing
        last_ex = a["ex_date"]
    return quantity * exit_price + cash, latest_listing, None


LABEL_STATUS = {
    "OK": 0,
    "INELIGIBLE": 1,
    "NO_ENTRY": 2,
    "RIGHT_CENSORED": 3,
    "ACTION_UNRESOLVED": 4,
}


def legal_label(meta, raw, calendar, index, horizon, actions, *, opening_status=None):
    """Compute a forward wealth label only after a legal entry and complete legal exit."""
    entry_index = index + 1
    target = entry_index + horizon
    if target >= len(calendar):
        return np.nan, -1, LABEL_STATUS["RIGHT_CENSORED"], 0
    entry = calendar[entry_index]
    statuses = opening_status or {}
    buy, reason = opening_price(
        meta,
        raw,
        entry,
        calendar[index],
        actions,
        "BUY",
        status=statuses.get(str(entry.date()), {}).get(meta["symbol"]),
        calendar=calendar,
    )
    if reason:
        return np.nan, -1, LABEL_STATUS["NO_ENTRY"], 0
    for j in range(target, len(calendar)):
        exit_date = calendar[j]
        sell, reason = opening_price(
            meta,
            raw,
            exit_date,
            calendar[j - 1],
            actions,
            "SELL",
            status=statuses.get(str(exit_date.date()), {}).get(meta["symbol"]),
            calendar=calendar,
        )
        if reason:
            continue
        wealth, listing, problem = gross_entitlement(entry, exit_date, sell, actions)
        if problem:
            return np.nan, -1, LABEL_STATUS["ACTION_UNRESOLVED"], j - target
        if listing is not None and exit_date < listing:
            continue
        return wealth / buy - 1, j, LABEL_STATUS["OK"], j - target
    return np.nan, -1, LABEL_STATUS["RIGHT_CENSORED"], len(calendar) - target
