"""Portfolio optimization with separately switchable turnover controls."""

from __future__ import annotations

import numpy as np
from scipy.optimize import linprog

from .rules import CONFIGS


def costs(meta, s, slippage):
    """Return estimated buy and sell cost rates for the instrument type."""
    stock = meta[s]["kind"] == "stock"
    return 0.0003 + (0.00001 if stock else 0) + slippage, 0.0003 + (
        0.00051 if stock else 0
    ) + slippage


def buffered_weights(target, previous, selected, meta, cap):
    """Keep small weight differences only when all portfolio constraints remain feasible."""
    out = dict(target)
    count = 0
    if sum(previous.values()) > cap + 1e-9:
        return out, count
    for s in selected:
        old = previous.get(s, 0.0)
        if not old or abs(out.get(s, 0.0) - old) > 0.02 or old > 0.14:
            continue
        proposed = {**out, s: old}
        if sum(proposed.values()) > cap + 1e-9:
            continue
        if any(
            sum(w for x, w in proposed.items() if meta[x]["industry"] == sector) > 0.28 + 1e-9
            for sector in {meta[x]["industry"] for x in proposed}
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

    chosen = []
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


def allocate(
    meta,
    forecasts,
    previous,
    exposure,
    level,
    adv,
    nav,
    sellable,
    returns,
    slippage=0.001,
    receivable_weight=0.0,
    config="baseline",
):
    """Optimize target weights under cash, liquidity, concentration and turnover constraints."""
    cfg = CONFIGS[config]
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
        if level < 2
        else (list(previous), {})
    )
    names = list(dict.fromkeys(selected + list(previous)))
    n = len(names)
    common = {
        "cap": cap,
        "level": level,
        "selected": selected,
        "rule_config": config,
        "rule_activity": audit,
    }
    if not n:
        return {}, {**common, "status": "CASH_NO_POSITIVE_EDGE"}
    objective = np.zeros(3 * n)
    bounds = []
    old = np.array([previous.get(s, 0.0) for s in names])
    buys = []
    sells = []
    penalty = 0.001 if cfg["penalty10"] else 0.0
    for i, s in enumerate(names):
        buy, sell = costs(meta, s, slippage)
        v = forecasts.get(s, {"mu": -0.05, "margin": 0})
        objective[i] = -v["mu"] + v["margin"] + sell
        objective[n + i] = buy + penalty
        objective[2 * n + i] = sell + penalty
        limit = float(adv.get(s, 0) or 0) * 0.01 / max(nav, 1)
        if not np.isfinite(limit):
            limit = 0.0
        max_sell = min(old[i], max(0.0, sellable.get(s, 0)), max(0.0, limit))
        forced = old[i] - max_sell
        bounds.append((0.0, max(forced, 0.14 if s in selected else 0.0)))
        buys.append((0.0, max(0.0, limit) if s in selected and level < 2 else 0.0))
        sells.append((0.0, max_sell))
    bounds += buys + sells
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
    cash.update({n + i: costs(meta, s, slippage)[0] for i, s in enumerate(names)})
    cash.update({2 * n + i: costs(meta, s, slippage)[1] for i, s in enumerate(names)})
    add(cash, max(0.0, 1 - receivable_weight))
    for sector in {meta[s]["industry"] for s in names}:
        add({i: 1 for i, s in enumerate(names) if meta[s]["industry"] == sector}, 0.28)
    budget = None
    if previous and level == 0:
        budget = 0.25 if cfg["normal_budget25"] else 0.50
    elif previous and level == 1 and cfg["caution_budget25"]:
        budget = 0.25
    if budget is not None:
        add({n + i: 1 for i in range(n)}, budget)
        add({2 * n + i: 1 for i in range(n)}, budget)
    result = linprog(
        objective,
        A_ub=np.array(A),
        b_ub=np.array(rhs),
        A_eq=eq,
        b_eq=old,
        bounds=bounds,
        method="highs",
    )
    if not result.success:
        scale = min(1.0, cap / max(sum(previous.values()), 1e-12))
        return {s: w * scale for s, w in previous.items()}, {
            **common,
            "status": "INFEASIBLE_REDUCE_ONLY",
            "solver_status": int(result.status),
        }
    x = result.x
    assert np.all(np.array(A) @ x <= np.array(rhs) + 1e-7) and np.max(np.abs(eq @ x - old)) < 1e-7
    targets = {s: max(0.0, float(x[i])) for i, s in enumerate(names)}
    buffered = 0
    if cfg["deadband2"] and level < 2:
        targets, buffered = buffered_weights(targets, previous, selected, meta, cap)
    audit.update(
        buffered_positions=buffered,
        buy_budget_binding=budget is not None and x[n : 2 * n].sum() >= budget - 1e-7,
        sell_budget_binding=budget is not None and x[2 * n :].sum() >= budget - 1e-7,
        extra_penalty_rate=penalty,
    )
    return targets, {
        **common,
        "status": "OPTIMAL",
        "positive_targets": sum(w > 0.0001 for w in targets.values()),
        "predicted_net_objective": -float(result.fun),
        "turnover_budget": budget,
    }
