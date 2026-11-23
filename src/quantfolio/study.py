"""A bounded, preregistered comparison of selection, recovery and turnover changes."""

from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .dataset import check_integrity, eligible_rows, load_bundle
from .evaluation import evaluate, evaluate_variants, experiment_folds, load_predictions
from .models import (
    CANDIDATES,
    candidate_scores,
    forecasts_by_date,
    industry_holdout_check,
    neutral_scores,
    prediction_map,
)
from .selection import assess, select_model, select_rules
from .settings import Paths
from .storage import digest, log, now, read, save, sha
from .training import run_training, trial
from .verification import verify


@dataclass(frozen=True)
class Recipe:
    """Registered selection, risk and turnover settings."""

    components: tuple[str, ...] = ()
    positive_score: bool = False
    industry_holdout: bool = False
    mode: str = "latched"
    turnover: str = "legacy"


SINGLES = {
    "positive_adoption": Recipe(("positive_adoption",), positive_score=True),
    "industry_holdout": Recipe(("industry_holdout",), industry_holdout=True),
    "staged_restore": Recipe(("staged_restore",), mode="staged_restore"),
    "probe_restore": Recipe(("probe_restore",), mode="probe_restore"),
    "cost_penalty": Recipe(("cost_penalty",), turnover="cost_penalty"),
    "cost_band": Recipe(("cost_band",), turnover="cost_band"),
}
COMBINATIONS = {
    "stable_selection": Recipe(("positive_adoption", "industry_holdout"), True, True),
    "stable_cost_band": Recipe(("positive_adoption", "cost_band"), True, turnover="cost_band"),
}


def research_paths(paths, protocol):
    """Resolve the isolated configuration for a study."""
    configured = Paths(paths.root)
    configured.config.update(
        protocol=protocol.relative_to(paths.root).as_posix(), selection_mode="positive_score"
    )
    return configured


def choose_inner_policy(results):
    """Choose eligible latched recipes by inner evidence alone; momentum is the fallback."""
    recipes = {**SINGLES, **COMBINATIONS}
    candidates = [
        name
        for name, result in results.items()
        if name in recipes
        and recipes[name].mode == "latched"
        and result.get("tested")
        and result["adoption"]["qualifies"]
        and result["adoption"]["score"] > 0
    ]
    return min(
        candidates,
        key=lambda name: (-results[name]["adoption"]["score"], len(recipes[name].components), name),
        default="matched_momentum",
    )


def combination_eligible(recipe, results):
    """Require evidence for both preregistered components before testing their pair."""
    return all(
        results[name]["adoption"]["qualifies"] and results[name]["adoption"]["score"] > 0
        for name in recipe.components
    )


