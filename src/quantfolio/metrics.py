"""Net returns, costs and exposure statistics."""

import collections

import numpy as np


def episode_stats(episodes):
    """Summarize complete ten-session accounts, excluding partial tails from means."""
    complete = [episode for episode in episodes if episode["complete_10_sessions"]]
    summaries = [episode["summary"] for episode in complete]
    days = [day for episode in complete for day in episode["ledger"]["curve"]]
    returns = np.array([summary["return"] for summary in summaries])
    lower_tail = np.quantile(returns, 0.05)
    return {
        "complete_episodes": len(summaries),
        "partial_episodes": len(episodes) - len(summaries),
        "mean_net_return": float(returns.mean()),
        "median_net_return": float(np.median(returns)),
        "worst_episode": float(returns.min()),
        "best_episode": float(returns.max()),
        "win_rate": float(np.mean(returns > 0)),
        "worst_drawdown": min(summary["max_drawdown"] for summary in summaries),
        "episode_es95": float(returns[returns <= lower_tail].mean()),
        "trades": sum(summary["trades"] for summary in summaries),
        "mean_fees": float(np.mean([summary["fees"] for summary in summaries])),
        "mean_slippage_cost": float(
            np.mean([summary.get("slippage_cost", 0.0) for summary in summaries])
        ),
        "mean_turnover": float(np.mean([summary["turnover"] for summary in summaries])),
        "unresolved_events": sum(summary["unresolved_events"] for summary in summaries),
        "quality_issues": sum(summary["quality_issues"] for summary in summaries),
        "rejection_counts": dict(
            sum(
                (collections.Counter(summary["rejection_counts"]) for summary in summaries),
                collections.Counter(),
            )
        ),
        "solver_counts": dict(
            sum(
                (collections.Counter(summary["solver_counts"]) for summary in summaries),
                collections.Counter(),
            )
        ),
        "mean_exposure": float(np.mean([day["holdings_value"] / day["nav"] for day in days])),
        "cash_only_days": sum(day["holdings_value"] == 0 for day in days),
        "total_full_episode_days": len(days),
        "days_beyond_8pct_alert": sum(day.get("risk_level", 0) >= 3 for day in days),
        "target_exposure_breach_days": sum(day.get("exposure_above_target", False) for day in days),
        "probe_entries": sum(episode["summary"]["probe_entries"] for episode in complete),
        "probe_failures": sum(episode["summary"]["probe_failures"] for episode in complete),
        "episodes_beyond_8pct": sum(
            episode["summary"]["max_drawdown"] < -0.08 for episode in complete
        ),
    }


def account_stats(ledger, curve, decisions, quality, mode):
    """Summarize one account, including its initial capital in the drawdown calculation."""
    nav = np.r_[1e6, [day["nav"] for day in curve]]
    returns = np.diff(nav) / nav[:-1]
    lower_tail = float(np.quantile(returns, 0.05))
    unresolved_actions = [
        event
        for event in ledger.events
        if event["type"] == "CORPORATE_ACTION"
        and (
            event["unknown_cash_pay_date"]
            or event.get("unknown_cash_available_date", False)
            or event["unknown_share_list_date"]
            or event["bonus_tax_not_modelled"]
            or event["fractional_shares_discarded"] > 0
        )
    ]
    return {
        "start": curve[0]["date"],
        "end": curve[-1]["date"],
        "sessions": len(curve),
        "return": nav[-1] / 1e6 - 1,
        "max_drawdown": float(np.min(nav / np.maximum.accumulate(nav) - 1)),
        "es95": float(returns[returns <= lower_tail].mean()),
        "fees": sum(trade["fee"] for trade in ledger.trades),
        "slippage_cost": sum(trade["slippage_cost"] for trade in ledger.trades),
        "total_cost": sum(trade["fee"] + trade["slippage_cost"] for trade in ledger.trades),
        "trades": len(ledger.trades),
        "turnover": sum(trade["gross"] for trade in ledger.trades) / 1e6,
        "ending_cash": ledger.cash / 100,
        "ending_receivables": sum(item["cents"] for item in ledger.receivables) / 100,
        "unresolved_events": len(unresolved_actions),
        "quality_issues": len(quality),
        "rejection_counts": dict(collections.Counter(item["reason"] for item in ledger.rejections)),
        "solver_counts": dict(collections.Counter(item["status"] for item in decisions)),
        "mean_exposure": float(np.mean([day["holdings_value"] / day["nav"] for day in curve])),
        "target_exposure_breach_days": sum(day["exposure_above_target"] for day in curve),
        "risk_mode": mode,
        "rule_config_days": dict(
            collections.Counter(decision["rule_config"] for decision in decisions)
        ),
        "phase_days": dict(collections.Counter(day["risk_phase"] for day in curve)),
        "recovery_transitions": [
            {"date": decision["execution_date"], **decision["recovery"]}
            for decision in decisions
            if decision["recovery"]["changed"]
        ],
        "probe_entries": sum(
            decision["recovery"]["changed"] and decision["recovery"]["phase"] == "PROBE20"
            for decision in decisions
        ),
        "probe_failures": sum(
            decision["recovery"]["reason"] in ("PROBE_LOSS_1", "PROBE_DRAWDOWN_7")
            for decision in decisions
        ),
    }
