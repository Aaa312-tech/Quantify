"""Locate numerical drift in frozen models without fitting or accepting new predictions."""

from __future__ import annotations

import argparse
import importlib.metadata
import io
import os
import platform
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import joblib
import numpy as np
from threadpoolctl import threadpool_info, threadpool_limits

from quantfolio.dataset import eligible_rows, load_bundle
from quantfolio.regression import check_inputs, read_baseline, runtime_sources
from quantfolio.settings import Paths, project_root
from quantfolio.storage import array_sha, now, read, save, sha


def fingerprint(array):
    array = np.asarray(array)
    return {
        "sha256": array_sha(array),
        "shape": list(array.shape),
        "dtype": str(array.dtype),
        "strides": list(array.strides),
        "alignment_mod_64": array.ctypes.data % 64,
        "nonfinite": int(np.count_nonzero(~np.isfinite(array))),
    }


def compare_predictions(actual, expected):
    if actual.shape != expected.shape or actual.dtype != expected.dtype:
        return {"exact": False, "reason": "shape or dtype differs"}
    if not np.isfinite(actual).all() or not np.isfinite(expected).all():
        return {"exact": False, "reason": "nonfinite predictions"}
    difference = np.abs(actual.astype(np.float64) - expected.astype(np.float64))
    indices = np.flatnonzero(actual != expected)
    return {
        "exact": not len(indices),
        "different": len(indices),
        "above_1e_12": int(np.count_nonzero(difference > 1e-12)),
        "max_absolute_error": float(np.max(difference, initial=0)),
        "examples": [
            {"row": int(i), "actual": float(actual[i]), "expected": float(expected[i])}
            for i in indices[:8]
        ],
    }


def environment():
    stream = io.StringIO()
    with redirect_stdout(stream):
        np.show_runtime()
    libraries = threadpool_info()
    for library in libraries:
        library["sha256"] = sha(library["filepath"])
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "scipy", "scikit-learn", "joblib", "threadpoolctl")
        },
        "variables": {
            name: os.environ.get(name)
            for name in (
                "MKL_ENABLE_INSTRUCTIONS",
                "MKL_CBWR",
                "MKL_NUM_THREADS",
                "MKL_DYNAMIC",
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "NPY_DISABLE_CPU_FEATURES",
            )
        },
        "threadpools": libraries,
        "numpy_build": np.show_config(mode="dicts"),
        "numpy_runtime": stream.getvalue(),
    }


def inspect_model(model, matrix, expected):
    stages = {"input": fingerprint(matrix)}
    transformed = matrix
    for name, step in model.steps[:-1]:
        transformed = step.transform(transformed)
        stages[name] = fingerprint(transformed)
    estimator = model.steps[-1][1]
    staged = estimator.predict(transformed)
    actual = model.predict(matrix)
    record = {
        "stages": stages,
        "prediction": fingerprint(actual),
        "frozen_prediction": fingerprint(expected),
        "comparison": compare_predictions(actual, expected),
        "staged_comparison": compare_predictions(staged, actual),
    }
    if hasattr(estimator, "coef_"):
        record["coefficients"] = fingerprint(estimator.coef_)
        record["intercept"] = fingerprint(estimator.intercept_)
        variants = {
            "linear": transformed @ estimator.coef_ + estimator.intercept_,
            "column_major": np.asfortranarray(transformed) @ estimator.coef_ + estimator.intercept_,
            "einsum": np.einsum("ij,j->i", transformed, estimator.coef_, optimize=False)
            + estimator.intercept_,
        }
        record["linear_probes"] = {
            name: {
                "fingerprint": fingerprint(values),
                "comparison": compare_predictions(values, expected),
            }
            for name, values in variants.items()
        }
    return record, actual


def diagnose(paths, output):
    output = output.resolve()
    if not output.is_relative_to(paths.root / "artifacts" / "checks"):
        raise ValueError("Diagnostics must be stored under artifacts/checks")
    if output.exists():
        raise FileExistsError(f"Choose an unused diagnostic directory: {output}")
    output.mkdir(parents=True)
    script = Path(__file__)
    (output / "diagnose_predictions.py").write_bytes(script.read_bytes())
    header = {
        "started_at": now(),
        "script_sha256": sha(script),
        "source_sha256": runtime_sources(),
        "commit": subprocess.check_output(
            ["git", "-C", str(paths.root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "command": sys.argv,
        "new_model_fits": 0,
    }
    save(output / "run.json", header, immutable=True)
    report = {"status": "FAIL", "models": [], "new_model_fits": 0}
    try:
        baseline = read_baseline(paths)
        check_inputs(paths, baseline)
        bundle = load_bundle(paths)
        table = bundle["table"]
        matrix = table[bundle["features"]].to_numpy(dtype=np.float32)
        source = paths.root / baseline["training"]
        results = sorted(source.glob("training/fold_*/result.json"))
        if (source / "final_fit/result.json").exists():
            results.append(source / "final_fit/result.json")
        for result_path in results:
            fold = read(result_path)["fold"]
            for stage, start, end in (
                ("validation_fit", fold["validation_start"], fold["validation_end"]),
                ("outer_fit", fold["test_start"], fold["test_end"]),
            ):
                rows = eligible_rows(table, start, end)
                for family in ("ridge", "tree"):
                    folder = result_path.parent / stage / family
                    model = joblib.load(folder / "model.joblib")
                    expected = np.load(folder / "predictions.npy", allow_pickle=False)
                    with threadpool_limits(limits=2):
                        record, actual = inspect_model(model, matrix[rows], expected)
                        record["threads"] = [
                            {"api": item["internal_api"], "threads": item["num_threads"]}
                            for item in threadpool_info()
                        ]
                    name = folder.relative_to(source).as_posix()
                    record.update(
                        {
                            "path": name,
                            "model_sha256": sha(folder / "model.joblib"),
                            "rows": fingerprint(rows),
                        }
                    )
                    predicted_path = output / name / "actual.npy"
                    predicted_path.parent.mkdir(parents=True)
                    with predicted_path.open("xb") as stream:
                        np.save(stream, actual, allow_pickle=False)
                    save(predicted_path.with_name("diagnostic.json"), record, immutable=True)
                    report["models"].append(record)
                    print(f"{name}: {record['comparison']}", flush=True)
        report["expected_models"] = 4 * baseline["expected"]["training_windows"]
        report["environment"] = environment()
        if len(report["models"]) == report["expected_models"] and all(
            row["comparison"]["exact"] and row["staged_comparison"]["exact"]
            for row in report["models"]
        ):
            report["status"] = "PASS"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["completed_at"] = now()
        save(output / "result.json", report, immutable=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(Paths(project_root(args.project)), args.output)
    raise SystemExit(0 if result["status"] == "PASS" else 1)
