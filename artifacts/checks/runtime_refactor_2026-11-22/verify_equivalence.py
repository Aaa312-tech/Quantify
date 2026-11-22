"""Verify extracted selection statements, frozen-window replay and shared account execution."""

import ast
import copy
import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np

from quantfolio import evaluation, models, training
from quantfolio.dataset import load_bundle
from quantfolio.risk import RISK_MODES
from quantfolio.rules import TURNOVER_POLICIES
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, now, read, save, sha

ROOT = Path(__file__).resolve().parents[3]
CHECKS = Path(__file__).resolve().parent
TEMP = ROOT / ".git/runtime_refactor_equivalence"
BASELINE = read(CHECKS / "scope.json")["baseline_commit"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def old_source(name):
    return subprocess.check_output(
        ["git", "show", f"{BASELINE}:src/quantfolio/{name}.py"], cwd=ROOT
    ).decode("utf-8")


def old_module(name):
    module = types.ModuleType(f"quantfolio._before_{name}")
    module.__package__ = "quantfolio"
    module.__file__ = str(ROOT / "src/quantfolio" / f"{name}.py")
    sys.modules[module.__name__] = module
    exec(compile(old_source(name), module.__file__, "exec"), module.__dict__)
    return module


def statements_equal():
    before = ast.parse(old_source("training"))
    after = ast.parse(Path(training.__file__).read_text("utf-8"))

    def functions(tree):
        return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    old, new = functions(before), functions(after)
    helper = new["_select_validation"]
    expanded = copy.deepcopy(new["run_window"])
    for index, node in enumerate(expanded.body):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == helper.name:
                require(
                    [a.id for a in node.value.args] == [a.arg for a in helper.args.args],
                    "Extracted phase positional bindings changed",
                )
                require(
                    {a.arg: a.value.id for a in node.value.keywords}
                    == {a.arg: a.arg for a in helper.args.kwonlyargs},
                    "Extracted phase keyword bindings changed",
                )
                require(
                    ast.dump(node.targets[0])
                    == ast.dump(
                        ast.parse("selected_rules, selected, rules, models = result")
                        .body[0]
                        .targets[0]
                    ),
                    "Extracted phase return binding changed",
                )
                require(
                    ast.dump(helper.body[-1].value)
                    == ast.dump(
                        ast.parse("selected_rules, selected, rules, models", mode="eval").body
                    ),
                    "Extracted phase return order changed",
                )
                require(
                    ast.dump(helper.body[1])
                    == ast.dump(ast.parse('table = bundle["table"]').body[0]),
                    "Unexpected helper prelude",
                )
                expanded.body[index : index + 1] = copy.deepcopy(helper.body[2:-1])
                break
    require(
        ast.dump(expanded) == ast.dump(old["run_window"]), "Training operation sequence changed"
    )
    require(
        ast.dump(new["run_training"]) == ast.dump(old["run_training"]),
        "Training registration changed",
    )
    before = functions(ast.parse(old_source("evaluation")))
    after = functions(ast.parse(Path(evaluation.__file__).read_text("utf-8")))
    protected = (
        "load_predictions",
        "experiment_folds",
        "evaluate",
        "execution_bridge",
        "policy_inputs",
        "replay",
    )
    for name in protected:
        require(
            ast.dump(before[name]) == ast.dump(after[name]),
            f"Evaluation guard or entry changed: {name}",
        )
    return {
        "expanded_window_ast_equal": True,
        "registration_ast_equal": True,
        "unchanged_evaluation_functions": list(protected),
    }


def replay_windows():
    paths = Paths(ROOT)
    bundle = load_bundle(paths)
    summary = read(
        paths.artifact_path(paths.config["evaluation"], "evaluations") / "evaluation_summary.json"
    )
    run = paths.artifact_path(summary["source_run"], "experiments")
    results = sorted(run.glob("training/fold_*/result.json")) + [run / "final_fit/result.json"]
    records, fit_calls, trial_calls = [], [], []
    fit_stage, trial = models.fit_stage, training.trial

    def no_fit(*args, **kwargs):
        raise ValueError("This verification must never fit a new model")

    for result_path in results:
        frozen = read(result_path)
        source = result_path.parent
        output = TEMP / "windows" / source.name

        def cached_fit(bundle, train_rows, pred_rows, path, identity):
            target = source / path.name
            require(
                all((target / family / "fit.json").is_file() for family in ("ridge", "tree")),
                "Missing frozen fit",
            )
            fit_calls.append(
                {
                    "fold": frozen["fold"]["fold"],
                    "stage": path.name,
                    "train_rows_sha256": array_sha(train_rows),
                    "prediction_rows_sha256": array_sha(pred_rows),
                    "identity": identity,
                }
            )
            return fit_stage(bundle, train_rows, pred_rows, target, identity)

        def cached_trial(bundle, fold, forecasts, config, path, identity, **controls):
            target = source / path.parent.name / path.name
            require(target.with_suffix(".json").is_file(), "Missing frozen validation trial")
            trial_calls.append(
                {
                    "fold": fold["fold"],
                    "trial": target.relative_to(run).as_posix(),
                    "config": config,
                    "controls": controls,
                }
            )
            return trial(bundle, fold, forecasts, config, target, identity, **controls)

        with (
            patch.object(training, "fit_stage", cached_fit),
            patch.object(training, "trial", cached_trial),
            patch.object(training, "now", lambda: frozen["completed_at"]),
            patch.object(models, "make_model", no_fit),
        ):
            actual = training.run_window(
                bundle,
                frozen["fold"],
                output,
                frozen["identity"],
                final=frozen["final_fit"],
                positive_score=True,
                turnover="legacy",
            )
        require(actual == frozen, f"Window result differs: {source.name}")
        with (
            np.load(source / "outer_predictions.npz") as before,
            np.load(output / "outer_predictions.npz") as after,
        ):
            require(before.files == after.files, "Forecast array names/order changed")
            for key in before.files:
                np.testing.assert_array_equal(before[key], after[key], strict=True)
        require(
            sha(output / "outer_predictions.npz") == frozen["predictions_sha256"],
            "Forecast archive bytes changed",
        )
        records.append(
            {
                "fold": frozen["fold"]["fold"],
                "result_equal": True,
                "forecast_sha256": frozen["predictions_sha256"],
                "selected_candidate": actual["selected_candidate"],
                "selected_rules": actual["selected_rules"],
            }
        )
        print(f"Frozen window matched: {source.name}", flush=True)
    require(len(records) == 12 and len(fit_calls) == 24, "Unexpected historical window scope")
    return {
        "windows": records,
        "fit_stage_cache_calls": fit_calls,
        "validation_trial_cache_calls": trial_calls,
        "new_model_fits": 0,
    }


def synthetic_accounts():
    spec = importlib.util.spec_from_file_location("refactor_factories", ROOT / "tests/factories.py")
    factories = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(factories)
    bundle, forecasts = factories.market_bundle()
    for raw in bundle["raws"].values():
        for start, end, price in ((10, 14, 9.6), (15, 18, 9.2), (19, 29, 9.9)):
            raw.iloc[start : end + 1, raw.columns.get_indexer(["open", "close"])] = price
            raw.iloc[start : end + 1, raw.columns.get_indexer(["low", "high"])] = [
                price - 0.1,
                price + 0.1,
            ]
    variants = {
        f"{mode}_{turnover}": {
            "forecasts": forecasts,
            "rules": "baseline",
            "mode": mode,
            "turnover": {day: turnover for day in range(9, 29)},
        }
        for mode in RISK_MODES
        for turnover in TURNOVER_POLICIES
    }
    variants["scheduled"] = {
        "forecasts": forecasts,
        "rules": {day: "baseline" if day % 2 else "all_on" for day in range(9, 29)},
        "mode": "latched",
        "turnover": {day: "legacy" if day % 2 else "cost_band" for day in range(9, 29)},
    }
    before = old_module("study")._evaluate_alternatives(
        copy.deepcopy(bundle), variants, 9, 28, TEMP / "old_variants"
    )
    controls = copy.deepcopy(variants)
    for value in controls.values():
        policies = set(value["turnover"].values())
        if len(policies) == 1:
            value["turnover"] = next(iter(policies))
    after = evaluation.evaluate_variants(
        copy.deepcopy(bundle), controls.items(), 9, 28, (10, 20, 30), TEMP / "new_variants"
    )
    require(before == after, "Shared study account matrix differs")
    previous_trial = old_module("training").trial
    trials = []
    for mode in RISK_MODES:
        for turnover in TURNOVER_POLICIES:
            name = f"{mode}_{turnover}"
            fold = {"validation_start": 9, "validation_end": 28}
            old = previous_trial(
                copy.deepcopy(bundle),
                fold,
                forecasts,
                "baseline",
                TEMP / "old_trials" / name,
                "synthetic-parity",
                mode=mode,
                turnover=turnover,
            )
            new = training.trial(
                copy.deepcopy(bundle),
                fold,
                forecasts,
                "baseline",
                TEMP / "new_trials" / name,
                "synthetic-parity",
                mode=mode,
                turnover=turnover,
            )
            require(
                {k: v for k, v in old.items() if k != "ledger_path"}
                == {k: v for k, v in new.items() if k != "ledger_path"},
                f"Validation trial differs: {name}",
            )
            trials.append(
                {"mode": mode, "turnover": turnover, "ledger_sha256": new["ledger_sha256"]}
            )
    return {
        "variants": len(variants),
        "accounts": after["accounts"],
        "ledger_files": len(after["artifacts"]),
        "full_results_and_ledger_hashes_equal": True,
        "trials": trials,
        "path_normalization": "Only the diagnostic output directory differs between old and new trial receipts.",
    }


if __name__ == "__main__":
    receipt = CHECKS / "equivalence.json"
    require(not TEMP.exists() and not receipt.exists(), "Use fresh diagnostic paths")
    result = {
        "baseline_commit": BASELINE,
        "audit_sha256": sha(Path(__file__)),
        "goal_status": "paused",
    }
    try:
        result["statements"] = statements_equal()
        result["frozen_windows"] = replay_windows()
        result["synthetic_accounts"] = synthetic_accounts()
        result["status"] = "PASS"
    except Exception as error:
        result.update(status="FAIL", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        result["completed_at"] = now()
        save(receipt, result, immutable=True)
    print("PASS: extracted operations, frozen windows, shared evaluation and validation trials")
