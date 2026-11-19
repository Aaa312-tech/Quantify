"""Portfolio statistics and paired circular-block uncertainty intervals."""

from __future__ import annotations

import collections

import numpy as np


def paired_interval(difference, confidence=0.9875, draws=5000):
    """Estimate a paired-mean interval using fixed-seed circular blocks of two episodes."""
    values = np.asarray(difference, dtype=float)
    n = len(values)
    if n < 2:
        return {
            "mean": float(values.mean()) if n else None,
            "lower": None,
            "upper": None,
            "episodes": n,
        }
    rng = np.random.default_rng(20261119)
    starts = rng.integers(0, n, (draws, (n + 1) // 2))
    indices = np.stack([starts, (starts + 1) % n], axis=2).reshape(draws, -1)[:, :n]
    means = values[indices].mean(axis=1)
    alpha = (1 - confidence) / 2
    return {
        "mean": float(values.mean()),
        "lower": float(np.quantile(means, alpha)),
        "upper": float(np.quantile(means, 1 - alpha)),
        "episodes": n,
        "confidence": confidence,
        "block_episodes": 2,
        "draws": draws,
        "seed": 20261119,
    }


def aggregate(eps):
    """Summarize complete ten-session accounts, excluding partial tails from means."""
    full = [x["summary"] for x in eps if x["complete_10_sessions"]]
    v = np.array([x["return"] for x in full])
    worst = np.quantile(v, 0.05)
    return {
        "complete_episodes": len(full),
        "partial_episodes": len(eps) - len(full),
        "mean_net_return": float(v.mean()),
        "median_net_return": float(np.median(v)),
        "worst_episode": float(v.min()),
        "best_episode": float(v.max()),
        "win_rate": float(np.mean(v > 0)),
        "worst_drawdown": min(x["max_drawdown"] for x in full),
        "episode_es95": float(v[v <= worst].mean()),
        "trades": sum(x["trades"] for x in full),
        "mean_fees": float(np.mean([x["fees"] for x in full])),
        "mean_turnover": float(np.mean([x["turnover"] for x in full])),
        "unresolved_events": sum(x["unresolved_events"] for x in full),
        "quality_issues": sum(x["quality_issues"] for x in full),
        "rejection_counts": dict(
            sum((collections.Counter(x["rejection_counts"]) for x in full), collections.Counter())
        ),
        "solver_counts": dict(
            sum((collections.Counter(x["solver_counts"]) for x in full), collections.Counter())
        ),
    }


def additional_stats(eps):
    """Summarize realized exposure and drawdown-alert days from account ledgers."""
    full = [e for e in eps if e["complete_10_sessions"]]
    days = [d for e in full for d in e["ledger"]["curve"]]
    return {
        "mean_exposure": float(np.mean([d["holdings_value"] / d["nav"] for d in days])),
        "cash_only_days": sum(d["holdings_value"] == 0 for d in days),
        "total_full_episode_days": len(days),
        "days_beyond_8pct_alert": sum(d.get("risk_level", 0) >= 3 for d in days),
        "target_exposure_breach_days": sum(d.get("exposure_above_target", False) for d in days),
    }


def stats(eps):
    """Combine return, exposure and risk statistics for the retained account schema."""
    full = [e for e in eps if e["complete_10_sessions"]]
    return {
        **aggregate(eps),
        **additional_stats(eps),
        "probe_entries": sum(e["summary"]["probe_entries"] for e in full),
        "probe_failures": sum(e["summary"]["probe_failures"] for e in full),
        "episodes_beyond_8pct": sum(e["summary"]["max_drawdown"] < -0.08 for e in full),
    }
