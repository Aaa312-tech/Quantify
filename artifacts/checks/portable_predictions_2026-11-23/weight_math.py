"""Compare weight fingerprints without fitting models or changing research outputs."""

import math
import os
from pathlib import Path
from unittest.mock import patch

import numpy as np

from quantfolio.dataset import label_mask, load_bundle
from quantfolio.models import training_targets
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, read, save, sha


root = Path.cwd()
folder = Path(__file__).parent
output = folder / os.environ.get("WEIGHT_PROBE_OUTPUT", "weight_math.json")
if output.exists():
    raise FileExistsError(output)
local = read(root / "artifacts/checks/portable_inference_full_2026-11-23/result.json")
cloud = read(folder / "cloud/ci-regression-45046246783/result.json")
table = load_bundle(Paths(root))["table"]
original_exp2 = np.exp2


def scalar_math(function):
    def evaluate(values):
        unique, inverse = np.unique(values, return_inverse=True)
        return np.array([function(float(value)) for value in unique])[inverse]

    return evaluate


variants = {
    "native_array": original_exp2,
    "numpy_scalar": scalar_math(original_exp2),
    "math_exp2": scalar_math(math.exp2),
    "math_pow": scalar_math(lambda value: math.pow(2.0, value)),
}
records = []
for before, after in zip(local["training"]["fit_inputs"], cloud["training"]["fit_inputs"], strict=True):
    fold = before["fold"]
    if before["stage"] == "validation":
        end, available = fold["train_end"], fold["validation_start"]
    elif before["stage"] == "final":
        end, available = fold["test_start"] - 1, fold["test_start"] + 1
    else:
        end, available = fold["validation_end"], fold["test_start"]
    rows = np.flatnonzero(label_mask(table, 5, fold["train_start"], end, available))
    assert array_sha(rows) == before["train_rows_sha256"] == after["train_rows_sha256"]
    reference = None
    cases = {}
    for name, function in variants.items():
        with patch("numpy.exp2", function):
            selected, targets, weights, _ = training_targets(table, rows)
        assert array_sha(selected) == before["fit_rows_sha256"] == after["fit_rows_sha256"]
        assert array_sha(targets) == before["target_sha256"] == after["target_sha256"]
        if reference is None:
            reference = weights
        digest = array_sha(weights)
        cases[name] = {
            "sha256": digest,
            "matches_local": digest == before["weights_sha256"],
            "matches_cloud": digest == after["weights_sha256"],
            "different_from_native": int(np.count_nonzero(weights != reference)),
            "max_absolute_difference": float(np.max(np.abs(weights - reference))),
        }
    records.append({"window": before["window"], "stage": before["stage"], "cases": cases})
save(
    output,
    {
        "scope": "Weight calculation only; no model fitting, calibration or strategy replay",
        "script_sha256": sha(__file__),
        "source_sha256": local["source_sha256"],
        "environment": {name: os.environ.get(name) for name in ("MKL_CBWR", "MKL_ENABLE_INSTRUCTIONS")},
        "records": records,
        "cloud_matches": {name: sum(row["cases"][name]["matches_cloud"] for row in records) for name in variants},
        "new_model_fits": 0,
    },
    immutable=True,
)
print({name: sum(row["cases"][name]["matches_cloud"] for row in records) for name in variants})
