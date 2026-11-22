"""Portfolio optimization with separately switchable turnover controls."""

from __future__ import annotations

from typing import TypedDict

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from .funds import concentration_rows, concentration_values, uses_reported_exposure
from .models import Forecast
from .rules import CONFIGS, TURNOVER_POLICIES

TargetWeights = dict[str, float]


class AllocationAccount(TypedDict):
    """Preceding-close NAV in CNY and position, sellable and receivable NAV fractions."""

    weights: TargetWeights
    nav: float
    sellable: dict[str, float]
    receivable_weight: float


class AllocationMarket(TypedDict):
    """Signal-date metadata, average daily turnover in CNY and historical return fractions."""

    meta: dict
    adv: dict[str, float]
    returns: pd.DataFrame


def costs(meta, s, slippage):
    """Return estimated buy and sell cost rates for the instrument type."""
    stock = meta[s]["kind"] == "stock"
    return 0.0003 + (0.00001 if stock else 0) + slippage, 0.0003 + (
        0.00051 if stock else 0
    ) + slippage


def buffered_weights(target, previous, selected, meta, cap, *, slippage=None):
    """Keep small weight differences only when all portfolio constraints remain feasible."""
    out = dict(target)
    count = 0
    if sum(previous.values()) > cap + 1e-9:
        return out, count
    for s in selected:
        old = previous.get(s, 0.0)
        width = (
            0.02 if slippage is None else min(0.02, max(0.005, 2 * sum(costs(meta, s, slippage))))
        )
        if not old or abs(out.get(s, 0.0) - old) > width or old > 0.14:
            continue
        proposed = {**out, s: old}
        if sum(proposed.values()) > cap + 1e-9:
            continue
        if any(
            sum(w for x, w in proposed.items() if meta[x]["industry"] == sector) > 0.28 + 1e-9
            for sector in {meta[x]["industry"] for x in proposed}
        ):
            continue
        if uses_reported_exposure(meta) and any(
            value > 0.28 + 1e-9 for value in concentration_values(meta, proposed).values()
        ):
            continue
        out = proposed
        count += 1
    return out, count


def select_names(meta, forecasts, previous, returns, slippage, config="baseline", maximum=6):
    """Choose eligible names with independently switchable rank and replacement rules."""
    cfg = CONFIGS[config]
    ordered = sorted(forecasts, key=lambda s: (-forecasts[s]["rank"], s))
    ordinal = {s: i + 1 for i, s in enumerate(ordered)}
    edge = {s: v["mu"] - v["margin"] for s, v in forecasts.items()}
    audit = {
        "retained_beyond12": 0,
        "replacements": 0,
        "blocked_by_count": 0,
        "blocked_by_rank_gap": 0,
    }

    def compatible(s, chosen):
        if sum(meta[x]["industry"] == meta[s]["industry"] for x in chosen) >= 2:
            return False
        return not chosen or not (returns[chosen].corrwith(returns[s]) > 0.85).any()

    chosen: list[str] = []
    retain = 18 if cfg["retain18"] else 12
    for s in sorted(previous, key=lambda x: (ordinal.get(x, 10**9), x)):
        if (
            s in edge
            and ordinal[s] <= retain
            and edge[s] > costs(meta, s, slippage)[1]
            and compatible(s, chosen)
        ):
            chosen.append(s)
            audit["retained_beyond12"] += int(ordinal[s] > 12)
        if len(chosen) == maximum:
            break
    for s in ordered:
        if s in chosen:
            continue
        buy, sell = costs(meta, s, slippage)
        if edge[s] <= buy + sell:
            continue
        if len(chosen) < maximum:
            if compatible(s, chosen):
                chosen.append(s)
        else:
            weakest = min(chosen, key=lambda x: edge[x])
            others = [x for x in chosen if x != weakest]
            if edge[s] - edge[weakest] <= buy + costs(meta, weakest, slippage)[
                1
            ] + 0.001 or not compatible(s, others):
                continue
            count_block = cfg["replace1"] and audit["replacements"] >= 1
            gap_block = (
                cfg["rank_gap3"] and forecasts[s]["rank"] - forecasts[weakest]["rank"] < 0.03
            )
            audit["blocked_by_count"] += int(count_block)
            audit["blocked_by_rank_gap"] += int(gap_block)
            if not count_block and not gap_block:
                chosen = others + [s]
                audit["replacements"] += 1
    return chosen, audit


