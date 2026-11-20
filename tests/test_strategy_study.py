"""Registered research changes must retain causal decisions and irreversible 8% exits."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from conftest import fixture

from quantfolio.allocation import buffered_weights
from quantfolio.models import factor_check, industry_holdout_check
from quantfolio.risk import RiskState
from quantfolio.selection import assess, select_model
from quantfolio.simulation import simulate
from quantfolio.study import COMBINATIONS, choose_inner_policy, combination_eligible
from quantfolio.validation import execution_audit, reconcile


def test_positive_adoption_rejects_a_qualifying_but_negative_score():
    base = {
        "returns": [0.0] * 6,
        "worst_drawdown": -0.075,
        "unresolved_events": 0,
        "quality_issues": 0,
        "accounting_pass": True,
        "continuous": {
            "return": 0.20,
            "max_drawdown": -0.075,
            "unresolved_events": 0,
            "quality_issues": 0,
        },
        "factor_check": {"pass": True},
    }
    candidate = deepcopy(base)
    # This paired pattern passed the older bootstrap gate despite a negative mean-minus-SE.
    candidate["returns"] = [
        -0.07214912,
        0.02239543,
        0.02060889,
        -0.01052682,
        0.04917507,
        0.22097442,
    ]
    original = assess(candidate, base)
    assert original["qualifies"] and original["score"] < 0
    assert not assess(candidate, base, positive_score=True)["qualifies"]
    trials = {
        name: deepcopy(base) for name in ("momentum", "ridge25", "ridge50", "tree25", "tree50")
    }
    trials["ridge50"] = candidate
    assert select_model(deepcopy(trials)) == "ridge50"
    assert select_model(deepcopy(trials), positive_score=True) == "momentum"


def test_inner_policy_ignores_outer_returns_and_cannot_select_recovery():
    def result(score, qualifies=True, outer=0):
        return {
            "tested": True,
            "adoption": {"qualifies": qualifies, "score": score},
            "outer_return": outer,
        }

    candidates = {
        "positive_adoption": result(0.002, outer=-0.2),
        "cost_band": result(0.001, outer=0.5),
        "probe_restore": result(1.0, outer=1.0),
        "stable_selection": result(0.002, outer=0.9),
    }
    assert choose_inner_policy(candidates) == "positive_adoption"
    candidates["positive_adoption"]["adoption"]["qualifies"] = False
    assert choose_inner_policy(candidates) == "stable_selection"
    assert choose_inner_policy({"cost_band": result(-0.001)}) == "matched_momentum"


def test_combination_needs_both_eligible_components():
    results = {
        name: {"adoption": {"qualifies": True, "score": 0.001}}
        for name in ("positive_adoption", "industry_holdout")
    }
    assert combination_eligible(COMBINATIONS["stable_selection"], results)
    results["industry_holdout"]["adoption"]["score"] = 0.0
    assert not combination_eligible(COMBINATIONS["stable_selection"], results)


@pytest.mark.parametrize("mode", ["staged_restore", "probe_restore"])
def test_staged_recovery_needs_time_health_and_lower_drawdown(mode):
    state = RiskState(mode=mode)
    state.update(1e6, False)
    assert state.update(940000, False)["phase"] == "FROZEN20"
    for _ in range(5):
        assert state.update(950000, True)["phase"] == "FROZEN20"
    result = state.update(960000, True)
    assert result["phase"] == "CAUTION45" and result["peak"] == 1e6
    for _ in range(5):
        assert state.update(975000, True)["phase"] == "CAUTION45"
    assert state.update(980000, False)["phase"] == "CAUTION45"
    assert state.update(980000, True)["phase"] == "CAUTION45"
    assert state.update(980000, True)["phase"] == "CAUTION45"
    assert state.update(980000, True)["phase"] == "NORMAL"
    assert state.update(920000, True)["phase"] == "HALTED0"
    for _ in range(20):
        result = state.update(1100000, True)
        assert result["phase"] == "HALTED0" and not result["can_buy"]


def test_probe_is_bounded_by_age_drawdown_and_permanent_halt():
    state = RiskState(mode="probe_restore")
    state.update(1e6, False)
    state.update(940000, False)
    for _ in range(9):
        assert state.update(940000, True)["phase"] == "FROZEN20"
    result = state.update(940000, True)
    assert result["phase"] == "PROBE20" and result["can_buy"]
    assert result["cap"] == 0.2 and result["peak"] == 1e6
    assert result["probe_anchor"] == 940000
    result = state.update(930000, True)
    assert result["phase"] == "FROZEN20" and not result["can_buy"]
    assert result["reason"] == "PROBE_DRAWDOWN_7"
    for _ in range(9):
        assert state.update(940000, True)["phase"] == "FROZEN20"
    assert state.update(940000, True)["phase"] == "PROBE20"
    assert state.update(919000, True)["phase"] == "HALTED0"
    for _ in range(20):
        assert state.update(1200000, True)["phase"] == "HALTED0"


@pytest.mark.parametrize("mode", ["staged_restore", "probe_restore"])
def test_recovery_accounts_pass_independent_checks_and_tampering_fails(mode):
    bundle, forecasts = fixture()
    dates = bundle["calendar"]
    for symbol in forecasts[0]:
        bundle["raws"][symbol].loc[dates[6] :, "close"] = 9.25
        bundle["raws"][symbol].loc[dates[7] :, "open"] = 9.25
        if mode == "staged_restore":
            bundle["raws"][symbol].loc[dates[15] :, ["open", "close"]] = 10.0
    if mode == "probe_restore":
        for day in range(16, 30):
            forecasts[day]["sh510300"] = {"rank": 1.0, "mu": 0.09, "margin": 0.0}
    summary, account = simulate(bundle, 6, 25, forecasts, mode=mode)
    assert reconcile(account, summary, bundle["meta"], [])["status"] == "PASS"
    assert execution_audit(account, bundle)["status"] == "PASS"
    phases = [d["recovery"]["phase"] for d in account["decisions"]]
    assert "FROZEN20" in phases
    assert ("CAUTION45" if mode == "staged_restore" else "PROBE20") in phases
    changed = deepcopy(account)
    changed["decisions"][-1]["recovery"]["healthy_streak_before"] += 1
    audit = execution_audit(changed, bundle)
    assert audit["status"] == "FAIL"
    assert "RISK_EVIDENCE_MISMATCH" in [r[0] for r in audit["errors"]]
    if mode == "probe_restore":
        probe_dates = {
            d["execution_date"] for d in account["decisions"] if d["recovery"]["phase"] == "PROBE20"
        }
        assert any(t["side"] == "BUY" and t["date"] in probe_dates for t in account["trades"])


def test_turnover_band_scales_with_cost_without_blocking_deleveraging():
    meta = {"example": {"kind": "stock", "industry": "example"}}
    old, target = {"example": 0.10}, {"example": 0.109}
    low, _ = buffered_weights(target, old, ["example"], meta, 0.9, slippage=0.001)
    high, _ = buffered_weights(target, old, ["example"], meta, 0.9, slippage=0.003)
    assert low == target and high == old
    reduced, buffered = buffered_weights(
        {"example": 0.07}, old, ["example"], meta, 0.08, slippage=0.003
    )
    assert reduced == {"example": 0.07} and buffered == 0


def test_cost_penalty_is_not_an_additional_cash_charge():
    bundle, forecasts = fixture()
    base, original = simulate(bundle, 6, 6, forecasts)
    result, account = simulate(bundle, 6, 6, forecasts, turnover="cost_penalty")
    assert original["trades"] == account["trades"]
    assert base["total_cost"] == result["total_cost"]
    rates = account["decisions"][0]["rule_activity"]["extra_penalty_rates"]
    assert all(rate == pytest.approx(0.00156) for rate in rates.values())


def test_future_turnover_and_recovery_outcomes_do_not_change_earlier_execution():
    bundle, forecasts = fixture()
    _, before = simulate(bundle, 6, 25, forecasts, mode="probe_restore")
    changed = deepcopy(bundle)
    for raw in changed["raws"].values():
        raw.loc[changed["calendar"][15] :, ["open", "close", "high", "low"]] *= 1.5
    _, after = simulate(
        changed,
        6,
        25,
        forecasts,
        mode="probe_restore",
        turnover={i: "cost_band" for i in range(15, 30)},
    )
    cutoff = str(bundle["calendar"][14].date())
    for section in ("trades", "curve", "decisions"):
        key = "execution_date" if section == "decisions" else "date"
        assert [r for r in before[section] if r[key] <= cutoff] == [
            r for r in after[section] if r[key] <= cutoff
        ]


def industry_sample(dominant=False):
    records, scores = [], []
    for day in range(50):
        for sector in range(6):
            size = 100 if dominant and sector == 0 else 12
            for stock in range(size):
                score = (stock + 0.5) / size
                sign = -1 if dominant and sector != 0 else 1
                records.append(
                    {
                        "date_index": day,
                        "symbol": f"{sector}-{stock}",
                        "industry": f"industry{sector}",
                        "momentum_percentile": 0.9,
                        "y5": sign * 0.02 * (score - 0.5) + sector * 0.002,
                        "end5": day + 6,
                        "status5": 0,
                    }
                )
                scores.append(score)
    table = pd.DataFrame(records)
    return table, np.arange(len(table)), np.asarray(scores)


@pytest.mark.parametrize("dominant", [False, True])
def test_industry_holdouts_detect_dependence_and_do_not_use_unavailable_labels(dominant):
    table, rows, scores = industry_sample(dominant)
    assert factor_check(table, rows, scores, 50)["pass"]
    result = industry_holdout_check(table, rows, scores, 50)
    assert result["pass"] is (not dominant)
    assert len(result["holdouts"]) == 5
    assert all(r["last_label_end"] < 50 for r in result["holdouts"])
    changed = table.copy()
    changed.loc[changed.end5 >= 50, "y5"] = 999.0
    assert industry_holdout_check(changed, rows, scores, 50) == result
