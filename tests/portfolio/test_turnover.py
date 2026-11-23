import copy
import unittest

import numpy as np
import pandas as pd
import pytest

from quantfolio.allocation import allocate, select_names
from quantfolio.rules import CONFIGS, RULES, config_for
from quantfolio.selection import assess, select_model, select_rules
from quantfolio.simulation import execution_targets, simulate
from quantfolio.validation import execution_audit, reconcile
from tests.factories import market_bundle


def selection_fixture():
    syms = [f"s{i:02d}" for i in range(20)]
    meta = {s: {"industry": s, "kind": "stock"} for s in syms}
    ret = pd.DataFrame(np.random.default_rng(42).normal(0, 0.01, (60, 20)), columns=syms)
    fc = {s: {"rank": 1 - i * 0.01, "mu": 0.05, "margin": 0.0} for i, s in enumerate(syms)}
    return (syms, meta, ret, fc)


@pytest.mark.parametrize(
    "planned,halted,stopped,expected",
    [
        (True, False, [], [("held", 100_000.0), ("new", 50_000.0), ("exit", 0.0)]),
        (False, False, [], [("exit", 150_000.0), ("held", 120_000.0)]),
        (True, False, ["held"], [("held", 0.0), ("new", 50_000.0), ("exit", 0.0)]),
        (False, False, ["exit"], [("exit", 0.0), ("held", 120_000.0)]),
        (True, True, ["new"], [("exit", 0.0), ("held", 0.0)]),
        (False, True, [], [("exit", 0.0), ("held", 0.0)]),
    ],
    ids=["rebalance", "hold", "stop_on_rebalance", "stop_between_rebalances", "halt", "hold_halt"],
)
def test_opening_targets_preserve_exits_stops_and_order(planned, halted, stopped, expected):
    weights = {"held": 0.10, "new": 0.05}
    positions = {"exit": 150_000.0, "held": 120_000.0}
    targets = execution_targets(
        weights, positions, 1_000_000.0, stopped, planned=planned, halted=halted
    )
    assert list(targets.items()) == expected
    assert weights == {"held": 0.10, "new": 0.05}
    assert positions == {"exit": 150_000.0, "held": 120_000.0}


