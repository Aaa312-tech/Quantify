"""Compare numerical decisions and ordered ledger bytes with the committed implementation."""

import copy
import hashlib
import itertools
import json
import subprocess
import sys
import types
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import numpy as np

from quantfolio import accounts, allocation, planning, risk, simulation
from quantfolio.rules import CONFIGS, TURNOVER_POLICIES
from quantfolio.storage import native_scalar, now, read, save, sha
from quantfolio.validation import execution_audit, reconcile

ROOT = Path(__file__).resolve().parents[3]
CHECKS = Path(__file__).resolve().parent
SCOPE = read(CHECKS / "scope.json")
sys.path.insert(0, str(ROOT))
from tests.factories import (  # noqa: E402
    bundle_prefix,
    fund_history_entry,
    fund_profile,
    market_bundle,
    opening_quotes,
    trading_calendar,
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fingerprint(value):
    # Preserve dictionary order and floating representation, as compressed ledgers do.
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, default=native_scalar).encode("utf-8")
    ).hexdigest()


def old_module(name):
    code = subprocess.check_output(
        ["git", "show", f"{SCOPE['baseline_commit']}:src/quantfolio/{name}.py"], cwd=ROOT
    )
    module = types.ModuleType(f"quantfolio._original_{name}")
    module.__package__ = "quantfolio"
    module.__file__ = str(ROOT / "src/quantfolio" / (name + ".py"))
    sys.modules[module.__name__] = module
    exec(compile(code, module.__file__, "exec"), module.__dict__)
    return module


def risk_cases(original):
    cases = 0
    boundaries = (0.025, 0.04, 0.05, 0.06, 0.07, 0.08)
    navs = [1_010_000.0, 1_000_000.0]
    for drawdown in boundaries:
        threshold = 1_000_000 * (1 - (drawdown - 1e-12))
        navs.extend([np.nextafter(threshold, -np.inf), threshold, np.nextafter(threshold, np.inf)])
    for mode, phase, age, streak, observations, healthy, nav in itertools.product(
        risk.RISK_MODES, risk.CAPS, (0, 4, 5, 9, 10), (0, 2, 3, 4, 5), (0, 5), (False, True), navs
    ):
        if phase == "PROBE20" and mode != "probe_restore":
            continue
        fields = dict(
            mode=mode, phase=phase, age=age, healthy_streak=streak, observations=observations
        )
        before, after = original.RiskState(**fields), risk.RiskState(**fields)
        require(
            fingerprint(before.update(nav, healthy)) == fingerprint(after.update(nav, healthy)),
            "Risk output",
        )
        require(asdict(before) == asdict(after), "Risk state")
        cases += 1
    for mode in risk.RISK_MODES:
        before, after = original.RiskState(mode=mode), risk.RiskState(mode=mode)
        rng = np.random.default_rng(137)
        nav = 1e6
        for _ in range(1000):
            nav *= 1 + rng.normal(0.0002, 0.008)
            healthy = bool(rng.random() > 0.2)
            require(
                fingerprint(before.update(nav, healthy)) == fingerprint(after.update(nav, healthy)),
                "Risk path",
            )
            require(asdict(before) == asdict(after), "Risk path state")
            cases += 1
    for fields, nav in [
        ({"mode": "bad"}, 1e6),
        ({"phase": "bad"}, 1e6),
        ({"phase": "PROBE20"}, 1e6),
    ] + [({}, x) for x in (0, -1, np.inf, np.nan)]:
        errors = []
        for cls in (original.RiskState, risk.RiskState):
            state = cls(**fields)
            try:
                state.update(nav, True)
            except ValueError as error:
                errors.append((str(error), asdict(state)))
        require(len(errors) == 2 and errors[0] == errors[1], "Risk invalid-input behavior")
        cases += 1
    return cases


