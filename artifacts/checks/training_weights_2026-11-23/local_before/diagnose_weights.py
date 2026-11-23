"""Trace registered training weights without fitting estimators or accepting drift."""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from scripts.diagnose_predictions import environment, fingerprint

from quantfolio.dataset import label_mask, load_bundle
from quantfolio.models import training_targets
from quantfolio.regression import check_inputs, checked_file, read_baseline, runtime_sources
from quantfolio.settings import Paths, project_root
from quantfolio.storage import array_sha, now, read, save, sha


def weight_stages(table, rows):
    selected, targets, weights, _ = training_targets(table, rows)
    candidates = table.iloc[selected]
    daily = candidates.groupby("date_index", sort=False).symbol.transform("size").to_numpy()
    industry = (
        candidates.groupby(["date_index", "industry"], sort=False)
        .symbol.transform("size")
        .to_numpy()
    )
    sectors = candidates.groupby("date_index", sort=False).industry.transform("nunique").to_numpy()
    ages = candidates.date_index.max() - candidates.date_index.to_numpy()
    exponent = -ages / 126
    base = 0.5 / daily + 0.5 / (industry * sectors)
    decay = np.exp2(exponent)
    unscaled = base * decay
    mean = unscaled.mean()
    unique = np.unique(exponent)
    return {
        "train_rows": rows,
        "fit_rows": selected,
        "target": targets,
        "daily_count": daily,
        "industry_count": industry,
        "sector_count": sectors,
        "ages": ages,
        "exponent": exponent,
        "base_weights": base,
        "decay": decay,
        "unscaled_weights": unscaled,
        "mean": np.array([mean]),
        "fsum_mean": np.array([math.fsum(unscaled) / len(unscaled)]),
        "native_weights": unscaled / mean,
        "weights": weights,
        "unique_exponents": unique,
        "numpy_exp2": np.exp2(unique),
        "math_exp2": np.array([math.exp2(float(value)) for value in unique]),
        "math_pow": np.array([math.pow(2.0, float(value)) for value in unique]),
    }


def read_reference(paths, relative, baseline):
    expected = read(paths.manifest)["files"].get(relative)
    if expected is None:
        raise ValueError("Weight reference must be a frozen project file")
    reference = read(checked_file(paths.root, relative, expected))
    if (
        reference.get("status") != "PASS"
        or reference.get("scope") != "full"
        or reference.get("baseline_commit") != baseline["baseline_commit"]
        or reference.get("profile_sha256") != baseline["profile_sha256"]
        or reference.get("new_model_fits") != 0
    ):
        raise ValueError("Weight reference must be a successful full frozen replay")
    records = reference["training"]["fit_inputs"]
    if len(records) != 2 * baseline["expected"]["training_windows"] or len(
        {(row["window"], row["stage"]) for row in records}
    ) != len(records):
        raise ValueError("Weight reference inventory is incomplete or duplicated")
    return records, expected


def diagnose(paths, output, reference):
    output = output.resolve()
    if not output.is_relative_to(paths.root / "artifacts" / "checks"):
        raise ValueError("Diagnostics must be stored under artifacts/checks")
    if output.exists():
        raise FileExistsError(f"Choose an unused diagnostic directory: {output}")
    baseline = read_baseline(paths)
    records, reference_sha = read_reference(paths, reference, baseline)
    check_inputs(paths, baseline)
    output.mkdir(parents=True)
    scripts = {}
    for name in ("diagnose_weights.py", "diagnose_predictions.py"):
        source = Path(__file__).with_name(name)
        scripts[name] = sha(source)
        (output / name).write_bytes(source.read_bytes())
    save(
        output / "run.json",
        {
            "started_at": now(),
            "scripts_sha256": scripts,
            "source_sha256": runtime_sources(),
            "commit": subprocess.check_output(
                ["git", "-C", str(paths.root), "rev-parse", "HEAD"], text=True
            ).strip(),
            "reference": reference,
            "reference_sha256": reference_sha,
            "command": sys.argv,
            "new_model_fits": 0,
        },
        immutable=True,
    )
    report = {"status": "FAIL", "records": [], "new_model_fits": 0}
    try:
        table = load_bundle(paths)["table"]
        for record in records:
            fold = record["fold"]
            if record["stage"] == "validation":
                end, available = fold["train_end"], fold["validation_start"]
            elif record["stage"] == "outer":
                end, available = fold["validation_end"], fold["test_start"]
            elif record["stage"] == "final":
                end, available = fold["test_start"] - 1, fold["test_start"] + 1
            else:
                raise ValueError("Unknown fitting stage")
            rows = np.flatnonzero(label_mask(table, 5, fold["train_start"], end, available))
            arrays = weight_stages(table, rows)
            destination = output / record["window"] / record["stage"]
            destination.mkdir(parents=True)
            with (destination / "stages.npz").open("xb") as stream:
                np.savez_compressed(stream, **arrays)
            checks = {
                name: array_sha(arrays[name]) == record[name + "_sha256"]
                for name in ("train_rows", "fit_rows", "target", "weights")
            }
            item = {
                "window": record["window"],
                "stage": record["stage"],
                "checks": checks,
                "arrays": {name: fingerprint(values) for name, values in arrays.items()},
                "project_matches_native": array_sha(arrays["weights"])
                == array_sha(arrays["native_weights"]),
            }
            save(destination / "diagnostic.json", item, immutable=True)
            report["records"].append(item)
            print(f"{record['window']}/{record['stage']}: {checks}", flush=True)
        report["environment"] = environment()
        if len(report["records"]) == len(records) and all(
            all(item["checks"].values()) for item in report["records"]
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
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(Paths(project_root(args.project)), args.output, args.reference)
    raise SystemExit(0 if result["status"] == "PASS" else 1)
