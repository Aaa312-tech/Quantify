"""Net returns, costs and exposure statistics."""

import collections

import numpy as np


def episode_stats(episodes):
    """Summarize complete ten-session accounts, excluding partial tails from means."""
    complete = [episode for episode in episodes if episode["complete_10_sessions"]]
    full = [episode["summary"] for episode in complete]
    days = [day for episode in complete for day in episode["ledger"]["curve"]]
    v = np.array([x["return"] for x in full])
    worst = np.quantile(v, 0.05)
    return {
        "complete_episodes": len(full),
        "partial_episodes": len(episodes) - len(full),
        "mean_net_return": float(v.mean()),
        "median_net_return": float(np.median(v)),
        "worst_episode": float(v.min()),
        "best_episode": float(v.max()),
        "win_rate": float(np.mean(v > 0)),
        "worst_drawdown": min(x["max_drawdown"] for x in full),
        "episode_es95": float(v[v <= worst].mean()),
        "trades": sum(x["trades"] for x in full),
        "mean_fees": float(np.mean([x["fees"] for x in full])),
        "mean_slippage_cost": float(np.mean([x.get("slippage_cost", 0.0) for x in full])),
        "mean_turnover": float(np.mean([x["turnover"] for x in full])),
        "unresolved_events": sum(x["unresolved_events"] for x in full),
        "quality_issues": sum(x["quality_issues"] for x in full),
        "rejection_counts": dict(
            sum((collections.Counter(x["rejection_counts"]) for x in full), collections.Counter())
        ),
        "solver_counts": dict(
            sum((collections.Counter(x["solver_counts"]) for x in full), collections.Counter())
        ),
        "mean_exposure": float(np.mean([d["holdings_value"] / d["nav"] for d in days])),
        "cash_only_days": sum(d["holdings_value"] == 0 for d in days),
        "total_full_episode_days": len(days),
        "days_beyond_8pct_alert": sum(d.get("risk_level", 0) >= 3 for d in days),
        "target_exposure_breach_days": sum(d.get("exposure_above_target", False) for d in days),
        "probe_entries": sum(e["summary"]["probe_entries"] for e in complete),
        "probe_failures": sum(e["summary"]["probe_failures"] for e in complete),
        "episodes_beyond_8pct": sum(e["summary"]["max_drawdown"] < -0.08 for e in complete),
    }


def account_stats(ledger, curve, decisions, quality, mode):
    """Summarize one account, including its initial capital in the drawdown calculation."""
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
    return {
        "start": curve[0]["date"],
        "end": curve[-1]["date"],
        "sessions": len(curve),
        "return": navs[-1] / 1e6 - 1,
        "max_drawdown": float(np.min(navs / np.maximum.accumulate(navs) - 1)),
        "es95": float(daily[daily <= worst].mean()),
        "fees": sum(t["fee"] for t in ledger.trades),
        "slippage_cost": sum(t["slippage_cost"] for t in ledger.trades),
        "total_cost": sum(t["fee"] + t["slippage_cost"] for t in ledger.trades),
        "trades": len(ledger.trades),
        "turnover": sum(t["gross"] for t in ledger.trades) / 1e6,
        "ending_cash": ledger.cash / 100,
        "ending_receivables": sum(item["cents"] for item in ledger.receivables) / 100,
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
