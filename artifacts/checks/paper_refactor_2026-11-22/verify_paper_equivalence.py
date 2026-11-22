"""Expand extracted statements and compare complete paper workflows with the baseline."""

import ast
import copy
import hashlib
import itertools
import json
import subprocess
import sys
import types
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from quantfolio import accounts, ledger, planning
from quantfolio.rules import CONFIGS, TURNOVER_POLICIES
from quantfolio.settings import Paths
from quantfolio.storage import digest, native_scalar, now, read, save, sha

ROOT = Path(__file__).resolve().parents[3]
CHECKS = Path(__file__).resolve().parent
SCOPE = read(CHECKS / "scope.json")
TEMP = ROOT / ".git/paper_refactor_equivalence"
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
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, default=native_scalar).encode("utf-8")
    ).hexdigest()


def original_source(name):
    return subprocess.check_output(
        ["git", "show", f"{SCOPE['baseline_commit']}:src/quantfolio/{name}.py"], cwd=ROOT
    ).decode("utf-8")


def original_module(name):
    module = types.ModuleType(f"quantfolio._paper_before_{name}")
    module.__package__ = "quantfolio"
    module.__file__ = str(TEMP / "sources" / (name + ".py"))
    sys.modules[module.__name__] = module
    exec(compile(original_source(name), module.__file__, "exec"), module.__dict__)
    return module


def expanded_statements():
    records = []
    for name, targets in {
        "planning": {"make_intent": "_opening_contracts", "resolve_intent": "_opening_market"},
        "accounts": {"restore_account": "_restore_risk", "audit_fills": "_check_buy_exposure"},
        "ledger": {"buy": "_record_trade", "sell": "_record_trade"},
    }.items():
        trees = [
            ast.parse(original_source(name)),
            ast.parse((ROOT / f"src/quantfolio/{name}.py").read_text("utf-8")),
        ]
        functions = [
            {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)} for tree in trees
        ]
        old, new = functions
        for target, helper_name in targets.items():
            helper = new[helper_name]

            class Expand(ast.NodeTransformer):
                def visit_Assign(self, node):
                    if (
                        not isinstance(node.value, ast.Call)
                        or ast.unparse(node.value.func) != helper_name
                    ):
                        return self.generic_visit(node)
                    require(
                        [ast.unparse(a) for a in node.value.args]
                        == [a.arg for a in helper.args.args],
                        "Helper argument order",
                    )
                    require(
                        ast.dump(node.targets[0], include_attributes=False).replace(
                            "Store()", "Load()"
                        )
                        == ast.dump(helper.body[-1].value),
                        "Helper result binding",
                    )
                    alias = (
                        'panels = bundle["panels"]'
                        if helper_name == "_opening_contracts"
                        else 'contracts = intent["contracts"]'
                    )
                    require(
                        ast.dump(helper.body[1]) == ast.dump(ast.parse(alias).body[0]),
                        "Helper alias",
                    )
                    return copy.deepcopy(helper.body[2:-1])

                def visit_Return(self, node):
                    if target != "restore_account" or not isinstance(node.value, ast.Tuple):
                        return node
                    call = node.value.elts[-1]
                    require(
                        isinstance(call, ast.Call)
                        and ast.unparse(call) == "_restore_risk(account, book)",
                        "Risk restoration call",
                    )
                    node.value.elts[-1] = copy.deepcopy(helper.body[-1].value)
                    return [*copy.deepcopy(helper.body[1:-1]), node]

                def visit_Expr(self, node):
                    if not isinstance(node.value, ast.Call):
                        return self.generic_visit(node)
                    call = node.value
                    if ast.unparse(call.func) == "_check_buy_exposure":
                        require(
                            [ast.unparse(a) for a in call.args]
                            == [a.arg for a in helper.args.args],
                            "Exposure argument order",
                        )
                        return copy.deepcopy(helper.body[1:])
                    if ast.unparse(call.func) != "self._record_trade":
                        return self.generic_visit(node)
                    bindings = dict(
                        zip(
                            [a.arg for a in helper.args.args],
                            [ast.Name(id="self", ctx=ast.Load()), *call.args],
                            strict=True,
                        )
                    )

                    class Bind(ast.NodeTransformer):
                        def visit_Name(self, value):
                            return copy.deepcopy(bindings.get(value.id, value))

                    return [Bind().visit(copy.deepcopy(statement)) for statement in helper.body[1:]]

            expanded = Expand().visit(copy.deepcopy(new[target]))
            require(
                ast.dump(expanded) == ast.dump(old[target]),
                f"Expanded statements differ: {name}.{target}",
            )
            records.append(f"{name}.{target}")
        for function in old.keys() - targets.keys():
            # The local amount converter is also checked, independently of Ledger.cents.
            require(
                ast.dump(old[function]) == ast.dump(new[function]),
                f"Unchanged function differs: {name}.{function}",
            )
    return records