class RuleTests(unittest.TestCase):
    def test_sixteen_independent_prespecified_configs(self):
        self.assertEqual(len(CONFIGS), 16)
        self.assertEqual(len({tuple(x.values()) for x in CONFIGS.values()}), 16)
        for r in RULES:
            self.assertEqual([k for k, v in CONFIGS["add_" + r].items() if v], [r])
            self.assertEqual([k for k, v in CONFIGS["remove_" + r].items() if not v], [r])

    def test_schedule_exact_signal_date_and_default(self):
        self.assertEqual(config_for({5: "all_on"}, 4), "baseline")
        self.assertEqual(config_for({5: "all_on"}, 5), "all_on")
        with self.assertRaises(ValueError):
            config_for("bad", 5)

    def test_replacement_count_is_independent_of_rank_gap(self):
        ss, m, r, f = selection_fixture()
        old = {s: 0.1 for s in ss[6:12]}
        for s in old:
            f[s]["mu"] = 0.01
        base, a = select_names(m, f, old, r, 0.001)
        limited, b = select_names(m, f, old, r, 0.001, "add_replace1")
        self.assertGreater(a["replacements"], 1)
        self.assertEqual(b["replacements"], 1)
        self.assertGreater(b["blocked_by_count"], 0)
        self.assertEqual(b["blocked_by_rank_gap"], 0)
        self.assertNotEqual(base, limited)

    def test_rank_gap_can_block_without_count_constraint(self):
        ss, m, r, f = selection_fixture()
        old = {ss[1]: 0.1}
        f[ss[1]]["mu"] = 0.01
        a, x = select_names(m, f, old, r, 0.001, maximum=1)
        b, y = select_names(m, f, old, r, 0.001, "add_rank_gap3", maximum=1)
        self.assertEqual(a, [ss[0]])
        self.assertEqual(b, [ss[1]])
        self.assertGreater(y["blocked_by_rank_gap"], 0)
        self.assertEqual(y["blocked_by_count"], 0)

    def test_retention18_is_distinct(self):
        ss, m, r, f = selection_fixture()
        old = {ss[13]: 0.1}
        a, x = select_names(m, f, old, r, 0.001, maximum=1)
        b, y = select_names(m, f, old, r, 0.001, "add_retain18", maximum=1)
        self.assertEqual(a, [ss[0]])
        self.assertEqual(b, [ss[13]])
        self.assertEqual(y["retained_beyond12"], 1)

    def test_normal_and_caution_budgets_are_separate(self):
        ss, m, r, f = selection_fixture()
        old = {s: 0.1 for s in ss[:6]}
        adv = {s: 1000000000.0 for s in ss}
        for level, cfg, budget in [
            (0, "baseline", 0.5),
            (0, "add_normal_budget25", 0.25),
            (0, "add_caution_budget25", 0.5),
            (1, "baseline", None),
            (1, "add_normal_budget25", None),
            (1, "add_caution_budget25", 0.25),
        ]:
            w, d = allocate(
                dict(weights=old, nav=1000000.0, sellable=old, receivable_weight=0.0),
                f,
                dict(meta=m, adv=adv, returns=r),
                exposure=0.9,
                level=level,
                config=cfg,
            )
            self.assertEqual(d["status"], "OPTIMAL")
            self.assertEqual(d["turnover_budget"], budget)

    def test_initial_funding_exempt_and_frozen_has_no_buys(self):
        ss, m, r, f = selection_fixture()
        adv = {s: 1000000000.0 for s in ss}
        w, d = allocate(
            dict(weights={}, nav=1000000.0, sellable={}, receivable_weight=0.0),
            f,
            dict(meta=m, adv=adv, returns=r),
            exposure=0.9,
            level=0,
            config="all_on",
        )
        self.assertIsNone(d["turnover_budget"])
        self.assertGreater(sum(w.values()), 0.25)
        old = {ss[0]: 0.14}
        w, d = allocate(
            dict(weights=old, nav=1000000.0, sellable=old, receivable_weight=0.0),
            f,
            dict(meta=m, adv=adv, returns=r),
            exposure=0.9,
            level=2,
            config="all_on",
        )
        self.assertLessEqual(sum(w.values()), 0.14)
        self.assertEqual(set(w), set(old))

    def test_terminal_exit_ignores_all_turnover_controls(self):
        ss, m, r, f = selection_fixture()
        old = {ss[0]: 0.5}
        w, d = allocate(
            dict(weights=old, nav=1000000.0, sellable={}, receivable_weight=0.0),
            f,
            dict(meta=m, adv={}, returns=r),
            exposure=0.9,
            level=3,
            config="all_on",
        )
        self.assertEqual(w, {ss[0]: 0.0})
        self.assertEqual(d["status"], "RISK_EXIT")

    def test_deadband_cannot_block_required_deleveraging(self):
        ss, m, r, f = selection_fixture()
        old = {s: 0.13 for s in ss[:6]}
        w, d = allocate(
            dict(weights=old, nav=1000000.0, sellable=old, receivable_weight=0.0),
            f,
            dict(meta=m, adv={s: 1000000000.0 for s in ss}, returns=r),
            exposure=0.12,
            level=1,
            config="add_deadband2",
        )
        self.assertLessEqual(sum(w.values()), 0.12 + 1e-08)
        self.assertEqual(d["rule_activity"]["buffered_positions"], 0)


class SelectionTests(unittest.TestCase):
    def base(self):
        return {
            "returns": [0.0] * 6,
            "worst_drawdown": -0.02,
            "unresolved_events": 0,
            "quality_issues": 0,
            "accounting_pass": True,
            "continuous": {
                "return": 0.01,
                "max_drawdown": -0.02,
                "unresolved_events": 0,
                "quality_issues": 0,
            },
            "factor_check": {"pass": True},
        }

    def test_requires_continuous_return_not_just_good_short_accounts(self):
        b = self.base()
        c = copy.deepcopy(b)
        c["returns"] = [0.002] * 6
        self.assertTrue(assess(c, b)["qualifies"])
        c["continuous"]["return"] = -0.01
        self.assertFalse(assess(c, b)["qualifies"])

    def test_requires_continuous_drawdown_and_quality(self):
        b = self.base()
        c = copy.deepcopy(b)
        c["returns"] = [0.002] * 6
        c["continuous"]["max_drawdown"] = -0.09
        self.assertFalse(assess(c, b)["qualifies"])
        c["continuous"]["max_drawdown"] = -0.02
        c["continuous"]["unresolved_events"] = 1
        self.assertFalse(assess(c, b)["qualifies"])

    def test_default_and_simpler_tie_break(self):
        x = {n: self.base() for n in CONFIGS}
        self.assertEqual(select_rules(x), "baseline")
        x["all_on"]["returns"] = x["add_deadband2"]["returns"] = [0.002] * 6
        self.assertEqual(select_rules(x), "add_deadband2")

    def test_industry_gate_still_required(self):
        x = {n: self.base() for n in ["momentum", "ridge25", "ridge50", "tree25", "tree50"]}
        x["ridge25"]["returns"] = [0.003] * 6
        x["ridge25"]["factor_check"]["pass"] = False
        self.assertEqual(select_model(x), "momentum")
        x["ridge25"]["factor_check"]["pass"] = True
        self.assertEqual(select_model(x), "ridge25")


