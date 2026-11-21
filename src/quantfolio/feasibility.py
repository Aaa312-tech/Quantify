"""One registered comparison of minimal sell-budget relaxation and unchanged execution."""

from pathlib import Path

import numpy as np

from .dataset import check_integrity, load_bundle
from .evaluation import evaluate, evaluate_accounts, first_difference, load_predictions
from .settings import Paths
from .storage import digest, load_gzip, now, read, save, sha
from .training import run_training
from .verification import verify

PROTOCOL = "configs/protocols/exit_budget_repair.json"
ARMS = {"legacy": "legacy_training", "exit_priority": "repair_training"}


def study_paths(paths, turnover):
    configured = Paths(paths.root)
    configured.config.update(
        protocol=PROTOCOL, selection_mode="positive_score", turnover_policy=turnover
    )
    return configured


def registered_inputs(paths):
    check_integrity(paths)
    protocol = read(paths.root / PROTOCOL)
    if protocol["candidate"] != "exit_priority" or sha(paths.bundle) != protocol["data_sha256"]:
        raise ValueError("Exit-budget study inputs differ from the registered protocol")
    baseline = paths.artifact_path(protocol["baseline_training"], "experiments")
    evaluation = paths.artifact_path(protocol["baseline_evaluation"], "evaluations")
    if (
        sha(baseline / "protocol.json") != protocol["baseline_protocol_sha256"]
        or sha(evaluation / "evaluation_summary.json") != protocol["baseline_evaluation_sha256"]
    ):
        raise ValueError("The registered exit-budget baseline changed")
    return protocol, baseline, evaluation