def _constraints(meta, names, cost_rates, cap, receivable_weight, budget):
    """Build ordered holding, cash, concentration and turnover constraints."""
    n = len(names)
    eq = np.zeros((n, 3 * n))
    for i in range(n):
        eq[i, i] = 1
        eq[i, n + i] = -1
        eq[i, 2 * n + i] = 1
    A = []
    rhs = []

    def add(values, upper):
        row = np.zeros(3 * n)
        for k, v in values.items():
            row[k] = v
        A.append(row)
        rhs.append(upper)

    add({i: 1 for i in range(n)}, cap)
    cash = {i: 1 for i in range(n)}
    cash.update({n + i: cost_rates[s][0] for i, s in enumerate(names)})
    cash.update({2 * n + i: cost_rates[s][1] for i, s in enumerate(names)})
    add(cash, max(0.0, 1 - receivable_weight))
    if uses_reported_exposure(meta):
        for row in concentration_rows(meta, names).values():
            add({i: row.get(s, 0.0) for i, s in enumerate(names)}, 0.28)
    else:
        for sector in {meta[s]["industry"] for s in names}:
            add({i: 1 for i, s in enumerate(names) if meta[s]["industry"] == sector}, 0.28)
    if budget is not None:
        add({n + i: 1 for i in range(n)}, budget)
        add({2 * n + i: 1 for i in range(n)}, budget)
    return eq, A, rhs