def optimizer_cases(original):
    bundle, forecasts = market_bundle()
    meta = bundle["meta"]
    symbols = list(meta)
    fc = {**forecasts[0], symbols[-1]: {"rank": 0.7, "mu": 0.04, "margin": 0.001}}
    calls = []
    solver = allocation.linprog

    def capture(objective, **kwargs):
        def encoded(value):
            if isinstance(value, np.ndarray):
                return (value.dtype.str, value.shape, value.tobytes().hex())
            return value

        calls.append(
            fingerprint(
                {"objective": encoded(objective), **{k: encoded(v) for k, v in kwargs.items()}}
            )
        )
        return solver(objective, **kwargs)

    records, solver_calls = [], 0
    for number, (config, turnover, level, case) in enumerate(
        itertools.product(CONFIGS, TURNOVER_POLICIES, range(4), range(4))
    ):
        current_meta = copy.deepcopy(meta)
        previous = {} if case == 0 else {s: (0.14 if case < 3 else 0.2) for s in symbols[:6]}
        sellable = dict(previous) if case != 2 else {s: 0.01 for s in previous}
        adv = {s: (1e9 if case != 2 else 1e6) for s in symbols}
        if case == 3:
            current_meta[symbols[-1]]["fund_profile"] = fund_profile(
                known={"capco:C39": "70"}, undisclosed="30"
            )
        args = (
            current_meta,
            fc,
            previous,
            0.9,
            level,
            adv,
            1e6,
            sellable,
            bundle["panels"]["ret1"],
        )
        kwargs = dict(
            slippage=(number % 3 + 1) / 1000,
            receivable_weight=0.02 if case == 2 else 0,
            config=config,
            turnover=turnover,
            allow_probe=case == 3 and level == 2,
        )
        outputs, matrices = [], []
        for module in (original, allocation):
            calls.clear()
            with patch.object(module, "linprog", capture):
                outputs.append(fingerprint(module.allocate(*args, **kwargs)))
            matrices.append(list(calls))
        require(outputs[0] == outputs[1], f"Allocation outputs: {config}/{turnover}/{level}/{case}")
        require(
            matrices[0] == matrices[1],
            "Optimizer objectives, constraints, bounds or retry calls differ",
        )
        solver_calls += len(matrices[0])
        records.append(
            {
                "config": config,
                "turnover": turnover,
                "level": level,
                "case": case,
                "result": outputs[0],
                "solver_calls": matrices[0],
            }
        )
    return {
        "cases": len(records),
        "solver_calls_per_implementation": solver_calls,
        "records_sha256": fingerprint(records),
    }


def scenario(case):
    bundle, forecasts = market_bundle()
    dates = bundle["calendar"]
    symbol = "sh600001"
    if case == 1:
        for raw in bundle["raws"].values():
            raw.loc[dates[10] :, ["open", "close", "high", "low"]] *= 0.88
    elif case == 2:
        bundle["raws"][symbol] = bundle["raws"][symbol].drop(dates[8:11])
    elif case == 3:
        bundle["actions"] = [
            {
                "id": "synthetic-dividend",
                "symbol": symbol,
                "record_date": str(dates[7].date()),
                "ex_date": str(dates[8].date()),
                "share_list_date": str(dates[10].date()),
                "cash_pay_date": str(dates[9].date()),
                "cash_available_date": str(dates[10].date()),
                "cash_per_share": 0.1,
                "share_multiplier": 1.0,
                "cash_rounding": None,
            }
        ]
        bundle["raws"][symbol].loc[dates[8] :, ["open", "close", "high", "low"]] -= 0.1
    elif case == 4:
        bundle["fund_mode"] = "reported"
        bundle["meta"]["sh510300"]["fund_history"] = [
            fund_history_entry(dates[0], fund_profile(known={"capco:C39": "70"}, undisclosed="30"))
        ]
        for forecast in forecasts.values():
            forecast["sh510300"] = {"rank": 0.95, "mu": 0.05, "margin": 0}
    return bundle, forecasts