def _window(paths, bundle, fit_folder, baseline_folder, output, identity):
    fitted = read(fit_folder / "result.json")
    fold = fitted["fold"]
    rows = eligible_rows(bundle["table"], fold["validation_start"], fold["validation_end"])
    predictions = {}
    for family in ("ridge", "tree"):
        folder = fit_folder / "validation_fit" / family
        metadata = read(folder / "fit.json")
        if sha(folder / "predictions.npy") != metadata["predictions_sha256"]:
            raise ValueError("Validation predictions changed before recipe selection")
        predictions[family] = np.load(folder / "predictions.npy")
    scores = candidate_scores(bundle["table"], rows, predictions)
    forecasts = {
        name: forecasts_by_date(
            bundle["table"], rows, scores[name], scores["momentum"], fitted["initial_calibration"]
        )
        for name in CANDIDATES
    }
    cache = {}
    for config, values in fitted["inner_rules"].items():
        cache[("momentum", config, "latched", "legacy")] = (
            deepcopy(values),
            fit_folder / "inner_rules" / f"{config}.json",
        )
    for name, values in fitted["inner_candidates"].items():
        if name != "momentum":
            cache[(name, fitted["selected_rules"], "latched", "legacy")] = (
                deepcopy(values),
                fit_folder / "inner_models" / f"{name}.json",
            )

    def account(model, config, mode="latched", turnover="legacy"):
        key = (model, config, mode, turnover)
        if key not in cache:
            path = output / "trials" / "__".join(key)
            values = trial(
                bundle, fold, forecasts[model], config, path, identity, mode=mode, turnover=turnover
            )
            cache[key] = values, path.with_suffix(".json")
        values, path = cache[key]
        return deepcopy(values), path

    factors = {}
    for family in ("ridge", "tree"):
        factors[family] = industry_holdout_check(
            bundle["table"],
            rows,
            neutral_scores(bundle["table"], rows, predictions[family])[0],
            fold["test_start"],
        )
    save(output / "industry_holdouts.json", factors, immutable=True)

    def selection(recipe):
        rules = deepcopy(fitted["inner_rules"])
        config = select_rules(rules, positive_score=recipe.positive_score)
        models = {}
        for name in CANDIDATES:
            values, _ = account(name, config)
            values.pop("adoption", None)
            values["factor_check"] = deepcopy(fitted["inner_candidates"][name]["factor_check"])
            if recipe.industry_holdout and name != "momentum":
                values["factor_check"]["holdout_check"] = factors[name[:-2]]
                values["factor_check"]["pass"] &= factors[name[:-2]]["pass"]
            models[name] = values
        model = select_model(models, positive_score=recipe.positive_score)
        evidence = {
            "rule_adoption": {
                name: value["adoption"] for name, value in rules.items() if "adoption" in value
            },
            "model_adoption": {
                name: value["adoption"] for name, value in models.items() if "adoption" in value
            },
        }
        return model, config, evidence

    legacy_model, legacy_config, _ = selection(Recipe())
    historical = read(baseline_folder / "result.json")
    if (legacy_model, legacy_config) != (
        historical["selected_candidate"],
        historical["selected_rules"],
    ):
        raise ValueError("The default selection changed during the research refactor")
    bases = {
        mode: account("momentum", "baseline", mode)[0]
        for mode in ("latched", "staged_restore", "probe_restore")
    }
    results = {}

    def investigate(recipe):
        evidence = None
        if recipe.positive_score or recipe.industry_holdout:
            model, config, evidence = selection(recipe)
        else:
            model, config = legacy_model, legacy_config
        values, source = account(model, config, recipe.mode, recipe.turnover)
        assessment = assess(values, bases[recipe.mode], positive_score=True)
        return {
            "tested": True,
            "choice": {
                "model": model,
                "rules": config,
                "mode": recipe.mode,
                "turnover": recipe.turnover,
            },
            "validation": values,
            "adoption": assessment,
            "selection_evidence": evidence,
            "trial_source": source.relative_to(paths.root).as_posix(),
        }

    for name, recipe in SINGLES.items():
        results[name] = investigate(recipe)
    primary = results["positive_adoption"]["choice"]
    if (primary["model"], primary["rules"]) != (
        fitted["selected_candidate"],
        fitted["selected_rules"],
    ):
        raise ValueError("Primary training and study adoption policies disagree")
    for name, recipe in COMBINATIONS.items():
        results[name] = (
            investigate(recipe)
            if combination_eligible(recipe, results)
            else {
                "tested": False,
                "status": "NOT_TESTED_COMPONENT_GATE",
                "components": recipe.components,
            }
        )
    selected = choose_inner_policy(results)
    choice = (
        results[selected]["choice"]
        if selected != "matched_momentum"
        else {"model": "momentum", "rules": "baseline", "mode": "latched", "turnover": "legacy"}
    )
    record = {
        "completed_at": now(),
        "fold": fold,
        "final_fit": fitted["final_fit"],
        "legacy_choice": {"model": legacy_model, "rules": legacy_config},
        "recipes": results,
        "inner_selected": {"recipe": selected, "choice": choice},
        "outer_results_used_for_selection": False,
        "fit_result_sha256": sha(fit_folder / "result.json"),
    }
    save(output / "choices.json", record, immutable=True)
    log("Research choices frozen", window=fold["fold"], selected=selected)
    return record


