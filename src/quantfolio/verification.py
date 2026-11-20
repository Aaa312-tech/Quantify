"""Reload saved estimators, check label purging and optionally fit one window afresh."""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from .dataset import (
    check_integrity,
    eligible_rows,
    label_mask,
    load_bundle,
    validate_features,
    walk_forward_folds,
)
from .models import fit_stage
from .settings import Paths
from .storage import now, read, save, sha


def verify(
    paths: Paths, output: Path | None = None, refit: bool = False, run: str | Path | None = None
) -> dict:
    """Validate every stored model against its predictions; refit only to a new output."""
    if refit and output is None:
        raise ValueError("Refit verification requires a new output directory")
    integrity = check_integrity(paths)
    bundle = load_bundle(paths, reference=run is None)
    source = paths.reference if run is None else paths.artifact_path(run, "experiments")
    if run is not None:
        from .evaluation import experiment_folds

        experiment_folds(paths, bundle, source)
    table = bundle["table"]
    feature_check = validate_features(bundle)
    matrix = table[bundle["features"]].to_numpy(dtype=np.float32)
    records = []
    result_paths = sorted(source.glob("training/fold_*/result.json"))
    if (source / "final_fit/result.json").is_file():
        result_paths.append(source / "final_fit/result.json")
    for result_path in result_paths:
        result = read(result_path)
        fold = result["fold"]
        stages = [
            ("validation_fit", fold["validation_start"], fold["validation_end"]),
            ("outer_fit", fold["test_start"], fold["test_end"]),
        ]
        for stage, start, end in stages:
            rows = eligible_rows(table, start, end)
            for family in ("ridge", "tree"):
                folder = result_path.parent / stage / family
                metadata = read(folder / "fit.json")
                if sha(folder / "model.joblib") != metadata["model_sha256"]:
                    raise ValueError(f"Model changed: {folder}")
                model = joblib.load(folder / "model.joblib")
                with threadpool_limits(limits=2):
                    actual = model.predict(matrix[rows])
                expected = np.load(folder / "predictions.npy")
                np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
                before = start + int(result["final_fit"] and stage == "outer_fit")
                if metadata["latest_training_label_end"] >= before:
                    raise AssertionError(f"Label availability violation: {folder}")
                records.append(
                    {
                        "path": folder.relative_to(paths.root).as_posix(),
                        "predictions": len(rows),
                        "max_error": float(np.max(np.abs(actual - expected))),
                        "label_availability_pass": True,
                    }
                )
    fresh = None
    if refit:
        fold = walk_forward_folds(bundle["calendar"])[0]
        training_rows = np.flatnonzero(
            label_mask(table, 5, fold["train_start"], fold["train_end"], fold["validation_start"])
        )
        prediction_rows = eligible_rows(table, fold["validation_start"], fold["validation_end"])
        predictions = fit_stage(
            bundle,
            training_rows,
            prediction_rows,
            output / "fresh_fit",
            {"purpose": "independent refit verification", "fold": fold},
        )
        fresh = {}
        for family, actual in predictions.items():
            expected = np.load(
                source / "training/fold_01/validation_fit" / family / "predictions.npy"
            )
            np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
            fresh[family] = {
                "predictions": len(actual),
                "max_error": float(np.max(np.abs(actual - expected))),
            }
    result = {
        "status": "PASS",
        "completed_at": now(),
        "source_run": source.relative_to(paths.root).as_posix(),
        "data_sha256": sha(paths.reference_bundle if run is None else paths.bundle),
        "integrity": integrity,
        "features": feature_check,
        "models_reloaded": len(records),
        "models": records,
        "fresh_refit": fresh,
    }
    if output:
        save(output / "verification.json", result, immutable=True)
    return result
