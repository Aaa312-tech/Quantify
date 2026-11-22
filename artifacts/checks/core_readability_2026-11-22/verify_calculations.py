"""Compare features, cached training windows and selection with the published implementation."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from quantfolio import calibration, features, models, selection, training
from quantfolio.dataset import eligible_rows, load_bundle
from quantfolio.ledger import causal_price_index
from quantfolio.rules import CONFIGS
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, now, read, save, sha

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
BASELINE = ROOT / ".git/core_readability_baseline/quantfolio"
TEMP = ROOT / ".git/core_readability_calculations"


def equal(before, after):
    if isinstance(before, (pd.DataFrame, pd.Series)):
        check = (
            pd.testing.assert_frame_equal
            if isinstance(before, pd.DataFrame)
            else pd.testing.assert_series_equal
        )
        check(before, after, check_exact=True)
    elif isinstance(before, np.ndarray):
        np.testing.assert_array_equal(before, after, strict=True)
    elif isinstance(before, dict):
        if list(before) != list(after):
            raise ValueError("Mapping keys or order changed")
        for key in before:
            equal(before[key], after[key])
    elif isinstance(before, (tuple, list)):
        if type(before) is not type(after) or len(before) != len(after):
            raise ValueError("Sequence structure changed")
        for left, right in zip(before, after):
            equal(left, right)
    else:
        np.testing.assert_equal(before, after)


def baseline_package(scope):
    for name, digest in scope["original_runtime_sha256"].items():
        if sha(BASELINE / Path(name).name) != digest:
            raise ValueError(f"Baseline source changed: {name}")
    spec = importlib.util.spec_from_file_location(
        "quantfolio_baseline", BASELINE / "__init__.py", submodule_search_locations=[str(BASELINE)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    return {
        name: importlib.import_module(f"quantfolio_baseline.{name}")
        for name in ("features", "models", "calibration", "selection")
    }


def feature_checks(bundle, old):
    actions = {}
    for event in bundle["actions"]:
        actions.setdefault(event["symbol"], []).append(event)
    benchmark, _ = causal_price_index(bundle["raws"]["sh510300"], actions.get("sh510300", []))
    benchmark_returns = benchmark.close.pct_change(fill_method=None)
    records = []
    for symbol, raw in bundle["raws"].items():
        causal, splits = causal_price_index(raw, actions.get(symbol, []))
        normalized = raw.copy()
        normalized["volume_lots"] = raw.volume_lots / splits
        before = old.price_features(normalized, causal, benchmark_returns)
        after = features.price_features(normalized, causal, benchmark_returns)
        equal(before, after)
        equal(old.base_features(raw, before), features.base_features(raw, after))
        records.append({"symbol": symbol, "rows": len(raw), "columns": list(before)})
        if len(records) % 100 == 0:
            print(f"Feature histories matched: {len(records)}", flush=True)
    equal(old.momentum_features(bundle["table"]), features.momentum_features(bundle["table"]))
    equal(
        old.industry_features(bundle["table"], bundle["meta"], bundle["calendar"]),
        features.industry_features(bundle["table"], bundle["meta"], bundle["calendar"]),
    )
    return {
        "securities": len(records),
        "raw_rows": sum(row["rows"] for row in records),
        "panel_rows": len(bundle["table"]),
        "all_columns_and_dtypes_equal": True,
    }


def frozen_windows(paths, bundle, old):
    run = ROOT / scope["active_training"]
    records = sorted(run.glob("training/fold_*/result.json")) + [run / "final_fit/result.json"]
    completed, fit_calls, trial_calls = [], [], []
    active_trial = training.trial
    rejected_old_cache = []

    def no_fit(*args, **kwargs):
        raise ValueError("New model fitting is not allowed in this verification")

    for result_path in records:
        frozen = read(result_path)
        source = result_path.parent
        output = TEMP / "windows" / source.name
        table = bundle["table"]
        rows = eligible_rows(
            table, frozen["fold"]["validation_start"], frozen["fold"]["validation_end"]
        )
        predictions = {
            family: np.load(source / "validation_fit" / family / "predictions.npy")
            for family in ("ridge", "tree")
        }
        old_scores = old["models"].candidate_scores(table, rows, predictions)
        equal(old_scores, models.candidate_scores(table, rows, predictions))

        def cached_fit(bundle, train_rows, pred_rows, path, identity):
            equal(
                old["models"].training_targets(table, train_rows),
                models.training_targets(table, train_rows),
            )
            equal(
                old["selection"].clip_for(table, train_rows),
                selection.label_clip_bounds(table, train_rows),
            )
            target = source / path.name
            # Old cached fits retain their original source identity. No hash is substituted.
            values = old["models"].fit_stage(bundle, train_rows, pred_rows, target, identity)
            if not rejected_old_cache:
                try:
                    models.fit_stage(bundle, train_rows, pred_rows, target, identity)
                except ValueError as error:
                    if "Cached model identity or hash mismatch" not in str(error):
                        raise
                    rejected_old_cache.append(True)
                else:
                    raise ValueError("Changed model source incorrectly accepted the old fit cache")
            fit_calls.append(
                {
                    "fold": frozen["fold"]["fold"],
                    "stage": path.name,
                    "training_rows_sha256": array_sha(train_rows),
                    "prediction_rows_sha256": array_sha(pred_rows),
                }
            )
            return values

        def cached_trial(bundle, fold, forecasts, config, path, identity, **controls):
            candidate = "momentum" if path.parent.name == "inner_rules" else path.name
            expected_config = (
                path.name if path.parent.name == "inner_rules" else frozen["selected_rules"]
            )
            if config != expected_config or controls != {"turnover": "legacy"}:
                raise ValueError("Validation trial controls changed")
            expected_forecasts = old["models"].forecasts_by_date(
                table,
                rows,
                old_scores[candidate],
                old_scores["momentum"],
                frozen["initial_calibration"],
            )
            equal(expected_forecasts, forecasts)
            target = source / path.parent.name / path.name
            trial_calls.append(target.relative_to(run).as_posix())
            return active_trial(bundle, fold, forecasts, config, target, identity, **controls)

        with (
            patch.object(training, "fit_stage", cached_fit),
            patch.object(training, "trial", cached_trial),
            patch.object(training, "now", lambda: frozen["completed_at"]),
            patch.object(models, "make_model", no_fit),
            patch.object(old["models"], "make_model", no_fit),
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
        equal(frozen, actual)
        with (
            np.load(source / "outer_predictions.npz") as before,
            np.load(output / "outer_predictions.npz") as after,
        ):
            if before.files != after.files:
                raise ValueError("Forecast array names or order changed")
            for name in before.files:
                equal(before[name], after[name])
            for name in models.CANDIDATES:
                equal(
                    old["models"].prediction_map(table, before, name),
                    models.prediction_map(table, after, name),
                )
        if sha(output / "outer_predictions.npz") != frozen["predictions_sha256"]:
            raise ValueError("Frozen prediction archive bytes changed")
        if len(completed) in (0, 11):
            scores = predictions["ridge"]
            equal(
                old["models"].industry_holdout_check(
                    table, rows, scores, frozen["fold"]["test_start"]
                ),
                models.industry_holdout_check(table, rows, scores, frozen["fold"]["test_start"]),
            )
        completed.append(
            {
                "fold": frozen["fold"]["fold"],
                "all_result_fields_equal": True,
                "forecast_sha256": frozen["predictions_sha256"],
                "candidate": actual["selected_candidate"],
                "rules": actual["selected_rules"],
            }
        )
        print(f"Frozen training window matched: {source.name}", flush=True)
    if len(completed) != 12 or len(fit_calls) != 24 or not rejected_old_cache:
        raise ValueError("Incomplete window verification")
    return {
        "windows": completed,
        "fit_input_comparisons": fit_calls,
        "validation_forecast_comparisons": len(trial_calls),
        "old_fit_cache_rejected_by_current_source": True,
        "new_model_fits": 0,
    }


def selection_checks(old):
    def candidate(advantage=0.0):
        return {
            "returns": [0.01 + advantage] * 6,
            "worst_drawdown": -0.04,
            "continuous": {
                "return": 0.1 + advantage,
                "max_drawdown": -0.04,
                "unresolved_events": 0,
                "quality_issues": 0,
            },
            "unresolved_events": 0,
            "quality_issues": 0,
            "accounting_pass": True,
            "factor_check": {"pass": True},
        }

    cases = 0
    for positive in (False, True):
        for scenario in ("fallback", "qualify", "tie", "factor", "quality", "drawdown", "partial"):
            for kind, names, fallback in (
                ("rules", list(CONFIGS), "baseline"),
                ("model", models.CANDIDATES, "momentum"),
            ):
                trials = {
                    name: candidate(0 if name == fallback or scenario == "fallback" else 0.01)
                    for name in names
                }
                for index, (name, value) in enumerate(trials.items()):
                    if name == fallback:
                        continue
                    if scenario == "qualify":
                        value["returns"] = [0.02 + index * 0.001] * 6
                    elif scenario == "factor":
                        value["factor_check"]["pass"] = False
                    elif scenario == "quality":
                        value["quality_issues"] = 1
                    elif scenario == "drawdown":
                        value["continuous"]["max_drawdown"] = -0.081
                    elif scenario == "partial":
                        value["returns"] = value["returns"][:5]
                if scenario == "partial":
                    trials[fallback]["returns"] = trials[fallback]["returns"][:5]
                before, after = copy.deepcopy(trials), copy.deepcopy(trials)
                equal(
                    getattr(old, "select_" + kind)(before, positive_score=positive),
                    getattr(selection, "select_" + kind)(after, positive_score=positive),
                )
                equal(before, after)
                cases += 1
    return {"cases": cases, "selection_and_adoption_records_equal": True}


def forecast_edges(table, old):
    cases = 0
    cal = {"centers": [0, 0.5, 1], "mean": [-0.1, 0, 0.1], "standard_error": [0.1, 0.05, 0.1]}
    for rows in (
        np.arange(10),
        np.arange(9, -1, -1),
        np.array([], dtype=int),
        np.array([0, 0, 2, 1, 2]),
    ):
        for dtype in (np.float32, np.float64):
            ranks = np.linspace(0, 1, len(rows), dtype=dtype)
            equal(
                old.forecasts_by_date(table, rows, ranks, ranks, cal),
                models.forecasts_by_date(table, rows, ranks, ranks, cal),
            )
            means, margins = calibration.map_calibration(ranks, cal)
            arrays = {
                "row_indices": rows,
                "test_rank": ranks,
                "common_mu": means,
                "common_margin": margins,
            }
            equal(
                old.prediction_map(table, arrays, "test"),
                models.prediction_map(table, arrays, "test"),
            )
            cases += 2
    return {"cases": cases, "empty_reverse_repeated_rows_and_float_dtypes_equal": True}


scope = read(OUTPUT / "scope.json")
runtime = {path.name: sha(path) for path in sorted((ROOT / "src/quantfolio").glob("*.py"))}
result = {
    "kind": "EXECUTION_DIFFERENTIAL",
    "baseline_commit": scope["baseline_commit"],
    "scope_sha256": sha(OUTPUT / "scope.json"),
    "audit_sha256": sha(Path(__file__)),
    "runtime_sha256": runtime,
    "subjects": {
        key: {"sha256": value["sha256"], "source_sha256": value["sources"]}
        for key, value in scope["subjects"].items()
    },
}
try:
    if TEMP.exists():
        raise FileExistsError(TEMP)
    if read(OUTPUT / "syntax_bindings.json")["status"] != "PASS":
        raise ValueError("Calculation syntax has not passed")
    TEMP.mkdir()
    old = baseline_package(scope)
    paths = Paths(ROOT)
    bundle = load_bundle(paths)
    result["features"] = feature_checks(bundle, old["features"])
    result["frozen_training"] = frozen_windows(paths, bundle, old)
    result["selection"] = selection_checks(old["selection"])
    result["forecast_edges"] = forecast_edges(bundle["table"], old["models"])
    if runtime != {path.name: sha(path) for path in sorted((ROOT / "src/quantfolio").glob("*.py"))}:
        raise ValueError("Source changed during verification")
    result.update(
        status="PASS", syntax_sha256=sha(OUTPUT / "syntax_bindings.json"), new_model_fits=0
    )
except Exception as error:
    result.update(status="FAIL", error=f"{type(error).__name__}: {error}")
    raise
finally:
    result["completed_at"] = now()
    save(OUTPUT / "differential.json", result, immutable=True)
print("PASS: features, frozen training windows, selection and forecast edges")