def simulation_cases(original):
    records, audit_checks = [], 0
    for number, (config, turnover, mode) in enumerate(
        itertools.product(CONFIGS, TURNOVER_POLICIES, risk.RISK_MODES)
    ):
        bundle, forecasts = scenario(number % 5)
        kwargs = dict(slippage=(number % 3 + 1) / 1000, mode=mode, rules=config, turnover=turnover)
        before = original.simulate(copy.deepcopy(bundle), 6, 29, forecasts, **kwargs)
        after = simulation.simulate(copy.deepcopy(bundle), 6, 29, forecasts, **kwargs)
        require(
            fingerprint(before) == fingerprint(after),
            f"Full simulation: {config}/{turnover}/{mode}",
        )
        summary, ledger = after
        for check in (
            reconcile(ledger, summary, bundle["meta"], bundle["actions"]),
            execution_audit(ledger, bundle),
        ):
            require(check["status"] == "PASS", f"Independent synthetic audit: {check}")
            audit_checks += check["checks"]
        records.append(
            {
                "config": config,
                "turnover": turnover,
                "mode": mode,
                "scenario": number % 5,
                "sha256": fingerprint(after),
            }
        )
    return {"cases": len(records), "independent_checks": audit_checks, "records": records}


def paper_cases(original, original_risk):
    def restore(account, meta):
        book, state = accounts.restore_account(account, meta)
        return book, original_risk.RiskState(**asdict(state))

    original.restore_account = restore
    records = []
    for number, (config, turnover) in enumerate(itertools.product(CONFIGS, TURNOVER_POLICIES)):
        bundle, forecasts = scenario(4 if number % 2 else 0)
        day = str(bundle["calendar"][6].date())
        account = accounts.new_account(str(bundle["calendar"][5].date()), "synthetic-parity")
        intents = [
            module.make_intent(
                bundle_prefix(bundle, 5),
                copy.deepcopy(account),
                forecasts[5],
                config,
                day,
                0.001,
                turnover=turnover,
            )
            for module in (original, planning)
        ]
        require(fingerprint(intents[0]) == fingerprint(intents[1]), "Paper intentions")
        for preview in (False, True):
            values = [
                module.resolve_intent(
                    intent,
                    copy.deepcopy(account),
                    [],
                    trading_calendar(bundle),
                    {} if preview else opening_quotes(intent),
                    preview=preview,
                )
                for module, intent in zip((original, planning), intents)
            ]
            require(fingerprint(values[0]) == fingerprint(values[1]), "Paper executions")
            require(values[1]["audit"]["status"] == "PASS", "Independent paper fill audit")
            records.append(fingerprint(values[1]))
    return {"cases": len(records), "ordered_results_sha256": fingerprint(records)}


receipt = CHECKS / "differential.json"
if receipt.exists():
    raise FileExistsError(receipt)
runtime = {p.name: sha(p) for p in sorted((ROOT / "src/quantfolio").glob("*.py"))}
result = {
    "kind": "EXECUTION_DIFFERENTIAL",
    "baseline_commit": SCOPE["baseline_commit"],
    "audit_sha256": sha(Path(__file__)),
    "runtime_sha256": runtime,
    "subjects": {
        key: {"sha256": value["sha256"], "source_sha256": value["sources"]}
        for key, value in SCOPE["subjects"].items()
    },
}
try:
    old_risk, old_allocation, old_simulation, old_planning = [
        old_module(name) for name in ("risk", "allocation", "simulation", "planning")
    ]
    old_simulation.allocate = old_allocation.allocate
    old_simulation.RiskState = old_risk.RiskState
    old_simulation.healthy_market = old_risk.healthy_market
    old_planning.allocate = old_allocation.allocate
    old_planning._Execution = old_simulation._Execution
    old_planning.buy_ceiling = old_simulation.buy_ceiling
    result["risk_cases"] = risk_cases(old_risk)
    print("Risk parity", result["risk_cases"], flush=True)
    result["allocation"] = optimizer_cases(old_allocation)
    print("Allocation parity", result["allocation"]["cases"], flush=True)
    result["simulation"] = simulation_cases(old_simulation)
    print("Simulation parity", result["simulation"]["cases"], flush=True)
    result["paper"] = paper_cases(old_planning, old_risk)
    require(
        runtime == {p.name: sha(p) for p in sorted((ROOT / "src/quantfolio").glob("*.py"))},
        "Runtime changed during audit",
    )
    result["status"] = "PASS"
except Exception as error:
    result.update(status="FAIL", error=f"{type(error).__name__}: {error}")
    raise
finally:
    result["completed_at"] = now()
    save(receipt, result, immutable=True)
print("PASS: exact execution parity, including ordered serialized outputs")