@pd.option_context("compute.use_numexpr", False)
def run_study(paths, output, protocol_path):
    """Refit 48 estimators and freeze all recipe choices before any outer evaluation."""
    output = paths.output_path(output, "experiments")
    checks = paths.output_path(f"artifacts/checks/{output.name}/models", "checks")
    protocol = (paths.root / protocol_path).resolve()
    if not protocol.is_relative_to((paths.root / "configs/protocols").resolve()):
        raise ValueError("Study protocol must stay under configs/protocols")
    check_integrity(paths)
    manifest = read(paths.manifest)
    if manifest["files"].get(protocol.relative_to(paths.root).as_posix()) != sha(protocol):
        raise ValueError("Study protocol is not registered or has changed")
    plan = read(protocol)
    if [item["name"] for item in plan["single_changes"]] != list(SINGLES) or {
        item["name"]: tuple(item["components"]) for item in plan["combinations"]
    } != {name: recipe.components for name, recipe in COMBINATIONS.items()}:
        raise ValueError("Candidate budget differs from the registered study")
    if (
        paths.bundle.relative_to(paths.root).as_posix() != plan["data_bundle"]
        or sha(paths.bundle) != plan["data_sha256"]
    ):
        raise ValueError("Study dataset differs from registration")
    baseline = paths.artifact_path(plan["baseline_training"], "experiments")
    if sha(baseline / "protocol.json") != plan["baseline_training_protocol_sha256"]:
        raise ValueError("Baseline training identity changed")
    specification = {
        "plan": protocol.relative_to(paths.root).as_posix(),
        "plan_sha256": sha(protocol),
        "data_sha256": sha(paths.bundle),
        "baseline": plan["baseline_training"],
        "recipes": {name: asdict(recipe) for name, recipe in {**SINGLES, **COMBINATIONS}.items()},
        "source_sha256": {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
    }
    identity = digest(specification)
    save(
        output / "protocol.json",
        {"registered_at": now(), "identity": identity, "specification": specification},
        immutable=True,
    )
    configured = research_paths(paths, protocol)
    fitted = output / "primary_training"
    run_training(configured, fitted, include_latest=True)
    verification = verify(configured, checks, True, fitted)
    bundle = load_bundle(configured)
    results = []
    folders = sorted(fitted.glob("training/fold_*")) + [fitted / "final_fit"]
    for folder in folders:
        relative = folder.relative_to(fitted)
        result = _window(
            paths, bundle, folder, baseline / relative, output / "selection" / relative, identity
        )
        results.append(
            {
                "path": (output / "selection" / relative / "choices.json")
                .relative_to(paths.root)
                .as_posix(),
                "fold": result["fold"]["fold"],
                "final_fit": result["final_fit"],
            }
        )
    status = {
        "status": "COMPLETE",
        "completed_at": now(),
        "identity": identity,
        "primary_training": fitted.relative_to(paths.root).as_posix(),
        "data_cutoff": bundle["cutoff"],
        "windows": results,
        "choices_sha256": {item["path"]: sha(paths.root / item["path"]) for item in results},
        "outer_results_evaluated": False,
        "model_verification": verification["status"],
        "verification_file": (checks / "verification.json").relative_to(paths.root).as_posix(),
        "decision": "RESEARCH_ONLY",
    }
    save(output / "study_status.json", status, immutable=True)
    return status


def _outer_inputs(bundle, fitted, windows):
    variants = {
        name: {"forecasts": {}, "rules": {}, "turnover": {}, "mode": recipe.mode}
        for name, recipe in SINGLES.items()
    }
    for name, mode in (
        ("legacy_selected", "latched"),
        ("legacy_baseline_rules", "latched"),
        ("staged_momentum", "staged_restore"),
        ("probe_momentum", "probe_restore"),
        ("inner_selected", "latched"),
    ):
        variants[name] = {"forecasts": {}, "rules": {}, "turnover": {}, "mode": mode}
    tested_pairs = {
        name
        for name in COMBINATIONS
        if any(window["recipes"][name]["tested"] for window in windows)
    }
    for name in tested_pairs:
        variants[name] = {"forecasts": {}, "rules": {}, "turnover": {}, "mode": "latched"}
    fallback = {"model": "momentum", "rules": "baseline", "turnover": "legacy", "mode": "latched"}
    for window in windows:
        fold = window["fold"]
        folder = fitted / "training" / f"fold_{fold['fold']:02d}"
        result = read(folder / "result.json")
        if sha(folder / "outer_predictions.npz") != result["predictions_sha256"]:
            raise ValueError("Outer predictions changed after choices were frozen")
        with np.load(folder / "outer_predictions.npz") as stored:
            arrays = dict(stored)
        expected = eligible_rows(bundle["table"], fold["test_start"], fold["test_end"])
        if not np.array_equal(arrays["row_indices"], expected):
            raise ValueError("Outer prediction coverage changed")
        forecasts = {name: prediction_map(bundle["table"], arrays, name) for name in CANDIDATES}
        for name, variant in variants.items():
            if name in SINGLES:
                choice = window["recipes"][name]["choice"]
            elif name in COMBINATIONS:
                item = window["recipes"][name]
                choice = (
                    item["choice"] if item["tested"] and item["adoption"]["qualifies"] else fallback
                )
            elif name.startswith("legacy_"):
                choice = {**fallback, **window["legacy_choice"]}
                if name == "legacy_baseline_rules":
                    choice["rules"] = "baseline"
            elif name == "inner_selected":
                choice = window["inner_selected"]["choice"]
            else:
                choice = fallback
            variant["forecasts"].update(forecasts[choice["model"]])
            dates = range(fold["test_start"], fold["test_end"] + 1)
            variant["rules"].update({day: choice["rules"] for day in dates})
            variant["turnover"].update({day: choice["turnover"] for day in dates})
    expected_dates = list(
        range(windows[0]["fold"]["test_start"], windows[-1]["fold"]["test_end"] + 1)
    )
    for variant in variants.values():
        if not all(
            sorted(variant[key]) == expected_dates for key in ("forecasts", "rules", "turnover")
        ):
            raise ValueError("Research forecasts or controls have incomplete date coverage")
    return variants


@pd.option_context("compute.use_numexpr", False)
def evaluate_study(paths, run, output):
    """Evaluate frozen research choices and the deployable non-recovering primary separately."""
    run = paths.artifact_path(run, "experiments")
    output = paths.output_path(output, "evaluations")
    registered = read(run / "protocol.json")
    status = read(run / "study_status.json")
    if status["status"] != "COMPLETE" or registered["identity"] != digest(
        registered["specification"]
    ):
        raise ValueError("Study choices are incomplete or their identity changed")
    if status["identity"] != registered["identity"]:
        raise ValueError("Study identity mismatch")
    for name, expected in registered["specification"]["source_sha256"].items():
        if sha(Path(__file__).parent / name) != expected:
            raise ValueError(f"Study source changed since choices were generated: {name}")
    protocol = paths.root / registered["specification"]["plan"]
    if sha(protocol) != registered["specification"]["plan_sha256"]:
        raise ValueError("Registered research plan changed")
    configured = research_paths(paths, protocol)
    fitted = paths.artifact_path(status["primary_training"], "experiments")
    bundle = load_bundle(configured)
    fitted_status, folds = experiment_folds(configured, bundle, fitted)
    _, _, _, start, end = load_predictions(bundle, fitted, folds, fitted_status["identity"])
    windows = []
    for item in status["windows"]:
        path = paths.root / item["path"]
        if (
            not path.resolve().is_relative_to(run)
            or sha(path) != status["choices_sha256"][item["path"]]
        ):
            raise ValueError("Frozen research choice changed or escaped its study")
        window = read(path)
        if window["outer_results_used_for_selection"]:
            raise ValueError("Outer outcomes entered recipe selection")
        if not item["final_fit"]:
            windows.append(window)
    if [window["fold"] for window in windows] != folds:
        raise ValueError("Research choice windows differ from the training calendar")
    variants = _outer_inputs(bundle, fitted, windows)
    # The CLI uses the registered positive-score primary evaluation.
    primary = evaluate(configured, fitted, output, [10, 20, 30], all_policies=True)
    variants.pop("positive_adoption")
    for controls in variants.values():
        policies = set(controls["turnover"].values())
        if len(policies) == 1:
            controls["turnover"] = next(iter(policies))
    alternatives = evaluate_variants(
        bundle, variants.items(), start, end, (10, 20, 30), output / "alternatives"
    )
    result = {
        "schema_version": 1,
        "status": "COMPLETE",
        "completed_at": now(),
        "identity": status["identity"],
        "data_cutoff": bundle["cutoff"],
        "source_run": run.relative_to(paths.root).as_posix(),
        "choices_sha256": status["choices_sha256"],
        "primary_evaluation_sha256": sha(output / "evaluation_summary.json"),
        "primary_policy": "positive_adoption",
        "single_changes": list(SINGLES),
        "combinations_tested": [name for name in COMBINATIONS if name in variants],
        "combinations_skipped": [name for name in COMBINATIONS if name not in variants],
        "decision": "RESEARCH_ONLY",
        "independent_alpha_validation": False,
        "primary_accounts": primary["accounts"],
        **alternatives,
    }
    save(output / "study_summary.json", result, immutable=True)
    return {
        key: value
        for key, value in result.items()
        if key not in ("results", "continuous", "artifacts", "choices_sha256")
    }
