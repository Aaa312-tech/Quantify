"""Reproducible training windows; outputs belong to a new named experiment."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .calibration import calibrate, map_calibration, rank_by_date
from .dataset import eligible_rows, label_mask
from .market import EXECUTION_MODEL
from .models import (
    CANDIDATES,
    candidate_scores,
    factor_check,
    fit_stage,
    forecasts_by_date,
    neutral_scores,
)
from .rules import CONFIGS
from .selection import clip_for, objective, select_model, select_rules
from .simulation import episodes, simulate
from .storage import dump_gzip, log, now, read, save, sha
from .validation import execution_audit, reconcile


@pd.option_context("compute.use_numexpr", False)
def run_training(paths, output, first=1, last=11, include_latest=False):
    """Train the registered protocol in a new experiment, preserving all reference files.

    Model and rule selection uses only the validation segment. Partial fold runs
    are explicitly marked PARTIAL and must not be presented as a full backtest.
    """
    from pathlib import Path

    from .dataset import check_integrity, load_bundle, walk_forward_folds
    from .settings import Paths
    from .storage import digest

    if not isinstance(paths, Paths):
        raise TypeError("paths must be a Paths instance")
    check_integrity(paths)
    bundle = load_bundle(paths)
    folds = walk_forward_folds(bundle["calendar"])
    if not 1 <= first <= last <= len(folds):
        raise ValueError(f"Require 1 <= first <= last <= {len(folds)}")
    specification = {
        "plan_sha256": sha(paths.protocol),
        "data_sha256": sha(paths.bundle),
        "source_sha256": {
            path.name: sha(path) for path in sorted(Path(__file__).parent.glob("*.py"))
        },
        "folds": folds,
        "model_candidates": CANDIDATES,
        "rule_configs": CONFIGS,
        "selected_folds": [first, last],
        "include_latest": include_latest,
        "execution_model": EXECUTION_MODEL,
    }
    identity = digest(specification)
    save(
        output / "protocol.json",
        {"registered_at": now(), "identity": identity, "specification": specification},
        immutable=True,
    )
    for fold in folds:
        if first <= fold["fold"] <= last:
            run_window(bundle, fold, output / "training" / f"fold_{fold['fold']:02d}", identity)
    if include_latest:
        latest = len(bundle["calendar"]) - 1
        validation_end = latest - 7
        validation_start = validation_end - 62
        train_end = validation_start - 7
        fold = {
            "fold": "final",
            "train_start": train_end - 251,
            "train_end": train_end,
            "validation_start": validation_start,
            "validation_end": validation_end,
            "test_start": latest,
            "test_end": latest,
        }
        run_window(bundle, fold, output / "final_fit", identity, final=True)
    result = {
        "status": "COMPLETE" if first == 1 and last == len(folds) else "PARTIAL",
        "completed_at": now(),
        "identity": identity,
        "folds_completed": last - first + 1,
        "data_cutoff": bundle["cutoff"],
        "latest_fit_included": include_latest,
        "decision": "RESEARCH_ONLY",
    }
    save(output / "training_status.json", result, immutable=True)
    return result


def trial(bundle, fold, forecasts, config, path, identity):
    """Run and independently audit a validation candidate before allowing its adoption."""
    summary_path = path.with_suffix(".json")
    ledger_path = path.with_suffix(".ledgers.json.gz")
    if summary_path.exists():
        out = read(summary_path)
        if out["identity"] != identity or sha(ledger_path) != out["ledger_sha256"]:
            raise ValueError(f"Cached trial identity or ledger hash mismatch: {summary_path}")
        return out
    accounts = episodes(
        bundle,
        fold["validation_start"],
        fold["validation_end"],
        forecasts,
        detail=True,
        rules=config,
    )
    continuous_summary, continuous_ledger = simulate(
        bundle,
        fold["validation_start"] + 1,
        fold["validation_end"] + 1,
        forecasts,
        detail=True,
        rules=config,
    )
    checks = [
        reconcile(e["ledger"], e["summary"], bundle["meta"], bundle["actions"]) for e in accounts
    ] + [reconcile(continuous_ledger, continuous_summary, bundle["meta"], bundle["actions"])]
    audits = [execution_audit(e["ledger"], bundle) for e in accounts] + [
        execution_audit(continuous_ledger, bundle)
    ]
    failures = [check for check in checks + audits if check["status"] != "PASS"]
    if failures:
        raise ValueError(f"Validation account audit failed: {failures}")
    dump_gzip(
        ledger_path,
        {
            "episodes": accounts,
            "continuous": {"summary": continuous_summary, "ledger": continuous_ledger},
            "reconciliation": checks,
            "execution_audit": audits,
        },
    )
    out = {
        **objective(accounts),
        "identity": identity,
        "config": config,
        "continuous": continuous_summary,
        "accounting_pass": True,
        "accounting_checks": sum(c["checks"] for c in checks),
        "execution_checks": sum(c["checks"] for c in audits),
        "ledger_sha256": sha(ledger_path),
        "ledger_path": str(ledger_path),
    }
    save(summary_path, out, immutable=True)
    return out


@pd.option_context("compute.use_numexpr", False)
def run_window(bundle, fold, folder, identity, final=False):
    """Select rules and models on validation data, refit, then freeze outer predictions."""
    folder.mkdir(parents=True, exist_ok=True)
    table = bundle["table"]
    if (folder / "result.json").exists():
        result = read(folder / "result.json")
        if (
            result["identity"] != identity
            or sha(folder / "outer_predictions.npz") != result["predictions_sha256"]
        ):
            raise ValueError(f"Cached window identity or prediction hash mismatch: {folder}")
        return result
    calibration_start = fold["train_end"] - 59
    fit_end = calibration_start - 7
    calibration_rows = eligible_rows(table, calibration_start, fold["train_end"])
    validation_rows = eligible_rows(table, fold["validation_start"], fold["validation_end"])
    test_rows = eligible_rows(table, fold["test_start"], fold["test_end"])
    calibration_fit_rows = np.flatnonzero(
        label_mask(table, 5, fold["train_start"], fit_end, calibration_start)
    )
    initial_calibration = calibrate(
        table,
        calibration_rows,
        rank_by_date(
            table, calibration_rows, table.iloc[calibration_rows].momentum_score.to_numpy()
        ),
        fold["validation_start"],
        clip_for(table, calibration_fit_rows),
    )
    train_rows = np.flatnonzero(
        label_mask(table, 5, fold["train_start"], fold["train_end"], fold["validation_start"])
    )
    predictions = fit_stage(
        bundle,
        train_rows,
        validation_rows,
        folder / "validation_fit",
        {"experiment": identity, "fold": fold, "stage": "validation"},
    )
    validation_scores = candidate_scores(table, validation_rows, predictions)
    forecasts = forecasts_by_date(
        table,
        validation_rows,
        validation_scores["momentum"],
        validation_scores["momentum"],
        initial_calibration,
    )
    rules = {}
    for config in CONFIGS:
        rules[config] = trial(
            bundle, fold, forecasts, config, folder / "inner_rules" / config, identity
        )
    selected_rules = select_rules(rules)
    log("Rules selected from past validation", window=fold["fold"], config=selected_rules)
    factors = {
        name: factor_check(
            table,
            validation_rows,
            neutral_scores(table, validation_rows, predictions[name])[0],
            fold["test_start"],
        )
        for name in ["ridge", "tree"]
    }
    factors["momentum"] = factor_check(
        table, validation_rows, validation_scores["momentum"], fold["test_start"]
    )
    models = {}
    for name in CANDIDATES:
        if name == "momentum":
            result = {**rules[selected_rules]}
            result.pop("adoption", None)
        else:
            result = trial(
                bundle,
                fold,
                forecasts_by_date(
                    table,
                    validation_rows,
                    validation_scores[name],
                    validation_scores["momentum"],
                    initial_calibration,
                ),
                selected_rules,
                folder / "inner_models" / name,
                identity,
            )
        result["factor_check"] = factors["momentum" if name == "momentum" else name[:-2]]
        models[name] = result
    selected = select_model(models)
    outer_calibration = calibrate(
        table,
        validation_rows,
        validation_scores["momentum"],
        fold["test_start"],
        clip_for(table, train_rows),
    )
    refit_rows = np.flatnonzero(
        label_mask(table, 5, fold["train_start"], fold["validation_end"], fold["test_start"])
    )
    if final:
        refit_rows = np.flatnonzero(
            label_mask(
                table, 5, fold["train_start"], fold["test_start"] - 1, fold["test_start"] + 1
            )
        )
    predictions = fit_stage(
        bundle,
        refit_rows,
        test_rows,
        folder / "outer_fit",
        {"experiment": identity, "fold": fold, "stage": "final" if final else "outer"},
    )
    scores = candidate_scores(table, test_rows, predictions)
    mean, margin = map_calibration(scores["momentum"], outer_calibration)
    arrays = {
        "row_indices": test_rows,
        "common_mu": mean,
        "common_margin": margin,
        "ridge_rank_target_prediction": predictions["ridge"],
        "tree_rank_target_prediction": predictions["tree"],
    }
    for name in CANDIDATES:
        arrays[name + "_rank"] = scores[name]
    np.savez_compressed(folder / "outer_predictions.npz", **arrays)
    result = {
        "identity": identity,
        "completed_at": now(),
        "fold": fold,
        "selected_candidate": selected,
        "selected_rules": selected_rules,
        "inner_rules": rules,
        "inner_candidates": models,
        "initial_calibration": initial_calibration,
        "outer_calibration": outer_calibration,
        "calibration_fit_end": fit_end,
        "calibration_signal_start": calibration_start,
        "validation_train_rows": len(train_rows),
        "refit_rows": len(refit_rows),
        "outer_rows": len(test_rows),
        "predictions_sha256": sha(folder / "outer_predictions.npz"),
        "final_fit": final,
        "outer_results_used_for_selection": False,
        "source_selection": "Validation only; continuous outcomes also constrain adoption.",
    }
    save(folder / "result.json", result, immutable=True)
    log("Training window complete", window=fold["fold"], rules=selected_rules, model=selected)
    return result
