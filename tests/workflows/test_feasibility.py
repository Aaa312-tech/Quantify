"""A sell-budget repair must preserve hard constraints and ordinary feasible decisions."""

from copy import deepcopy
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import OptimizeResult

from quantfolio.allocation import allocate
from quantfolio.simulation import simulate
from quantfolio.validation import execution_audit, reconcile
from quantfolio.verification import replay_paper_account
from tests.factories import market_bundle

pytestmark = pytest.mark.integration


def inputs():
    bundle, predictions = market_bundle()
    names = list(predictions[5])
    previous = {s: 0.14 for s in names}
    forecasts = {s: {**predictions[5][s], "mu": -0.1} for s in names}
    return dict(
        account=dict(weights=previous, nav=1e6, sellable=previous.copy(), receivable_weight=0.0),
        forecasts=forecasts,
        market=dict(
            meta=bundle["meta"],
            adv={s: 1e9 for s in names},
            returns=bundle["panels"]["ret1"].iloc[:6],
        ),
        exposure=0.9,
        level=0,
    )


def test_all_negative_edges_exit_even_when_sales_exceed_the_normal_budget():
    case = inputs()
    old, before = allocate(**case)
    targets, decision = allocate(**case, turnover="exit_priority")
    assert before["status"] == "INFEASIBLE_REDUCE_ONLY" and old == case["account"]["weights"]
    assert decision["status"] == "OPTIMAL" and all(w == 0 for w in targets.values())
    proof = decision["feasibility"]
    assert proof["original_sell_budget"] == 0.5
    assert proof["minimum_selling"] == pytest.approx(0.84)
    assert proof["effective_sell_budget"] == pytest.approx(0.84 + 1e-9, abs=1e-12)


def test_a_required_exit_does_not_expand_the_buy_budget_or_position_limits():
    case = inputs()
    new = [f"sh60001{i}" for i in range(6)]
    for symbol in new:
        case["market"]["meta"][symbol] = {"kind": "stock", "industry": symbol}
        case["market"]["adv"][symbol] = 1e9
    case["forecasts"] = {
        s: {"rank": 1 - i * 0.01, "mu": 0.05, "margin": 0.0} for i, s in enumerate(new)
    }
    case["market"]["returns"] = pd.DataFrame(
        np.random.default_rng(7).normal(0, 0.01, (60, 12)),
        columns=list(case["account"]["weights"]) + new,
    )
    targets, decision = allocate(**case, turnover="exit_priority")
    assert all(targets[s] == 0 for s in case["account"]["weights"])
    assert sum(targets[s] for s in new) == pytest.approx(0.5)
    assert max(targets.values()) <= 0.14 + 1e-9
    assert decision["rule_activity"]["buy_budget_binding"]


def test_t1_and_liquidity_conflicts_do_not_become_fictitious_feasible_sales():
    for unavailable in ("sellable", "adv"):
        case = inputs()
        case["exposure"] = 0.6
        case["account" if unavailable == "sellable" else "market"][unavailable] = dict.fromkeys(
            case["account"]["weights"], 0.0
        )
        old, _ = allocate(**case)
        targets, decision = allocate(**case, turnover="exit_priority")
        assert targets == old
        assert decision["status"] == "INFEASIBLE_REDUCE_ONLY"
        assert not decision["feasibility"]["hard_constraints_feasible"]
        assert "effective_sell_budget" not in decision["feasibility"]


@pytest.mark.parametrize("status", [1, 3, 4])
def test_solver_failure_is_not_evidence_for_relaxing_constraints(status):
    with patch(
        "quantfolio.allocation.linprog", return_value=OptimizeResult(success=False, status=status)
    ) as solve:
        _, decision = allocate(**inputs(), turnover="exit_priority")
    assert solve.call_count == 1
    assert "feasibility" not in decision and decision["solver_status"] == status


def test_feasible_portfolios_keep_identical_targets_and_objective():
    case = inputs()
    case["account"]["weights"] = {s: 0.04 for s in case["account"]["weights"]}
    case["account"]["sellable"] = case["account"]["weights"].copy()
    old, before = allocate(**case)
    targets, decision = allocate(**case, turnover="exit_priority")
    decision.pop("turnover_policy")
    assert before["status"] == "OPTIMAL"
    assert targets == old and decision == before


@pytest.mark.parametrize("cost", [0.001, 0.002, 0.003])
def test_exit_priority_real_fills_reconcile_and_match_the_paper_workflow(cost):
    bundle, forecasts = market_bundle()
    for day in range(10, 30):
        for value in forecasts[day].values():
            value["mu"] = -0.1
    _, original = simulate(deepcopy(bundle), 6, 20, forecasts, slippage=cost)
    summary, ledger = simulate(bundle, 6, 20, forecasts, slippage=cost, turnover="exit_priority")
    assert original["curve"][-1]["holdings_value"] > 0
    assert ledger["curve"][-1]["holdings_value"] == 0
    assert ledger["decisions"][5]["feasibility"]["minimum_selling"] > 0.5
    assert reconcile(ledger, summary, bundle["meta"], [])["status"] == "PASS"
    assert execution_audit(ledger, bundle)["status"] == "PASS"
    expected = {"summary": summary, "ledger": ledger}
    replay, checks = replay_paper_account(
        bundle, forecasts, "baseline", expected, cost, turnover="exit_priority"
    )
    assert replay == expected and checks > 0
