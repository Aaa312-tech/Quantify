"""Validation-only rule and model adoption; no outer-result selection."""

from __future__ import annotations

import numpy as np

from .rules import CONFIGS, complexity


def eligible_rows(table, start, end):
    """Select eligible signal rows within an inclusive historical date-index range."""
    return np.flatnonzero(
        (table.date_index.to_numpy() >= start)
        & (table.date_index.to_numpy() <= end)
        & table.eligible.to_numpy()
    )


def objective(eps):
    """Summarize validation accounts without including any outer test results."""
    full = [x["summary"] for x in eps if x["complete_10_sessions"]]
    ret = np.array([x["return"] for x in full])
    return {
        "returns": ret.tolist(),
        "mean_net_return": float(ret.mean()),
        "worst_drawdown": min(x["max_drawdown"] for x in full),
        "episodes": len(full),
        "trades": sum(x["trades"] for x in full),
        "unresolved_events": sum(x["summary"]["unresolved_events"] for x in eps),
        "quality_issues": sum(x["summary"]["quality_issues"] for x in eps),
    }


def clip_for(table, rows):
    """Estimate a clipping bound from the supplied training labels only."""
    return {"gross_clip": np.quantile(table.iloc[rows].y5.to_numpy(), [0.01, 0.99]).tolist()}


def assess(candidate, base, minimum=0.001):
    """Apply predeclared return, paired-consistency, quality and drawdown adoption gates."""
    diff = np.array(candidate["returns"]) - np.array(base["returns"])
    n = len(diff)
    rng = np.random.default_rng(20261119)
    starts = rng.integers(0, n, (2000, (n + 1) // 2))
    ix = np.stack([starts, (starts + 1) % n], axis=2).reshape(2000, -1)[:, :n]
    lower = float(np.quantile(diff[ix].mean(axis=1), 0.10))
    se = float(diff.std(ddof=1) / np.sqrt(n))
    c = candidate["continuous"]
    b = base["continuous"]
    req = {
        "six_complete_episodes": n == 6,
        "minimum_mean_advantage": diff.mean() >= minimum,
        "wins_at_least_four": int((diff > 0).sum()) >= 4,
        "lower90_positive": lower > 0,
        "episode_dd_within8": candidate["worst_drawdown"] >= -0.08,
        "episode_dd_not_over50bp_worse": candidate["worst_drawdown"]
        >= base["worst_drawdown"] - 0.005,
        "continuous_dd_within8": c["max_drawdown"] >= -0.08,
        "continuous_dd_not_over50bp_worse": c["max_drawdown"] >= b["max_drawdown"] - 0.005,
        "continuous_return_not_over50bp_worse": c["return"] >= b["return"] - 0.005,
        "quality_clear": candidate["unresolved_events"]
        == candidate["quality_issues"]
        == base["unresolved_events"]
        == base["quality_issues"]
        == c["unresolved_events"]
        == c["quality_issues"]
        == b["unresolved_events"]
        == b["quality_issues"]
        == 0,
        "accounting_clear": candidate["accounting_pass"] and base["accounting_pass"],
    }
    return {
        "requirements": req,
        "qualifies": bool(all(req.values())),
        "mean_advantage": float(diff.mean()),
        "winning_episodes": int((diff > 0).sum()),
        "lower90": lower,
        "standard_error": se,
        "score": float(diff.mean() - se),
    }


def select_rules(trials):
    """Choose a qualifying turnover rule from past validation evidence, defaulting to baseline."""
    selected = "baseline"
    best = -np.inf
    for name in sorted((k for k in CONFIGS if k != "baseline"), key=lambda x: (complexity(x), x)):
        a = assess(trials[name], trials["baseline"])
        trials[name]["adoption"] = a
        if a["qualifies"] and a["score"] > best:
            selected = name
            best = a["score"]
    return selected


def select_model(trials):
    """Choose a qualifying ranking model with an industry gate, defaulting to momentum."""
    selected = "momentum"
    best = -np.inf
    for name in ["ridge25", "tree25", "ridge50", "tree50"]:
        a = assess(trials[name], trials["momentum"], 0.002)
        a["requirements"]["industry_factor_check"] = trials[name]["factor_check"]["pass"]
        a["qualifies"] = bool(all(a["requirements"].values()))
        trials[name]["adoption"] = a
        if a["qualifies"] and a["score"] > best:
            selected = name
            best = a["score"]
    return selected