def outcome(function, *args, **kwargs):
    try:
        return {"value": function(*args, **kwargs)}
    except (ValueError, KeyError, TypeError) as error:
        return {"error": type(error).__name__, "message": str(error)}


def restoration_cases(before):
    bundle, _ = market_bundle()
    account = accounts.new_account("2026-01-12", "restoration")
    account.update(
        kind="manual", initial_date="2026-01-05", cash=900000, sessions_completed=5, next_lot_id=2
    )
    account["risk"].update(age=5, healthy_streak=3, observations=5)
    account["lots"] = [
        {
            "id": 1,
            "symbol": "sh600001",
            "quantity": 10000,
            "bought": "2026-01-06",
            "available": "2026-01-07",
            "stop_cost": 10.0,
        }
    ]
    cases = [account, accounts.new_account("2026-01-12", "fresh")]
    for field, values in {
        "mode": ["staged_restore", "unknown"],
        "phase": ["PROBE20", "unknown"],
        "peak": [0, 999999, float("inf")],
        "probe_anchor": [1],
        "age": [-1, True, 6],
        "healthy_streak": [-1, 6],
        "observations": [0, 6],
    }.items():
        for value in values:
            changed = copy.deepcopy(account)
            changed["risk"][field] = value
            cases.append(changed)
    for field, value in [
        ("risk", {}),
        ("next_lot_id", 1),
        ("sessions_completed", 0),
        ("initial_cash", 1),
        ("cash", 100.001),
        ("initial_date", "2026-01-13"),
    ]:
        cases.append({**copy.deepcopy(account), field: value})
    for field, value in [
        ("symbol", "unknown"),
        ("id", 0),
        ("quantity", 0),
        ("bought", "2025-01-01"),
        ("available", "2026-01-06"),
        ("stop_cost", 0),
    ]:
        changed = copy.deepcopy(account)
        changed["lots"][0][field] = value
        cases.append(changed)

    def restore(module, value):
        book, state = module.restore_account(value, bundle["meta"])
        return module.snapshot_account(
            book, value, value["as_of"], state, value["sessions_completed"], value["parent"]
        )

    records = []
    for value in cases:
        a, b = [outcome(restore, module, copy.deepcopy(value)) for module in (before, accounts)]
        require(fingerprint(a) == fingerprint(b), "Account restoration or rejection differs")
        records.append(a)
    return {
        "cases": len(records),
        "rejections": sum("error" in r for r in records),
        "sha256": fingerprint(records),
    }


