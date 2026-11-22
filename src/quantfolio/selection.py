"""Model and turnover-rule selection on validation accounts."""

from __future__ import annotations

import numpy as np

from .rules import CONFIGS, complexity


def validation_summary(episodes):
    """Use complete episodes for returns, but include all episodes in quality checks."""
    summaries = [episode["summary"] for episode in episodes if episode["complete_10_sessions"]]
    returns = np.array([summary["return"] for summary in summaries])
    return {
        "returns": returns.tolist(),
        "mean_net_return": float(returns.mean()),
        "worst_drawdown": min(summary["max_drawdown"] for summary in summaries),
        "episodes": len(summaries),
        "trades": sum(summary["trades"] for summary in summaries),
        "unresolved_events": sum(episode["summary"]["unresolved_events"] for episode in episodes),
        "quality_issues": sum(episode["summary"]["quality_issues"] for episode in episodes),
    }


def label_clip_bounds(table, rows):
    """Estimate a clipping bound from the supplied training labels only."""
    return {"gross_clip": np.quantile(table.iloc[rows].y5.to_numpy(), [0.01, 0.99]).tolist()}


def assess(candidate, base, minimum=0.001, *, positive_score=False):
    """Apply predeclared return, paired-consistency, quality and drawdown adoption gates."""
    advantages = np.array(candidate["returns"]) - np.array(base["returns"])
    episode_count = len(advantages)
    rng = np.random.default_rng(20261119)
    starts = rng.integers(0, episode_count, (2000, (episode_count + 1) // 2))
    sample_indices = np.stack([starts, (starts + 1) % episode_count], axis=2).reshape(2000, -1)[
        :, :episode_count
    ]
    lower_bound = float(np.quantile(advantages[sample_indices].mean(axis=1), 0.10))
    standard_error = float(advantages.std(ddof=1) / np.sqrt(episode_count))
    candidate_account = candidate["continuous"]
    baseline_account = base["continuous"]
    requirements = {
        "six_complete_episodes": episode_count == 6,
        "minimum_mean_advantage": advantages.mean() >= minimum,
        "wins_at_least_four": int((advantages > 0).sum()) >= 4,
        "lower90_positive": lower_bound > 0,
        "episode_dd_within8": candidate["worst_drawdown"] >= -0.08,
        "episode_dd_not_over50bp_worse": candidate["worst_drawdown"]
        >= base["worst_drawdown"] - 0.005,
        "continuous_dd_within8": candidate_account["max_drawdown"] >= -0.08,
        "continuous_dd_not_over50bp_worse": candidate_account["max_drawdown"]
        >= baseline_account["max_drawdown"] - 0.005,
        "continuous_return_not_over50bp_worse": candidate_account["return"]
        >= baseline_account["return"] - 0.005,
        "quality_clear": candidate["unresolved_events"]
        == candidate["quality_issues"]
        == base["unresolved_events"]
        == base["quality_issues"]
        == candidate_account["unresolved_events"]
        == candidate_account["quality_issues"]
        == baseline_account["unresolved_events"]
        == baseline_account["quality_issues"]
        == 0,
        "accounting_clear": candidate["accounting_pass"] and base["accounting_pass"],
    }
    if positive_score:
        requirements["positive_mean_minus_standard_error"] = advantages.mean() - standard_error > 0
    return {
        "requirements": requirements,
        "qualifies": bool(all(requirements.values())),
        "mean_advantage": float(advantages.mean()),
        "winning_episodes": int((advantages > 0).sum()),
        "lower90": lower_bound,
        "standard_error": standard_error,
        "score": float(advantages.mean() - standard_error),
    }


def select_rules(trials, *, positive_score=False):
    """Choose a qualifying turnover rule from past validation evidence, defaulting to baseline."""
    selected = "baseline"
    best_score = -np.inf
    for name in sorted((k for k in CONFIGS if k != "baseline"), key=lambda x: (complexity(x), x)):
        adoption = assess(trials[name], trials["baseline"], positive_score=positive_score)
        trials[name]["adoption"] = adoption
        if adoption["qualifies"] and adoption["score"] > best_score:
            selected = name
            best_score = adoption["score"]
    return selected


def select_model(trials, *, positive_score=False):
    """Choose a qualifying ranking model with an industry gate, defaulting to momentum."""
    selected = "momentum"
    best_score = -np.inf
    for name in ["ridge25", "tree25", "ridge50", "tree50"]:
        adoption = assess(trials[name], trials["momentum"], 0.002, positive_score=positive_score)
        adoption["requirements"]["industry_factor_check"] = trials[name]["factor_check"]["pass"]
        adoption["qualifies"] = bool(all(adoption["requirements"].values()))
        trials[name]["adoption"] = adoption
        if adoption["qualifies"] and adoption["score"] > best_score:
            selected = name
            best_score = adoption["score"]
    return selected