def run_exit_study(paths, output):
    output = paths.output_path(output, "experiments")
    registered_inputs(paths)
    specification = {
        "plan_sha256": sha(paths.root / PROTOCOL),
        "data_sha256": sha(paths.bundle),
        "source_sha256": {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "arms": ARMS,
    }
    identity = digest(specification)
    save(
        output / "protocol.json",
        {"registered_at": now(), "identity": identity, "specification": specification},
        immutable=True,
    )
    arms = {}
    for turnover, folder in ARMS.items():
        configured = study_paths(paths, turnover)
        result = run_training(configured, output / folder, include_latest=True)
        checked = verify(configured, output / "verification" / turnover, run=output / folder)
        expected = len(_fit_inventory(output / folder)["models"])
        if checked["models_reloaded"] != expected:
            raise ValueError(f"The exit-budget study requires all {expected} registered models")
        arms[turnover] = {"training": result, "models_reloaded": checked["models_reloaded"]}
    status = {"status": "COMPLETE", "completed_at": now(), "identity": identity, "arms": arms}
    save(output / "study_status.json", status, immutable=True)
    return status


def _fit_inventory(run):
    """Require the complete artifact inventory declared by a completed training range."""
    registered = read(run / "protocol.json")
    specification = registered["specification"]
    status = read(run / "training_status.json")
    identity = digest(specification)
    if registered["identity"] != identity or status["identity"] != identity:
        raise ValueError("Fit comparison training identity differs")
    folds = specification["folds"]
    first, last = specification["selected_folds"]
    latest = specification["include_latest"]
    if (
        not 1 <= first <= last <= len(folds)
        or [fold["fold"] for fold in folds] != list(range(1, len(folds) + 1))
        or type(latest) is not bool
        or status["latest_fit_included"] is not latest
        or status["folds_completed"] != last - first + 1
        or status["status"] != ("COMPLETE" if first == 1 and last == len(folds) else "PARTIAL")
    ):
        raise ValueError("Fit comparison training range is incomplete or inconsistent")
    folders = {f"training/fold_{fold['fold']:02d}": fold for fold in folds[first - 1 : last]}
    if latest:
        folders["final_fit"] = None
    inventory = {
        "models": sorted(
            f"{folder}/{stage}/{family}/model.joblib"
            for folder in folders
            for stage in ("validation_fit", "outer_fit")
            for family in ("ridge", "tree")
        ),
        "forecasts": sorted(f"{folder}/outer_predictions.npz" for folder in folders),
    }
    for key, filename in (("models", "model.joblib"), ("forecasts", "outer_predictions.npz")):
        actual = sorted(file.relative_to(run).as_posix() for file in run.rglob(filename))
        if actual != inventory[key]:
            raise ValueError(f"Fit comparison {key} inventory differs from its registered range")
    for folder, fold in folders.items():
        result = read(run / folder / "result.json")
        if (
            result["identity"] != identity
            or result["final_fit"] is not (fold is None)
            or (fold is not None and result["fold"] != fold)
            or (fold is None and result["fold"]["fold"] != "final")
        ):
            raise ValueError("Fit comparison window identity differs")
    return inventory


def fit_parity(baseline, current):
    """Compare actual estimator bytes, saved predictions and complete outer forecast arrays."""
    inventory = _fit_inventory(baseline)
    if _fit_inventory(current) != inventory:
        raise ValueError("Fit comparison runs cover different registered artifacts")
    models = []
    for name in inventory["models"]:
        relative = Path(name)
        original = baseline / relative
        same = sha(original) == sha(current / relative)
        before = np.load(original.with_name("predictions.npy"))
        after = np.load((current / relative).with_name("predictions.npy"))
        predictions = before.dtype == after.dtype and np.array_equal(before, after)
        models.append(
            {"path": relative.as_posix(), "model_equal": same, "predictions_equal": predictions}
        )
    forecasts = []
    choices = []
    for name in inventory["forecasts"]:
        relative = Path(name)
        original = baseline / relative
        with np.load(original) as before, np.load(current / relative) as after:
            equal = set(before.files) == set(after.files) and all(
                before[k].dtype == after[k].dtype and np.array_equal(before[k], after[k])
                for k in before.files
            )
        forecasts.append({"path": relative.as_posix(), "arrays_equal": equal})
        old = read(original.with_name("result.json"))
        new = read((current / relative).with_name("result.json"))
        keys = ("selected_candidate", "selected_rules")
        choices.append(
            {
                "fold": old["fold"]["fold"],
                "before": {key: old[key] for key in keys},
                "after": {key: new[key] for key in keys},
            }
        )
    passed = all(r["model_equal"] and r["predictions_equal"] for r in models) and all(
        r["arrays_equal"] for r in forecasts
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "models": models,
        "forecasts": forecasts,
        "choices": choices,
    }


def account_differences(before, after, artifacts, *, exact=False):
    records = []
    for relative in sorted(artifacts):
        old, new = load_gzip(before / relative), load_gzip(after / relative)
        old_accounts = old if isinstance(old, list) else [old]
        new_accounts = new if isinstance(new, list) else [new]
        if len(old_accounts) != len(new_accounts):
            raise ValueError("Exit-budget comparison account boundaries changed")
        for index, (original, current) in enumerate(zip(old_accounts, new_accounts)):
            equal = original == current
            if exact and not equal:
                raise ValueError(f"Legacy behavior changed: {relative}/{index}")
            records.append(
                {
                    "path": relative,
                    "account_index": index,
                    "full_account_equal": equal,
                    "before": original["summary"],
                    "after": current["summary"],
                    "first_economic_difference": {
                        key: first_difference(original["ledger"][key], current["ledger"][key])
                        for key in ("trades", "events", "rejections", "curve")
                    },
                }
            )
    return records


def evaluate_exit_study(paths, run, output):
    run = paths.artifact_path(run, "experiments")
    output = paths.output_path(output, "evaluations")
    protocol, baseline, previous = registered_inputs(paths)
    registered = read(run / "protocol.json")
    status = read(run / "study_status.json")
    specification = registered["specification"]
    if (
        status["status"] != "COMPLETE"
        or status["identity"] != registered["identity"]
        or registered["identity"] != digest(specification)
        or specification["plan_sha256"] != sha(paths.root / PROTOCOL)
        or specification["data_sha256"] != sha(paths.bundle)
    ):
        raise ValueError("Exit-budget study is incomplete or belongs to other inputs")
    for name, expected in specification["source_sha256"].items():
        if sha(Path(__file__).with_name(name)) != expected:
            raise ValueError("Exit-budget study source changed after registration: " + name)
    parity = {arm: fit_parity(baseline, run / folder) for arm, folder in ARMS.items()}
    if any(result["status"] != "PASS" for result in parity.values()):
        raise ValueError("The exit-budget correction changed a model or frozen forecast")
    if any(r["before"] != r["after"] for r in parity["legacy"]["choices"]):
        raise ValueError("Legacy model or rule selection changed")
    save(output / "fit_parity.json", parity, immutable=True)
    results = {}
    for arm, folder in ARMS.items():
        results[arm] = evaluate(
            study_paths(paths, arm),
            run / folder,
            output / arm,
            [10, 20, 30],
            all_policies=arm == "legacy",
        )
    original = read(previous / "evaluation_summary.json")
    for relative, expected in original["artifacts"].items():
        if sha(previous / relative) != expected:
            raise ValueError("The frozen comparison ledger changed")
    legacy = account_differences(previous, output / "legacy", original["artifacts"], exact=True)
    save(output / "legacy_parity.json", {"status": "PASS", "accounts": len(legacy)}, immutable=True)
    # This stage intentionally holds the old predictions and choices fixed across engines.
    bundle = load_bundle(paths)
    maps, schedule, _, start, end = load_predictions(
        bundle, baseline, identity=read(baseline / "training_status.json")["identity"]
    )
    fixed = evaluate_accounts(
        bundle,
        maps,
        schedule,
        start,
        end,
        [10, 20, 30],
        ["selected_strategy", "matched_momentum"],
        output / "fixed_forecasts",
        turnover="exit_priority",
    )
    save(output / "fixed_forecasts/summary.json", fixed, immutable=True)
    fixed_changes = account_differences(previous, output / "fixed_forecasts", fixed["artifacts"])
    reselected_changes = account_differences(
        output / "fixed_forecasts", output / "exit_priority", results["exit_priority"]["artifacts"]
    )
    save(output / "fixed_forecast_differences.json", fixed_changes, immutable=True)
    save(output / "reselection_differences.json", reselected_changes, immutable=True)
    comparison = {}
    requirements = {
        "models_unchanged": True,
        "legacy_accounts_unchanged": len(legacy) == original["accounts"],
    }
    for cost in (10, 20, 30):
        key = f"{cost}bps"
        old = original["continuous"][key]["selected_strategy"]["summary"]
        selected = results["exit_priority"]["continuous"][key]["selected_strategy"]["summary"]
        matched = results["exit_priority"]["continuous"][key]["matched_momentum"]["summary"]
        comparison[key] = {
            "legacy": old,
            "fixed_forecasts": fixed["continuous"][key]["selected_strategy"]["summary"],
            "reselected": selected,
            "matched_momentum": matched,
            "excess_to_matched_momentum": selected["return"] - matched["return"],
            "ten_day": results["exit_priority"]["results"][key],
        }
        requirements[key + "_return_not_lower"] = selected["return"] >= old["return"]
        requirements[key + "_drawdown_within8"] = selected["max_drawdown"] >= -0.08
        requirements[key + "_quality_clear"] = (
            selected["quality_issues"] == selected["unresolved_events"] == 0
        )
    result = {
        "status": "COMPLETE",
        "completed_at": now(),
        "decision": "RESEARCH_ONLY",
        "identity": registered["identity"],
        "source_run": run.relative_to(paths.root).as_posix(),
        "protocol": PROTOCOL,
        "scope": protocol["discovery"],
        "baseline_accounts_equal": len(legacy),
        "evaluated_accounts": sum(r["accounts"] for r in results.values()) + fixed["accounts"],
        "comparison": comparison,
        "activation": {"requirements": requirements, "passes": all(requirements.values())},
        "superiority_gate": False,
        "models": parity,
    }
    save(output / "study_summary.json", result, immutable=True)
    return {k: v for k, v in result.items() if k not in ("comparison", "models")}