def journal_cases(before, account_before):
    records = []
    for code, kind, cash, slip in itertools.product(
        ("600001", "688001", "920001", "510300"),
        ("stock", "etf"),
        (100, 1000000),
        (0, 0.001, 0.003),
    ):
        symbol = "s" + code
        meta = {symbol: {"code": code, "kind": kind, "industry": "test"}}
        books = [module.Ledger(meta, initial=cash) for module in (before, ledger)]
        day = pd.Timestamp("2026-01-05")
        template = accounts.new_account(str(day.date()), "journal")
        results = [[], []]
        for step, (side, quantity, delta) in enumerate(
            [
                ("buy", 200, 0),
                ("sell", 100, 0),
                ("buy", 150, 1),
                ("sell", 101, 1),
                ("sell", 10000, 2),
            ]
        ):
            for index, (book, module) in enumerate(zip(books, (account_before, accounts))):
                result = outcome(
                    getattr(book, side),
                    day + pd.Timedelta(days=delta),
                    symbol,
                    quantity,
                    10.01,
                    day if step % 2 else None,
                    slippage=slip,
                )
                snapshot = module.snapshot_account(
                    book, template, str(day.date()), module.RiskState(), 0, None
                )
                results[index].append(
                    {
                        "return": result,
                        "snapshot": snapshot,
                        "trades": copy.deepcopy(book.trades),
                        "rejections": copy.deepcopy(book.rejections),
                    }
                )
        require(
            fingerprint(results[0]) == fingerprint(results[1]), "Journal or partial state differs"
        )
        records.append(fingerprint(results[1]))
    return {
        "scenarios": len(records),
        "operations": len(records) * 5,
        "sha256": fingerprint(records),
    }


def scenario(case):
    bundle, forecasts = market_bundle()
    dates = bundle["calendar"]
    if case == 1:
        for raw in bundle["raws"].values():
            raw.loc[dates[10] :, ["open", "close", "high", "low"]] *= 0.88
    if case == 2:
        bundle["raws"]["sh600001"] = bundle["raws"]["sh600001"].drop(dates[8:10])
    if case == 3:
        bundle["actions"] = [
            {
                "id": "dividend",
                "symbol": "sh600001",
                "record_date": str(dates[7].date()),
                "ex_date": str(dates[8].date()),
                "cash_pay_date": str(dates[9].date()),
                "cash_available_date": str(dates[10].date()),
                "cash_per_share": 0.1,
                "share_multiplier": 1.0,
                "share_list_date": None,
            }
        ]
        bundle["raws"]["sh600001"].loc[dates[8] :, ["open", "close", "high", "low"]] -= 0.1
    if case == 4:
        bundle["fund_mode"] = "reported"
        bundle["meta"]["sh510300"]["fund_history"] = [
            fund_history_entry(dates[0], fund_profile(known={"capco:C39": "70"}, undisclosed="30"))
        ]
        for values in forecasts.values():
            values["sh510300"] = {"rank": 0.95, "mu": 0.05, "margin": 0}
    return bundle, forecasts


def paper_cases(before):
    records, sessions, rejections = [], 0, 0
    for number, (config, turnover, cost) in enumerate(
        itertools.product(CONFIGS, TURNOVER_POLICIES, (0.001, 0.002, 0.003))
    ):
        bundle, forecasts = scenario(number % 5)
        original = accounts.new_account(str(bundle["calendar"][5].date()), "daily-parity")
        carried = [copy.deepcopy(original), copy.deepcopy(original)]
        sequence = []
        for index in range(6, 21):
            day = str(bundle["calendar"][index].date())
            intents = [
                outcome(
                    module.make_intent,
                    bundle_prefix(bundle, index - 1),
                    account,
                    forecasts[index - 1],
                    config,
                    day,
                    cost,
                    turnover=turnover,
                )
                for module, account in zip((before, planning), carried)
            ]
            require(
                fingerprint(intents[0]) == fingerprint(intents[1]),
                "Intent fields or failure priority differ",
            )
            sequence.append(fingerprint(intents[0]))
            if "error" in intents[0]:
                rejections += 1
                break
            intent = intents[0]["value"]
            quotes = {
                s: {
                    "open": float(bundle["raws"][s].at[bundle["calendar"][index], "open"]),
                    "market_at": day + "T09:25:00+08:00",
                    "suspended": False,
                }
                for s in intent["contracts"]
                if bundle["calendar"][index] in bundle["raws"][s].index
            }
            for preview in (True, False):
                outputs = [
                    outcome(
                        module.resolve_intent,
                        copy.deepcopy(item["value"]),
                        copy.deepcopy(account),
                        bundle["actions"],
                        trading_calendar(bundle),
                        {} if preview else quotes,
                        preview=preview,
                    )
                    for module, item, account in zip((before, planning), intents, carried)
                ]
                require(
                    fingerprint(outputs[0]) == fingerprint(outputs[1]),
                    "Paper outputs or failure priority differ",
                )
                sequence.append(fingerprint(outputs[0]))
                if not preview and "value" in outputs[0]:
                    carried = [item["value"]["opening_account"] for item in outputs]
            sessions += 1
            if "error" in outputs[0]:
                rejections += 1
                break
        records.append(
            {
                "config": config,
                "turnover": turnover,
                "cost": cost,
                "scenario": number % 5,
                "sha256": fingerprint(sequence),
            }
        )
    return {
        "scenarios": len(records),
        "sessions": sessions,
        "paired_rejections": rejections,
        "records": records,
    }


