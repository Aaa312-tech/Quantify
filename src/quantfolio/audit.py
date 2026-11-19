"""Independent execution and high-water-mark audit."""

from __future__ import annotations

import pandas as pd

from .risk import CAPS


def execution_audit(ledger, b):
    """Independently inspect preceding-close signals, high-water marks and post-fill limits."""
    errors = []
    checks = 0
    peak = 1e6
    marks = {(x["date"], x["after_fill"]): x for x in ledger["intermediate_weights"]}
    decisions = {d["execution_date"]: d for d in ledger["decisions"]}
    previous_phase = "NORMAL"
    for row, d in zip(ledger["curve"], ledger["decisions"]):
        state = d["recovery"]
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
        if state["changed"] and state["phase"] in ("PROBE20", "RECOVERY45", "NORMAL"):
            checks += 2
            if state["state_age_before"] < 5 or state["healthy_streak_before"] < 3:
                errors.append(["RECOVERY_WITHOUT_CONFIRMATION", row["date"]])
        if state["reason"] == "PROBE_PROFIT_AND_GLOBAL_RECOVERY":
            checks += 2
            if (
                row["prior_close_nav"] / state["probe_anchor_before"] - 1 < 0.01 - 1e-10
                or state["drawdown"] >= 0.05
            ):
                errors.append(["INVALID_PROBE_GRADUATION", row["date"]])
        previous_phase = state["phase"]
    for i, t in enumerate(ledger["trades"]):
        checks += 3
        date = pd.Timestamp(t["date"])
        prior = b["calendar"][b["calendar"].get_loc(date) - 1]
        if t["signal_date"] != str(prior.date()):
            errors.append(["NONPRIOR_SIGNAL", t])
        if t["gross"] > float(b["panels"]["adv20"].at[prior, t["symbol"]]) * 0.01 + 0.02:
            errors.append(["ADV_LIMIT", t])
        if t["cash_after"] < 0:
            errors.append(["NEGATIVE_CASH", t])
        if t["side"] == "BUY":
            checks += 4
            m = marks[t["date"], i + 1]
            if not decisions[t["date"]]["recovery"]["can_buy"]:
                errors.append(["BUY_WHILE_FROZEN", t])
            if m["max_nav_weight"] > 0.15 + 1e-7:
                errors.append(["POSTBUY_SINGLE_CAP", t])
            if m["max_industry_weight"] > 0.30 + 1e-7:
                errors.append(["POSTBUY_SECTOR_CAP", t])
            if m["gross_exposure"] > m["risk_cap"] + 1e-7:
                errors.append(["POSTBUY_TOTAL_CAP", t])
    return {"status": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors}
