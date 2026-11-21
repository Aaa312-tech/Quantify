"""Freeze cohort and target expectations from accepted labels before any new fit."""

from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.dataset import label_mask, load_snapshot
from quantfolio.models import training_targets
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, now, read, save, sha

root = Path.cwd()
pfile = root / "configs/protocols/companion_payments.json"
p = read(pfile)
checks = root / p["checks_output"]
assert not (root / p["training_output"]).exists()
bundle = load_snapshot(Paths(root), root / p["source_bundle"], p["source_data_cutoff"])
review = read(checks / "input_closure.json")
assert review["status"] == "PASS"
old = bundle["table"]
expected = old.copy(deep=True)
label_columns = {f"{field}{h}" for h in (1, 3, 5) for field in ("y", "end", "status", "delay")}
non_labels = [name for name in old if name not in label_columns]
pd.testing.assert_frame_equal(old[non_labels], expected[non_labels], check_exact=True)
restored = set()
run = root / p["baseline_run"]
records = []
for file in [*sorted((run / "training").glob("fold_*/result.json")), run / "final_fit/result.json"]:
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
        rows = np.flatnonzero(label_mask(expected, 5, fold["train_start"], end, before))
        old_rows = np.flatnonzero(label_mask(old, 5, fold["train_start"], end, before))
        fitted, target, weights, _ = training_targets(expected, rows)
        old_fitted, old_target, old_weights, _ = training_targets(old, old_rows)
        added = sorted(set(rows) - set(old_rows))
        assert set(old_rows) <= set(rows) and set(added) <= restored
        assert set(old_fitted) <= set(fitted)
        common, left, right = np.intersect1d(
            old_fitted, fitted, assume_unique=True, return_indices=True
        )
        records.append(
            {
                "window": fold["fold"],
                "stage": stage,
                "reference_rows_sha256": array_sha(rows),
                "fit_rows_sha256": array_sha(fitted),
                "target_sha256": array_sha(target),
                "weights_sha256": array_sha(weights),
                "restored_reference_rows": added,
                "added_fit_rows": sorted(set(fitted) - set(old_fitted)),
                "common_fit_rows": len(common),
                "common_targets_changed": int((old_target[left] != target[right]).sum()),
                "common_weights_changed": int((old_weights[left] != weights[right]).sum()),
            }
        )
assert len(records) == 24
save(
    checks / "target_expectations.json",
    {
        "status": "FROZEN_BEFORE_TRAINING",
        "created_at": now(),
        "protocol_sha256": sha(pfile),
        "parent_bundle_sha256": sha(root / p["source_bundle"]),
        "independent_label_review_sha256": sha(checks / "input_closure.json"),
        "model_code_sha256": sha(root / "src/quantfolio/models.py"),
        "source_sha256": {f.name: sha(f) for f in (root / "src/quantfolio").glob("*.py")},
        "records": records,
        "training_started": False,
        "scope": "The independent payment-only scope requires every label cell to remain exact. All 24 training stages must subsequently match these expected cohort, target and weight hashes. No estimator is fitted or return-based choice made here.",
    },
    immutable=True,
)
print(
    "TARGET EXPECTATIONS FROZEN",
    len(records),
    "stages affected",
    sum(bool(r["restored_reference_rows"]) for r in records),
    flush=True,
)