def workflow_cases(before, account_before):
    bundle, forecasts = market_bundle()
    records = []
    for cost in (10, 20, 30):
        roots = [TEMP / f"workflow_{cost}" / side for side in ("before", "after")]
        projects = []
        clock = ["2026-01-12T17:00:00+08:00"]
        with ExitStack() as stack:
            for module in (before, planning, account_before, accounts):
                stack.enter_context(patch.object(module, "now", lambda: clock[0]))
            for root, module in zip(roots, (before, planning)):
                (root / "configs").mkdir(parents=True)
                (root / "configs/project.toml").write_text(
                    'data_cutoff="2026-01-12"\nbundle="data.bin"\ncalendar="configs/calendar.json"\nmanifest="configs/integrity_manifest.json"\n',
                    encoding="utf-8",
                )
                (root / "data.bin").write_bytes(b"Synthetic provider fixture, not market data")
                save(root / "configs/calendar.json", trading_calendar(bundle))
                save(root / "configs/competition.json", {"initial_cash": 1000000})
                save(root / "fit.json", {"synthetic": True})
                save(
                    root / "configs/integrity_manifest.json",
                    {"files": {"configs/calendar.json": sha(root / "configs/calendar.json")}},
                )
                save(root / "account.json", accounts.new_account("2026-01-12", "round-trip"))
                paths = Paths(root)
                projects.append(paths)
                stack.enter_context(
                    patch.object(module, "load_bundle", lambda p: bundle_prefix(bundle, 5))
                )
                stack.enter_context(
                    patch.object(
                        module,
                        "latest_forecasts",
                        lambda p, b: (
                            forecasts[5],
                            {"selected_candidate": "momentum", "selected_rules": "baseline"},
                            [p.root / "fit.json"],
                        ),
                    )
                )
                module.freeze_plan(paths, "account.json", "artifacts/plans/frozen", cost)
            plans = [read(root / "artifacts/plans/frozen/plan.json") for root in roots]
            require(
                all(
                    p["identity"] == digest({k: v for k, v in p.items() if k != "identity"})
                    for p in plans
                ),
                "Plan digest",
            )
            require(
                plans[0]["source_sha256"] == before.source_hashes()
                and plans[1]["source_sha256"] == planning.source_hashes(),
                "Actual source identities",
            )
            economic = [
                {k: v for k, v in p.items() if k not in ("identity", "source_sha256")}
                for p in plans
            ]
            require(
                fingerprint(economic[0]) == fingerprint(economic[1]),
                "Frozen plan content differs beyond source identity",
            )
            require(
                (roots[0] / "artifacts/plans/frozen/plan.md").read_bytes()
                == (roots[1] / "artifacts/plans/frozen/plan.md").read_bytes(),
                "Plan report bytes",
            )
            clock[0] = "2026-01-13T09:26:00+08:00"
            for module, paths, plan in zip((before, planning), projects, plans):
                save(
                    paths.root / "quotes.json",
                    {
                        "session": "2026-01-13",
                        "source": "synthetic",
                        "recorded_at": clock[0],
                        "quotes": opening_quotes(plan["intent"]),
                    },
                )
                module.resolve_plan(
                    paths,
                    "artifacts/plans/frozen/plan.json",
                    "quotes.json",
                    "artifacts/plans/resolved",
                )
            results = [read(root / "artifacts/plans/resolved/resolution.json") for root in roots]
            require(
                all(
                    r["identity"] == digest({k: v for k, v in r.items() if k != "identity"})
                    for r in results
                ),
                "Resolution digest",
            )
            economic = [
                {k: v for k, v in r.items() if k not in ("identity", "plan_sha256")}
                for r in results
            ]
            require(
                fingerprint(economic[0]) == fingerprint(economic[1]),
                "Resolved plan differs beyond parent hash",
            )
            clock[0] = "2026-01-13T16:01:00+08:00"
            closed = []
            for module, paths, plan in zip((before, planning), projects, plans):
                save(
                    paths.root / "closing.json",
                    {
                        "session": "2026-01-13",
                        "source": "synthetic",
                        "recorded_at": clock[0],
                        "quotes": {
                            s: {"close": 10.2, "market_at": "2026-01-13T15:00:00+08:00"}
                            for s in plan["intent"]["contracts"]
                        },
                    },
                )
                output = module.close_account(
                    paths,
                    "artifacts/plans/resolved/resolution.json",
                    "closing.json",
                    "artifacts/accounts/closed",
                )
                value = read(output["account"])
                module.validate_parent(paths, value)
                require(
                    value["parent"]["sha256"]
                    == sha(paths.root / "artifacts/plans/resolved/resolution.json"),
                    "Closing parent identity",
                )
                value["parent"].pop("sha256")
                closed.append(value)
            require(
                fingerprint(closed[0]) == fingerprint(closed[1]),
                "Closing balances or carried risk differ",
            )
            records.append(
                {
                    "cost_bps": cost,
                    "plan_economic_sha256": fingerprint(
                        {
                            k: v
                            for k, v in plans[1].items()
                            if k not in ("identity", "source_sha256")
                        }
                    ),
                    "closing_sha256_without_parent_digest": fingerprint(closed[1]),
                    "source_hash_differences": [
                        name
                        for name, value in plans[0]["source_sha256"].items()
                        if plans[1]["source_sha256"][name] != value
                    ],
                }
            )
    return records