def allocate(
    account: AllocationAccount,
    forecasts: dict[str, Forecast],
    market: AllocationMarket,
    *,
    exposure: float,
    level: int,
    slippage: float = 0.001,
    config: str = "baseline",
    turnover: str = "legacy",
    allow_probe: bool = False,
) -> tuple[TargetWeights, dict]:
    """Optimize target weights under cash, liquidity, concentration and turnover constraints."""
    previous, nav, sellable = account["weights"], account["nav"], account["sellable"]
    receivable_weight = account["receivable_weight"]
    meta, adv, returns = market["meta"], market["adv"], market["returns"]
    cfg = CONFIGS[config]
    if turnover not in TURNOVER_POLICIES:
        raise ValueError("Unknown turnover policy")
    cap = min(exposure, [0.9, 0.45, 0.2, 0.0][level])
    if level == 3:
        return {s: 0.0 for s in previous}, {
            "status": "RISK_EXIT",
            "cap": cap,
            "level": level,
            "selected": [],
            "rule_config": config,
        }
    selected, audit = (
        select_names(meta, forecasts, previous, returns, slippage, config)
        if level < 2 or allow_probe
        else (list(previous), {})
    )
    names = list(dict.fromkeys(selected + list(previous)))
    n = len(names)
    common: dict = {
        "cap": cap,
        "level": level,
        "selected": selected,
        "rule_config": config,
        "rule_activity": audit,
    }
    if turnover != "legacy":
        common["turnover_policy"] = turnover
    if not n:
        return {}, {**common, "status": "CASH_NO_POSITIVE_EDGE"}
    objective = np.zeros(3 * n)
    bounds = []
    old = np.array([previous.get(s, 0.0) for s in names])
    buys = []
    sells = []
    penalty = 0.001 if cfg["penalty10"] else 0.0
    penalties = {}
    cost_rates = {s: costs(meta, s, slippage) for s in names}
    for i, s in enumerate(names):
        buy, sell = cost_rates[s]
        extra = (buy + sell) / 2 if turnover == "cost_penalty" else penalty
        penalties[s] = extra
        v = forecasts.get(s, {"mu": -0.05, "margin": 0})
        objective[i] = -v["mu"] + v["margin"] + sell
        objective[n + i] = buy + extra
        objective[2 * n + i] = sell + extra
        limit = float(adv.get(s, 0) or 0) * 0.01 / max(nav, 1)
        if not np.isfinite(limit):
            limit = 0.0
        max_sell = min(old[i], max(0.0, sellable.get(s, 0)), max(0.0, limit))
        forced = old[i] - max_sell
        bounds.append((0.0, max(forced, 0.14 if s in selected else 0.0)))
        buys.append((0.0, max(0.0, limit) if s in selected and (level < 2 or allow_probe) else 0.0))
        sells.append((0.0, max_sell))
    bounds += buys + sells
    budget = None
    if previous and level == 0:
        budget = 0.25 if cfg["normal_budget25"] else 0.50
    elif previous and level == 1 and cfg["caution_budget25"]:
        budget = 0.25
    eq, A, rhs = _constraints(meta, names, cost_rates, cap, receivable_weight, budget)
    problem = dict(
        A_ub=np.array(A),
        b_ub=np.array(rhs),
        A_eq=eq,
        b_eq=old,
        bounds=bounds,
        method="highs",
    )
    result = linprog(objective, **problem)
    sell_budget = budget
    if turnover == "exit_priority" and result.status == 2 and budget is not None:
        # The final inequality is the sell budget. All other constraints remain binding.
        minimum = linprog(
            problem["A_ub"][-1],
            **{**problem, "A_ub": problem["A_ub"][:-1], "b_ub": problem["b_ub"][:-1]},
        )
        if not minimum.success and minimum.status != 2:
            raise ValueError("Cannot establish the minimum feasible sell budget")
        common["feasibility"] = {
            "original_sell_budget": budget,
            "hard_constraints_feasible": bool(minimum.success),
            "minimum_selling": float(minimum.fun) if minimum.success else None,
        }
        if minimum.success:
            if minimum.fun <= budget + 1e-7:
                raise ValueError("Solver feasibility results disagree; do not relax constraints")
            sell_budget = float(minimum.fun) + 1e-9
            rhs[-1] = sell_budget
            problem["b_ub"] = np.array(rhs)
            result = linprog(objective, **problem)
            if not result.success:
                raise ValueError("The minimally relaxed sell budget did not restore feasibility")
            common["feasibility"]["effective_sell_budget"] = sell_budget
    if not result.success:
        scale = min(1.0, cap / max(sum(previous.values()), 1e-12))
        return {s: w * scale for s, w in previous.items()}, {
            **common,
            "status": "INFEASIBLE_REDUCE_ONLY",
            "solver_status": int(result.status),
        }
    x = result.x
    if not (
        np.all(np.array(A) @ x <= np.array(rhs) + 1e-7) and np.max(np.abs(eq @ x - old)) < 1e-7
    ):
        raise ValueError("Optimizer returned weights that violate portfolio constraints")
    targets = {s: max(0.0, float(x[i])) for i, s in enumerate(names)}
    buffered = 0
    if (cfg["deadband2"] or turnover == "cost_band") and (level < 2 or allow_probe):
        targets, buffered = buffered_weights(
            targets,
            previous,
            selected,
            meta,
            cap,
            slippage=slippage if turnover == "cost_band" else None,
        )
    audit.update(
        buffered_positions=buffered,
        buy_budget_binding=budget is not None and x[n : 2 * n].sum() >= budget - 1e-7,
        sell_budget_binding=sell_budget is not None and x[2 * n :].sum() >= sell_budget - 1e-7,
        extra_penalty_rate=penalty if turnover != "cost_penalty" else None,
    )
    if turnover == "cost_penalty":
        audit["extra_penalty_rates"] = penalties
    return targets, {
        **common,
        "status": "OPTIMAL",
        "positive_targets": sum(w > 0.0001 for w in targets.values()),
        "predicted_net_objective": -float(result.fun),
        "turnover_budget": budget,
    }
