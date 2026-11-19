"""Reproducible training windows; outputs belong to a new named experiment."""

from __future__ import annotations

import numpy as np

from .audit import execution_audit
from .calibration import calibrate, map_calibration, rank_by_date
from .models import (
    CANDIDATES,
    candidate_scores,
    factor_check,
    fit_stage,
    forecasts_by_date,
    neutral_scores,
)
from .reconciliation import reconcile
from .rules import CONFIGS
from .selection import clip_for, eligible_rows, objective, select_model, select_rules
from .simulation import episodes, simulate
from .splits import label_mask
from .storage import dump_gzip, log, now, read, save, sha


def run_training(paths, output, first=1, last=11, include_latest=False):
    """Train the unchanged V6 protocol in a new experiment, preserving all reference files.

    Model and rule selection uses only the validation segment. Partial fold runs
    are explicitly marked PARTIAL and must not be presented as a full backtest.
    """
    from pathlib import Path

    from .dataset import check_integrity, load_bundle
    from .settings import Paths
    from .splits import walk_forward_folds
    from .storage import digest

    if not isinstance(paths, Paths):
        raise TypeError("paths must be a Paths instance")
    check_integrity(paths)
    bundle = load_bundle(paths)
    folds = walk_forward_folds(bundle["calendar"])
    if not 1 <= first <= last <= len(folds):
        raise ValueError(f"Require 1 <= first <= last <= {len(folds)}")
    specification = {
        "plan_sha256": sha(paths.root / "configs/experiment_v6.json"),
        "data_sha256": sha(paths.bundle),
        "source_sha256": {
            path.name: sha(path) for path in sorted(Path(__file__).parent.glob("*.py"))
        },
        "folds": folds,
        "model_candidates": CANDIDATES,
        "rule_configs": CONFIGS,
        "selected_folds": [first, last],
        "include_latest": include_latest,
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
        assert out["identity"] == identity and sha(ledger_path) == out["ledger_sha256"]
        return out
    eps = episodes(
        bundle,
        fold["validation_start"],
        fold["validation_end"],
        forecasts,
        detail=True,
        rules=config,
    )
    cs, cl = simulate(
        bundle,
        fold["validation_start"] + 1,
        fold["validation_end"] + 1,
        forecasts,
        detail=True,
        rules=config,
    )
    checks = [
        reconcile(e["ledger"], e["summary"], bundle["meta"], bundle["actions"]) for e in eps
    ] + [reconcile(cl, cs, bundle["meta"], bundle["actions"])]
    audits = [execution_audit(e["ledger"], bundle) for e in eps] + [execution_audit(cl, bundle)]
    assert all(c["status"] == "PASS" for c in checks + audits), str(
        [c for c in checks + audits if c["status"] != "PASS"]
    )
    dump_gzip(
        ledger_path,
        {
            "episodes": eps,
            "continuous": {"summary": cs, "ledger": cl},
            "reconciliation": checks,
            "execution_audit": audits,
        },
    )
    out = {
        **objective(eps),
        "identity": identity,
        "config": config,
        "continuous": cs,
        "accounting_pass": True,
        "accounting_checks": sum(c["checks"] for c in checks),
        "execution_checks": sum(c["checks"] for c in audits),
        "ledger_sha256": sha(ledger_path),
        "ledger_path": str(ledger_path),
    }
    save(summary_path, out, immutable=True)
    return out


def run_window(b, f, folder, identity, final=False):
    """Select rules and models on validation data, refit, then freeze outer predictions."""
    folder.mkdir(parents=True, exist_ok=True)
    t = b["table"]
    if (folder / "result.json").exists():
        x = read(folder / "result.json")
        assert (
            x["identity"] == identity
            and sha(folder / "outer_predictions.npz") == x["predictions_sha256"]
        )
        return x
    ci = f["train_end"] - 59
    fit_end = ci - 7
    cr = eligible_rows(t, ci, f["train_end"])
    vr = eligible_rows(t, f["validation_start"], f["validation_end"])
    tr = eligible_rows(t, f["test_start"], f["test_end"])
    first = np.flatnonzero(label_mask(t, 5, f["train_start"], fit_end, ci))
    initial_cal = calibrate(
        t,
        cr,
        rank_by_date(t, cr, t.iloc[cr].momentum_score.to_numpy()),
        f["validation_start"],
        clip_for(t, first),
    )
    vt = np.flatnonzero(label_mask(t, 5, f["train_start"], f["train_end"], f["validation_start"]))
    pred = fit_stage(
        b,
        vt,
        vr,
        folder / "validation_fit",
        {"experiment": identity, "fold": f, "stage": "validation"},
    )
    vs = candidate_scores(t, vr, pred)
    fc = forecasts_by_date(t, vr, vs["momentum"], vs["momentum"], initial_cal)
    rules = {}
    for config in CONFIGS:
        rules[config] = trial(b, f, fc, config, folder / "inner_rules" / config, identity)
    selected_rules = select_rules(rules)
    log("V6 rules selected from past validation", window=f["fold"], config=selected_rules)
    factors = {
        name: factor_check(t, vr, neutral_scores(t, vr, pred[name])[0], f["test_start"])
        for name in ["ridge", "tree"]
    }
    factors["momentum"] = factor_check(t, vr, vs["momentum"], f["test_start"])
    models = {}
    for name in CANDIDATES:
        if name == "momentum":
            x = {**rules[selected_rules]}
            x.pop("adoption", None)
        else:
            x = trial(
                b,
                f,
                forecasts_by_date(t, vr, vs[name], vs["momentum"], initial_cal),
                selected_rules,
                folder / "inner_models" / name,
                identity,
            )
        x["factor_check"] = factors["momentum" if name == "momentum" else name[:-2]]
        models[name] = x
    selected = select_model(models)
    outer_cal = calibrate(t, vr, vs["momentum"], f["test_start"], clip_for(t, vt))
    refit = np.flatnonzero(label_mask(t, 5, f["train_start"], f["validation_end"], f["test_start"]))
    if final:
        refit = np.flatnonzero(
            label_mask(t, 5, f["train_start"], f["test_start"] - 1, f["test_start"] + 1)
        )
    pred = fit_stage(
        b,
        refit,
        tr,
        folder / "outer_fit",
        {"experiment": identity, "fold": f, "stage": "final" if final else "outer"},
    )
    score = candidate_scores(t, tr, pred)
    mean, margin = map_calibration(score["momentum"], outer_cal)
    arrays = {
        "row_indices": tr,
        "common_mu": mean,
        "common_margin": margin,
        "ridge_rank_target_prediction": pred["ridge"],
        "tree_rank_target_prediction": pred["tree"],
    }
    for name in CANDIDATES:
        arrays[name + "_rank"] = score[name]
    np.savez_compressed(folder / "outer_predictions.npz", **arrays)
    out = {
        "identity": identity,
        "completed_at": now(),
        "fold": f,
        "selected_candidate": selected,
        "selected_rules": selected_rules,
        "inner_rules": rules,
        "inner_candidates": models,
        "initial_calibration": initial_cal,
        "outer_calibration": outer_cal,
        "calibration_fit_end": fit_end,
        "calibration_signal_start": ci,
        "validation_train_rows": len(vt),
        "refit_rows": len(refit),
        "outer_rows": len(tr),
        "predictions_sha256": sha(folder / "outer_predictions.npz"),
        "final_fit": final,
        "outer_results_used_for_selection": False,
        "source_selection": "Validation only; continuous outcomes also constrain adoption.",
    }
    save(folder / "result.json", out, immutable=True)
    log("V6 window complete", window=f["fold"], rules=selected_rules, model=selected)
    return out