receipt = CHECKS / "differential.json"
if receipt.exists() or TEMP.exists():
    raise FileExistsError("Use new audit output paths")
(TEMP / "sources").mkdir(parents=True)
for name, expected in SCOPE["original_runtime_sha256"].items():
    if name.endswith(".py"):
        target = TEMP / "sources" / Path(name).name
        target.write_bytes(
            subprocess.check_output(["git", "show", f"{SCOPE['baseline_commit']}:{name}"], cwd=ROOT)
        )
        require(sha(target) == expected, "Original source snapshot")
runtime = {p.name: sha(p) for p in sorted((ROOT / "src/quantfolio").glob("*.py"))}
result = {
    "kind": "EXECUTION_DIFFERENTIAL",
    "baseline_commit": SCOPE["baseline_commit"],
    "audit_sha256": sha(Path(__file__)),
    "runtime_sha256": runtime,
    "subjects": {
        k: {"sha256": v["sha256"], "source_sha256": v["sources"]}
        for k, v in SCOPE["subjects"].items()
    },
    "clock": "Common synthetic verification clock; production clocks and source identities preserved.",
}
try:
    result["expanded_statement_parity"] = expanded_statements()
    before_ledger, before_accounts, before_simulation, before = [
        original_module(name) for name in ("ledger", "accounts", "simulation", "planning")
    ]
    before_accounts.Ledger = before_ledger.Ledger
    before_accounts.Lot = before_ledger.Lot
    before_simulation.Ledger = before_ledger.Ledger
    before._Execution = before_simulation._Execution
    for name in ("audit_fills", "new_account", "restore_account", "snapshot_account"):
        setattr(before, name, getattr(before_accounts, name))
    with (
        patch.object(accounts, "now", return_value="2026-01-12T17:00:00+08:00"),
        patch.object(before_accounts, "now", return_value="2026-01-12T17:00:00+08:00"),
    ):
        result["restoration"] = restoration_cases(before_accounts)
        result["journal"] = journal_cases(before_ledger, before_accounts)
        result["paper"] = paper_cases(before)
    print("Statement, restoration, journal and daily paper parity passed", flush=True)
    result["workflows"] = workflow_cases(before, before_accounts)
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
print("PASS: exact paper workflow equivalence with original source provenance")
