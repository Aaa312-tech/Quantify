"""Freeze all fit cohorts, targets, weights and matrices after independent input acceptance."""

from pathlib import Path

import numpy as np

from quantfolio.dataset import label_mask, load_snapshot, walk_forward_folds
from quantfolio.models import training_targets
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
base = root / p["checks_output"]
require(not (root / p["training_output"]).exists(), "Training has already started")
acceptance = read(base / "numeric_acceptance.json")
bundle_path = root / p["snapshot_output"] / "bundle.joblib"
require(
    acceptance["status"] == "PASS" and acceptance["bundle_sha256"] == sha(bundle_path),
    "Independent input acceptance missing",
)
new = load_snapshot(paths, bundle_path, p["source_data_cutoff"])
old = load_snapshot(paths, root / p["source_bundle"], p["source_data_cutoff"])
require(
    len(walk_forward_folds(new["calendar"])) == p["retraining"]["windows"], "Training range differs"
)
run = root / p["baseline_run"]
files = [*sorted((run / "training").glob("fold_*/result.json")), run / "final_fit/result.json"]
require(len(files) == p["retraining"]["windows"] + 1, "Baseline fit inventory differs")
table, previous = new["table"], old["table"]
matrix = table[new["features"]].to_numpy(dtype=np.float32)
old_matrix = previous[old["features"]].to_numpy(dtype=np.float32)
records = []
for file in files:
    result = read(file)
    fold, final = result["fold"], result["final_fit"]
    for stage, end, before in (
        ("validation_fit", fold["train_end"], fold["validation_start"]),
        (
            "outer_fit",
            fold["test_start"] - 1 if final else fold["validation_end"],
            fold["test_start"] + int(final),
        ),
    ):
        rows = np.flatnonzero(label_mask(table, 5, fold["train_start"], end, before))
        old_rows = np.flatnonzero(label_mask(previous, 5, fold["train_start"], end, before))
        fitted, target, weights, _ = training_targets(table, rows)
        old_fitted, old_target, old_weights, _ = training_targets(previous, old_rows)
        common, left, right = np.intersect1d(
            old_fitted, fitted, assume_unique=True, return_indices=True
        )
        features_equal = (old_matrix[common] == matrix[common]) | (
            np.isnan(old_matrix[common]) & np.isnan(matrix[common])
        )
        records.append(
            {
                "window": fold["fold"],
                "stage": stage,
                "fold": fold,
                "reference_rows_sha256": array_sha(rows),
                "fit_rows_sha256": array_sha(fitted),
                "target_sha256": array_sha(target),
                "weights_sha256": array_sha(weights),
                "feature_matrix_sha256": array_sha(matrix[fitted]),
                "reference_rows": len(rows),
                "fit_rows": len(fitted),
                "added_reference_rows": sorted(set(rows) - set(old_rows)),
                "removed_reference_rows": sorted(set(old_rows) - set(rows)),
                "added_fit_rows": sorted(set(fitted) - set(old_fitted)),
                "removed_fit_rows": sorted(set(old_fitted) - set(fitted)),
                "common_fit_rows": len(common),
                "common_targets_changed": int((old_target[left] != target[right]).sum()),
                "common_weights_changed": int((old_weights[left] != weights[right]).sum()),
                "common_feature_cells_changed": int((~features_equal).sum()),
                "prior_result_sha256": sha(file),
            }
        )
require(len(records) * 2 == p["retraining"]["fits"], "Registered estimator count differs")
save(
    base / "target_expectations.json",
    {
        "status": "FROZEN_BEFORE_TRAINING",
        "created_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "parent_bundle_sha256": sha(root / p["source_bundle"]),
        "bundle_sha256": sha(bundle_path),
        "numeric_acceptance_sha256": sha(base / "numeric_acceptance.json"),
        "model_code_sha256": sha(root / "src/quantfolio/models.py"),
        "source_sha256": {
            file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))
        },
        "records": records,
        "training_started": False,
        "scope": "Every stage in the complete registered range. Cohort, target, weight and feature changes are recorded; no date-only equality or monotonic cohort assumption is imposed. The original training target algorithm is applied to independently accepted inputs before any fitting.",
    },
    immutable=True,
)
print("FITTING_EXPECTATIONS_FROZEN", len(records), flush=True)