class SimulationTests(unittest.TestCase):
    def test_all_configs_independent_ledger_and_execution_checks(self):
        b, f = market_bundle()
        for name in CONFIGS:
            s, ledger_detail = simulate(b, 6, 25, f, rules=name)
            self.assertEqual(reconcile(ledger_detail, s, b["meta"], [])["status"], "PASS")
            self.assertEqual(execution_audit(ledger_detail, b)["status"], "PASS")

    def test_future_rules_and_predictions_do_not_change_prefix(self):
        b, f = market_bundle()
        s, ledger_detail = simulate(b, 6, 25, f)
        g = copy.deepcopy(f)
        rules = {i: "all_on" for i in range(12, 30)}
        for i in range(12, 30):
            g[i] = {}
        z, k = simulate(b, 6, 25, g, rules=rules)
        cut = str(b["calendar"][12].date())
        for key in ["trades", "curve"]:
            self.assertEqual(
                [x for x in ledger_detail[key] if x["date"] <= cut],
                [x for x in k[key] if x["date"] <= cut],
            )

    def test_penalty_not_an_extra_realized_fee(self):
        b, f = market_bundle()
        s, ledger_detail = simulate(b, 6, 6, f)
        z, k = simulate(b, 6, 6, f, rules="add_penalty10")
        self.assertEqual(ledger_detail["trades"], k["trades"])
        self.assertEqual(s["fees"], z["fees"])

    def test_latched_risk_does_not_reopen_buys(self):
        b, f = market_bundle()
        for s in f[0]:
            b["raws"][s].loc[b["calendar"][6] :, "close"] = 9.15
            b["raws"][s].loc[b["calendar"][7] :, "open"] = 9.15
        s, ledger_detail = simulate(b, 6, 25, f, rules="all_on")
        freeze = next(
            (
                d["execution_date"]
                for d in ledger_detail["decisions"]
                if d["recovery"]["phase"] == "FROZEN20"
            )
        )
        self.assertFalse(
            any((t["date"] > freeze and t["side"] == "BUY" for t in ledger_detail["trades"]))
        )
        self.assertEqual(reconcile(ledger_detail, s, b["meta"], [])["status"], "PASS")

    def test_audit_rejects_recovery_even_after_a_long_healthy_streak(self):
        bundle, forecasts = market_bundle()
        for symbol in forecasts[0]:
            bundle["raws"][symbol].loc[bundle["calendar"][6] :, "close"] = 9.15
            bundle["raws"][symbol].loc[bundle["calendar"][7] :, "open"] = 9.15
        _, account = simulate(bundle, 6, 25, forecasts)
        state = account["decisions"][-1]["recovery"]
        self.assertEqual(state["phase"], "FROZEN20")
        state.update(phase="NORMAL", changed=True, state_age_before=5, healthy_streak_before=3)
        audit = execution_audit(account, bundle)
        self.assertEqual(audit["status"], "FAIL")
        self.assertIn("RISK_LEVEL_RESTORED", [error[0] for error in audit["errors"]])

    def test_audit_rejects_a_missing_daily_decision(self):
        bundle, forecasts = market_bundle()
        _, account = simulate(bundle, 6, 8, forecasts)
        account["decisions"].pop()
        self.assertEqual(execution_audit(account, bundle)["errors"], [["DECISION_DATES_MISMATCH"]])


if __name__ == "__main__":
    unittest.main()
