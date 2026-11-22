"""Replay a registered research baseline without fitting models or adopting new results."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import logging
import platform
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

from . import dataset, evaluation, features, models, training, verification
from .dataset import eligible_rows, validate_features
from .ledger import causal_price_index
from .planning import register_output
from .storage import array_sha, digest, load_gzip, log, now, read, save, sha


def compare_values(expected, actual, path="result"):
    """Compare ordered fields, array dtypes and values; report the first failing location."""
    if isinstance(expected, pd.Index):
        pd.testing.assert_index_equal(expected, actual, exact=True, obj=path)
    elif isinstance(expected, (pd.DataFrame, pd.Series)):
        check = (
            pd.testing.assert_frame_equal
            if isinstance(expected, pd.DataFrame)
            else pd.testing.assert_series_equal
        )
        check(expected, actual, check_exact=True, obj=path)
    elif isinstance(expected, np.ndarray):
        np.testing.assert_array_equal(expected, actual, strict=True, err_msg=path)
    elif isinstance(expected, dict):
        if not isinstance(actual, dict) or list(expected) != list(actual):
            raise ValueError(f"Mapping fields or order differ: {path}")
        for key, value in expected.items():
            compare_values(value, actual[key], f"{path}/{key}")
    elif isinstance(expected, (tuple, list)):
        if type(expected) is not type(actual) or len(expected) != len(actual):
            raise ValueError(f"Sequence type or length differs: {path}")
        for index, (before, after) in enumerate(zip(expected, actual)):
            compare_values(before, after, f"{path}/{index}")
    else:
        np.testing.assert_equal(actual, expected, err_msg=path)


def runtime_sources():
    return {path.name: sha(path) for path in sorted(Path(__file__).parent.glob("*.py"))}


def checked_file(root, relative, expected):
    root = root.resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or invalid registered input: {relative}")
    if path.stat().st_size < 1024 and path.read_bytes().startswith(
        b"version https://git-lfs.github.com/spec/v1"
    ):
        raise ValueError(f"Missing Git LFS object: {relative}. Run 'git lfs pull'.")
    if sha(path) != expected:
        raise ValueError(f"Registered input hash differs: {relative}")
    return path


def read_baseline(paths):
    with (paths.root / "configs/regression.toml").open("rb") as stream:
        config = tomllib.load(stream)
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported regression configuration")
    manifest = read(paths.manifest)["files"]
    records = {}
    for name in ("scope", "receipt", "compatibility"):
        relative, expected = config[name], config[name + "_sha256"]
        if manifest.get(relative) != expected:
            raise ValueError(f"Regression {name} is not frozen")
        records[name] = read(checked_file(paths.root, relative, expected))
    scope, receipt = records["scope"], records["receipt"]
    if (
        receipt["status"] != "PASS"
        or receipt["scope_sha256"] != config["scope_sha256"]
        or scope["baseline_commit"] != config["baseline_commit"]
        or scope["configuration"] != paths.configuration_path.relative_to(paths.root).as_posix()
    ):
        raise ValueError("Regression baseline identity or completion differs")
    original = {
        Path(name).name: value
        for name, value in scope["source_sha256"].items()
        if name.endswith(".py")
    }
    certificate = records["compatibility"]
    if certificate["runtime_sha256"] != original:
        raise ValueError("Baseline source compatibility belongs to different code")
    return {
        **scope,
        "subjects": certificate["subjects"],
        "configuration_sha256": {paths.config["archive"]: config["archive_sha256"]},
        "profile_sha256": sha(paths.root / "configs/regression.toml"),
    }


def check_inputs(paths, baseline):
    manifest = read(paths.manifest)["files"]
    registered = baseline["inputs_sha256"]
    if not registered:
        raise ValueError("Empty regression input inventory")
    for relative, expected in registered.items():
        # The active project configuration is versioned code, rather than a frozen artifact.
        if relative != baseline["configuration"] and manifest.get(relative) != expected:
            raise ValueError(f"Input is no longer registered: {relative}")
        checked_file(paths.root, relative, expected)
    for relative, subject in baseline["subjects"].items():
        if manifest.get(relative) != subject["sha256"]:
            raise ValueError(f"Historical subject is no longer registered: {relative}")
        checked_file(paths.root, relative, subject["sha256"])
    for relative, expected in baseline.get("configuration_sha256", {}).items():
        checked_file(paths.root, relative, expected)
    for folder in (baseline["training"], baseline["evaluation"]):
        expected = {name for name in registered if name.startswith(folder + "/")}
        actual = {
            path.relative_to(paths.root).as_posix()
            for path in (paths.root / folder).rglob("*")
            if path.is_file()
        }
        if actual != expected:
            raise ValueError(f"Research artifact inventory differs: {folder}")


@contextmanager
def baseline_package(paths, baseline, revision):
    command = ["git", "-C", str(paths.root)]
    resolved = subprocess.run(
        [*command, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
        capture_output=True,
        text=True,
    )
    if resolved.returncode:
        raise ValueError(
            "Baseline commit is unavailable; fetch the registered commit before replay"
        )
    commit = resolved.stdout.strip()
    if commit != baseline["baseline_commit"]:
        raise ValueError("Requested baseline differs from the registered commit")
    namespace = "_quantfolio_regression_baseline"
    if namespace in sys.modules:
        raise ValueError("A baseline replay is already running in this process")
    temp_root = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="quantfolio-regression-", dir=temp_root) as temporary:
        work = Path(temporary).resolve()
        if not work.is_relative_to(temp_root):
            raise ValueError("Temporary baseline escapes its directory")
        for relative, expected in baseline["source_sha256"].items():
            member = Path(relative)
            if (
                member.is_absolute()
                or member.parts[:2] != ("src", "quantfolio")
                or ".." in member.parts
            ):
                raise ValueError("Invalid baseline source member")
            result = subprocess.run([*command, "show", f"{commit}:{relative}"], capture_output=True)
            if result.returncode or hashlib.sha256(result.stdout).hexdigest() != expected:
                raise ValueError(f"Baseline source differs: {relative}")
            target = work / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(result.stdout)
        package_dir = work / "src/quantfolio"
        spec = importlib.util.spec_from_file_location(
            namespace, package_dir / "__init__.py", submodule_search_locations=[str(package_dir)]
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[namespace] = package
        try:
            spec.loader.exec_module(package)
            for name in ("dataset", "evaluation", "models", "features", "training", "verification"):
                importlib.import_module(namespace + "." + name)
            yield package, work
        finally:
            for name in list(sys.modules):
                if name == namespace or name.startswith(namespace + "."):
                    del sys.modules[name]


def compare_features(bundle, previous, scope):
    actions = {}
    for action in bundle["actions"]:
        actions.setdefault(action["symbol"], []).append(action)
    benchmark, _ = causal_price_index(bundle["raws"]["sh510300"], actions.get("sh510300", []))
    old_benchmark, _ = previous.ledger.causal_price_index(
        bundle["raws"]["sh510300"], actions.get("sh510300", [])
    )
    compare_values(old_benchmark, benchmark, "benchmark_adjustment")
    benchmark_returns = benchmark.close.pct_change(fill_method=None)
    symbols = list(bundle["raws"])
    if scope == "core":
        symbols = symbols[:10]
    rows = 0
    for symbol in symbols:
        raw = bundle["raws"][symbol]
        adjusted, splits = causal_price_index(raw, actions.get(symbol, []))
        old_adjusted, old_splits = previous.ledger.causal_price_index(raw, actions.get(symbol, []))
        compare_values(old_adjusted, adjusted, symbol + "/adjusted_prices")
        compare_values(old_splits, splits, symbol + "/share_adjustment")
        normalized = raw.copy()
        normalized["volume_lots"] = raw.volume_lots / splits
        before = previous.features.price_features(normalized, adjusted, benchmark_returns)
        actual = features.price_features(normalized, adjusted, benchmark_returns)
        compare_values(before, actual, symbol + "/price_features")
        compare_values(
            previous.features.base_features(raw, before),
            features.base_features(raw, actual),
            symbol + "/base_features",
        )
        rows += len(raw)
    compare_values(
        previous.features.momentum_features(bundle["table"]),
        features.momentum_features(bundle["table"]),
        "momentum_features",
    )
    compare_values(
        previous.features.industry_features(bundle["table"], bundle["meta"], bundle["calendar"]),
        features.industry_features(bundle["table"], bundle["meta"], bundle["calendar"]),
        "industry_features",
    )
    return {
        "securities": len(symbols),
        "raw_rows": rows,
        "all_columns_and_dtypes_equal": True,
        "frozen_features": validate_features(bundle),
    }


def replay_windows(
    bundle, source, folders, training_module, model_module, reference_models, specification, output
):
    records, fits = [], []
    table = bundle["table"]
    constructors = {name: model_module.make_model(name) for name in ("ridge", "tree")}
    for name, model in constructors.items():
        if joblib.hash(model) != joblib.hash(reference_models.make_model(name)):
            raise ValueError(f"Model construction changed: {name}")

    for folder in folders:
        frozen = read(folder / "result.json")
        validation_rows = eligible_rows(
            table, frozen["fold"]["validation_start"], frozen["fold"]["validation_end"]
        )
        predictions = {
            name: np.load(folder / "validation_fit" / name / "predictions.npy")
            for name in constructors
        }
        reference_scores = reference_models.candidate_scores(table, validation_rows, predictions)
        calls = []

        def cached_fit(current_bundle, train_rows, prediction_rows, target, identity):
            fit_rows, targets, weights, details = model_module.training_targets(table, train_rows)
            fingerprint = {
                **identity,
                "train_rows_sha256": array_sha(train_rows),
                "fit_rows_sha256": array_sha(fit_rows),
                "prediction_rows_sha256": array_sha(prediction_rows),
                "model_code_sha256": specification["source_sha256"]["models.py"],
            }
            values = {}
            for name, model in constructors.items():
                cached = folder / target.name / name
                metadata = read(cached / "fit.json")
                if metadata["identity"] != digest({**fingerprint, "family": name}):
                    raise ValueError(f"Cached fit rows or identity differ: {cached}")
                compare_values(metadata["target_details"], details, "target_details")
                compare_values(
                    metadata["target_bounds"],
                    [float(targets.min()), float(targets.max())],
                    "target_bounds",
                )
                compare_values(
                    metadata["parameters"], model.steps[-1][1].get_params(), "model_parameters"
                )
                if metadata["train_samples"] != len(fit_rows) or metadata[
                    "prediction_samples"
                ] != len(prediction_rows):
                    raise ValueError("Cached fit sample count differs")
                checked_file(
                    source,
                    cached.relative_to(source).as_posix() + "/model.joblib",
                    metadata["model_sha256"],
                )
                checked_file(
                    source,
                    cached.relative_to(source).as_posix() + "/predictions.npy",
                    metadata["predictions_sha256"],
                )
                values[name] = np.load(cached / "predictions.npy")
            fits.append(
                {
                    "window": folder.name,
                    "stage": target.name,
                    **fingerprint,
                    "target_sha256": array_sha(targets),
                    "weights_sha256": array_sha(weights),
                }
            )
            return values

        def cached_trial(current_bundle, fold, forecasts, config, target, identity, **controls):
            rules_trial = target.parent.name == "inner_rules"
            candidate = "momentum" if rules_trial else target.name
            expected_config = target.name if rules_trial else frozen["selected_rules"]
            if (
                config != expected_config
                or fold != frozen["fold"]
                or controls != {"turnover": specification.get("turnover_policy", "legacy")}
            ):
                raise ValueError("Validation trial controls changed")
            expected_forecasts = reference_models.forecasts_by_date(
                table,
                validation_rows,
                reference_scores[candidate],
                reference_scores["momentum"],
                frozen["initial_calibration"],
            )
            compare_values(expected_forecasts, forecasts, "validation_forecasts/" + candidate)
            cached = folder / target.parent.name / target.name
            summary = read(cached.with_suffix(".json"))
            if (
                summary["identity"] != identity
                or sha(cached.with_suffix(".ledgers.json.gz")) != summary["ledger_sha256"]
            ):
                raise ValueError("Cached validation trial identity or ledger differs")
            calls.append(cached.relative_to(source).as_posix())
            return summary

        def no_fit(*args, **kwargs):
            raise ValueError("Model fitting is forbidden during regression")

        destination = output / folder.name
        with (
            patch.object(training_module, "fit_stage", cached_fit),
            patch.object(training_module, "trial", cached_trial),
            patch.object(training_module, "now", lambda: frozen["completed_at"]),
            patch.object(model_module, "make_model", no_fit),
        ):
            actual = training_module.run_window(
                bundle,
                frozen["fold"],
                destination,
                frozen["identity"],
                final=frozen["final_fit"],
                positive_score=specification.get("selection_mode", "legacy") == "positive_score",
                turnover=specification.get("turnover_policy", "legacy"),
            )
        compare_values(frozen, actual, folder.name + "/result")
        with (
            np.load(folder / "outer_predictions.npz") as expected,
            np.load(destination / "outer_predictions.npz") as actual,
        ):
            if expected.files != actual.files:
                raise ValueError("Forecast array inventory or order differs")
            for name in expected.files:
                compare_values(expected[name], actual[name], folder.name + "/" + name)
        if sha(destination / "outer_predictions.npz") != frozen["predictions_sha256"]:
            raise ValueError("Compressed forecast bytes differ")
        records.append(
            {
                "window": folder.name,
                "forecast_sha256": frozen["predictions_sha256"],
                "validation_forecast_comparisons": len(calls),
                "all_result_fields_equal": True,
            }
        )
        log("Replayed frozen training window", source=str(source), window=folder.name)
    if len(fits) != 2 * len(folders):
        raise ValueError("Incomplete fit-input replay")
    return {
        "windows": records,
        "fit_inputs": fits,
        "calibrations_recomputed": 2 * len(folders),
        "new_model_fits": 0,
    }


def replay_history(
    bundle, maps, schedule, start, end, reference, source, output, module, scope, turnover
):
    costs = [10, 20, 30] if scope == "full" else [10]
    policies = list(reference["results"]["10bps"]) if scope == "full" else ["selected_strategy"]
    actual = module.evaluate_accounts(
        bundle, maps, schedule, start, end, costs, policies, output, turnover=turnover
    )
    for field in ("results", "continuous"):
        expected = {
            f"{cost:02d}bps": {name: reference[field][f"{cost:02d}bps"][name] for name in policies}
            for cost in costs
        }
        compare_values(expected, actual[field], field)
    expected_files = {
        relative: reference["artifacts"][relative]
        for cost in costs
        for policy in policies
        for relative in (
            f"evaluation/{cost:02d}bps/{policy}.ledgers.json.gz",
            f"evaluation/continuous/{cost:02d}bps/{policy}.json.gz",
        )
    }
    compare_values(expected_files, actual["artifacts"], "historical_inventory")
    records, accounts = [], 0
    for relative, expected in expected_files.items():
        if sha(source / relative) != expected or sha(output / relative) != expected:
            raise ValueError(f"Historical ledger bytes differ: {relative}")
        stored = load_gzip(source / relative)
        accounts += len(stored) if isinstance(stored, list) else 1
        records.append({"path": relative, "before": expected, "after": sha(output / relative)})
    if accounts != actual["accounts"]:
        raise ValueError("Historical account count differs")
    if scope == "full":
        for key, value in actual.items():
            compare_values(reference[key], value, key)
    return {
        "accounts": accounts,
        "ledger_files": len(records),
        "all_result_fields_equal": True,
        "records": records,
    }


def scope_counts(report):
    try:
        return {
            "models": len(report["models"]),
            "training_windows": len(report["training"]["windows"]),
            "fit_stages": len(report["training"]["fit_inputs"]),
            "historical_accounts": report["history"]["accounts"],
            "ledger_files": report["history"]["ledger_files"],
            "paper_accounts": report["paper"]["accounts"],
            "paper_sessions": report["paper"]["sessions"],
            "securities": report["features"]["securities"],
            "features": report["features"]["frozen_features"]["features"],
        }
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete regression result inventory") from error


def register_compatibility(paths, output):
    """Register only completed full evidence bound to the current sources and inputs."""
    report = read(output / "result.json")
    baseline = read_baseline(paths)
    check_inputs(paths, baseline)
    sources = runtime_sources()
    if (
        report["status"] != "PASS"
        or report["scope"] != "full"
        or report["source_sha256"] != sources
        or report["profile_sha256"] != baseline["profile_sha256"]
        or report["baseline_commit"] != baseline["baseline_commit"]
    ):
        raise ValueError("Only a current, complete full regression can be registered")
    compare_values(baseline["expected"], scope_counts(report), "registration_scope")
    if report.get("new_model_fits") != 0 or any(
        item["max_error"] != 0 for item in report["models"]
    ):
        raise ValueError("Regression must use unchanged saved predictions without new fits")
    evidence = {}
    for filename, kind in (
        ("differential.json", "EXECUTION_DIFFERENTIAL"),
        ("history.json", "HISTORICAL_LEDGER_PARITY"),
    ):
        path = output / filename
        item = read(path)
        if (
            item["status"] != "PASS"
            or item["kind"] != kind
            or item["scope"] != "full"
            or item["runtime_sha256"] != sources
            or item["subjects"] != baseline["subjects"]
            or item["profile_sha256"] != baseline["profile_sha256"]
        ):
            raise ValueError("Regression evidence does not bind the current implementation")
        fields = ("features", "training", "models") if filename == "differential.json" else ()
        for field in fields:
            compare_values(report[field], item[field], "registered_" + field)
        if filename == "history.json":
            compare_values(
                report["history"],
                {key: item[key] for key in report["history"]},
                "registered_history",
            )
            if len(item["records"]) != baseline["expected"]["ledger_files"]:
                raise ValueError("Incomplete historical ledger evidence")
        evidence[kind] = {"path": path.relative_to(paths.root).as_posix(), "sha256": sha(path)}
    save(
        output / "source_compatibility.json",
        {
            "schema_version": 1,
            "kind": "EXECUTION_REFACTOR",
            "baseline_commit": baseline["baseline_commit"],
            "runtime_sha256": sources,
            "subjects": baseline["subjects"],
            "evidence": evidence,
        },
        immutable=True,
    )
    register_output(paths, output)


def run_regression(paths, output, baseline, *, scope="core", register=False):
    if scope not in {"core", "full"} or (register and scope != "full"):
        raise ValueError("Compatibility registration requires --scope full")
    output = paths.output_path(output, "checks")
    output.mkdir(parents=True, exist_ok=False)
    sources = runtime_sources()
    started = time.perf_counter()
    revision = subprocess.run(
        ["git", "-C", str(paths.root), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    changes = subprocess.run(
        ["git", "-C", str(paths.root), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
    )
    header = {
        "schema_version": 1,
        "started_at": now(),
        "scope": scope,
        "baseline": baseline,
        "registration_requested": register,
        "source_sha256": sources,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "scipy", "scikit-learn", "joblib")
        },
    }
    header["git_commit"] = revision.stdout.strip() if revision.returncode == 0 else None
    header["working_tree_changes"] = (
        changes.stdout.splitlines() if changes.returncode == 0 else None
    )
    header["command"] = [
        "quantfolio",
        "--project",
        str(paths.root),
        "--config",
        str(paths.config_file),
        "regression",
        "--scope",
        scope,
        "--baseline",
        baseline,
        "--output",
        str(output),
    ] + (["--register"] if register else [])
    save(output / "run.json", header, immutable=True)
    logger = logging.getLogger("quantfolio")
    handler = logging.FileHandler(output / "run.log", mode="x", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    report = {"status": "FAIL", "scope": scope, "source_sha256": sources, "new_model_fits": 0}
    stage = "inputs"
    try:
        profile = read_baseline(paths)
        check_inputs(paths, profile)
        log("Registered regression inputs verified", scope=scope)
        report["profile_sha256"] = profile["profile_sha256"]
        report["baseline_commit"] = profile["baseline_commit"]
        with baseline_package(paths, profile, baseline) as (previous, temporary):
            original_paths = previous.settings.Paths(paths.root, paths.config_file)
            bundle = previous.dataset.load_bundle(original_paths)
            compare_values(bundle, dataset.load_bundle(paths), "loaded_dataset")
            log("Baseline and current dataset loading match")
            source = paths.root / profile["training"]
            evaluated = paths.root / profile["evaluation"]
            reference = read(evaluated / "evaluation_summary.json")
            status, folds = previous.evaluation.experiment_folds(original_paths, bundle, source)
            specification = read(source / "protocol.json")["specification"]
            maps, schedule, _, start, end = previous.evaluation.load_predictions(
                bundle, source, folds, status["identity"]
            )
            current_maps, current_schedule, _, current_start, current_end = (
                evaluation.load_predictions(bundle, source, folds, status["identity"])
            )
            compare_values(
                (maps, schedule, start, end),
                (current_maps, current_schedule, current_start, current_end),
                "forecast_mapping",
            )
            folders = [source / "training" / f"fold_{fold['fold']:02d}" for fold in folds]
            if status["latest_fit_included"]:
                folders.append(source / "final_fit")
            if len(folders) != profile["expected"]["training_windows"]:
                raise ValueError("Registered window count differs")
            if scope == "core":
                folders = folders[:1]
            stage = "models"
            log("Checking saved estimators", windows=len(folders))
            report["models"] = verification.verify_models(
                bundle, [folder / "result.json" for folder in folders], paths.root
            )
            if len(report["models"]) != 4 * len(folders) or any(
                row["max_error"] != 0 for row in report["models"]
            ):
                raise ValueError("Model predictions or inventory differ from the exact baseline")
            stage = "features"
            log("Comparing feature calculations", scope=scope)
            report["features"] = compare_features(bundle, previous, scope)
            stage = "training_windows"
            old_windows = replay_windows(
                bundle,
                source,
                folders,
                previous.training,
                previous.models,
                previous.models,
                specification,
                temporary / "old_windows",
            )
            current_windows = replay_windows(
                bundle,
                source,
                folders,
                training,
                models,
                previous.models,
                specification,
                temporary / "current_windows",
            )
            compare_values(old_windows, current_windows, "training_replay")
            report["training"] = current_windows
            common = {
                "baseline_commit": profile["baseline_commit"],
                "profile_sha256": profile["profile_sha256"],
                "runtime_sha256": sources,
                "subjects": profile["subjects"],
                "scope": scope,
                "status": "PASS",
            }
            save(
                output / "differential.json",
                {
                    **common,
                    "kind": "EXECUTION_DIFFERENTIAL" if scope == "full" else "CORE_DIFFERENTIAL",
                    "features": report["features"],
                    "training": current_windows,
                    "models": report["models"],
                    "new_model_fits": 0,
                },
                immutable=True,
            )
            stage = "historical_accounts"
            original_accounts = replay_history(
                bundle,
                maps,
                schedule,
                start,
                end,
                reference,
                evaluated,
                temporary / "old_accounts",
                previous.evaluation,
                scope,
                paths.config.get("turnover_policy", "legacy"),
            )
            current_accounts = replay_history(
                bundle,
                maps,
                schedule,
                start,
                end,
                reference,
                evaluated,
                temporary / "current_accounts",
                evaluation,
                scope,
                paths.config.get("turnover_policy", "legacy"),
            )
            compare_values(original_accounts, current_accounts, "historical_accounts")
            report["history"] = {k: v for k, v in current_accounts.items() if k != "records"}
            save(
                output / "history.json",
                {
                    **common,
                    "kind": "HISTORICAL_LEDGER_PARITY"
                    if scope == "full"
                    else "CORE_HISTORICAL_PARITY",
                    **current_accounts,
                },
                immutable=True,
            )
            stage = "paper_accounts"
            previous_replay = (
                getattr(previous.verification, "replay_paper_account", None)
                or previous.verification._paper_account
            )
            paper_options = {
                "costs": (10, 20, 30) if scope == "full" else (10,),
                "account_limit": None if scope == "full" else 2,
            }
            original_paper = verification.compare_paper_accounts(
                bundle,
                maps["selected"],
                schedule,
                evaluated,
                reference,
                replay=previous_replay,
                turnover=paths.config.get("turnover_policy", "legacy"),
                **paper_options,
            )
            current_paper = verification.compare_paper_accounts(
                bundle,
                maps["selected"],
                schedule,
                evaluated,
                reference,
                turnover=paths.config.get("turnover_policy", "legacy"),
                **paper_options,
            )
            compare_values(original_paper, current_paper, "paper_accounts")
            if current_paper["status"] != "PASS":
                raise ValueError(current_paper["error"])
            report["paper"] = {k: v for k, v in current_paper.items() if k != "records"}
            save(output / "paper.json", {**common, **current_paper}, immutable=True)
            if scope == "full":
                compare_values(profile["expected"], scope_counts(report), "full_scope")
        stage = "final_integrity"
        check_inputs(paths, profile)
        if runtime_sources() != sources:
            raise ValueError("Implementation changed during regression")
        report["status"] = "PASS"
    except Exception as error:
        report["failed_stage"] = stage
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["completed_at"] = now()
        report["elapsed_seconds"] = time.perf_counter() - started
        try:
            save(output / "result.json", report, immutable=True)
        finally:
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(old_level)
    if register:
        register_compatibility(paths, output)
    return {
        "status": report["status"],
        "scope": scope,
        "result": str(output / "result.json"),
        "registered": register,
        "new_model_fits": 0,
    }
