"""Load the frozen, self-contained research bundle and validate portable integrity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from .features import industry_features, momentum_features
from .settings import Paths
from .storage import read, sha


def check_integrity(paths: Paths) -> dict[str, Any]:
    """Verify every frozen input and reference file before deserializing models."""
    manifest = read(paths.manifest)
    errors = []
    for relative, expected in manifest["files"].items():
        path = (paths.root / relative).resolve()
        if not path.is_relative_to(paths.root.resolve()):
            raise ValueError(f"Manifest path escapes project: {relative}")
        if not path.is_file() or sha(path) != expected:
            errors.append(relative)
    if errors:
        if any(_lfs_pointer(paths.root / relative) for relative in errors):
            raise ValueError("Git LFS objects are missing. Run 'git lfs pull' in the project.")
        raise ValueError(f"Reference integrity failed: {errors[:10]}")
    return {"status": "PASS", "files": len(manifest["files"])}


def _lfs_pointer(path: Path) -> bool:
    return (
        path.is_file()
        and path.stat().st_size < 1024
        and path.read_bytes().startswith(b"version https://git-lfs.github.com/spec/v1")
    )


def load_bundle(paths: Paths, *, reference: bool = False) -> dict[str, Any]:
    """Load only the local, hash-verified trusted bundle; never arbitrary downloaded pickle."""
    path = paths.reference_bundle if reference else paths.bundle
    cutoff = (
        paths.config.get("reference_data_cutoff", paths.config["data_cutoff"])
        if reference
        else paths.config["data_cutoff"]
    )
    return load_snapshot(paths, path, cutoff)


def load_snapshot(paths: Paths, path: Path, cutoff: str) -> dict[str, Any]:
    """Load a registered local snapshot against its expected cutoff and frozen hash."""
    path = path.resolve()
    if not path.is_relative_to(paths.root):
        raise ValueError("Dataset must stay inside the project")
    manifest = read(paths.manifest)
    relative = path.relative_to(paths.root).as_posix()
    if _lfs_pointer(path):
        raise ValueError("Dataset is a Git LFS pointer. Run 'git lfs pull' in the project.")
    if sha(path) != manifest["files"][relative]:
        raise ValueError("Dataset hash mismatch; restore the verified snapshot before using it")
    bundle = joblib.load(path)
    required = {"meta", "raws", "actions", "panels", "calendar", "table", "features", "cutoff"}
    if required - bundle.keys():
        raise ValueError(f"Missing dataset fields: {required - bundle.keys()}")
    if str(bundle["cutoff"])[:10] != cutoff:
        raise ValueError("Dataset cutoff differs from the registered inputs")
    aliases = paths.archive.get("features", {})
    columns = [aliases.get(name, name) for name in bundle["table"].columns]
    features = [aliases.get(name, name) for name in bundle["features"]]
    if len(set(columns)) != len(columns) or len(set(features)) != len(features):
        raise ValueError("Archived feature names collide with current columns")
    # Rename the freshly loaded frame; preserve values, dtypes and model input order.
    bundle["table"].columns = columns
    bundle["features"] = features
    return bundle


def validate_features(bundle: dict[str, Any]) -> dict[str, Any]:
    """Rebuild derived ranks and prove future rows do not change historical prefixes."""
    table = bundle["table"]
    industry, _ = industry_features(table, bundle["meta"], bundle["calendar"])
    rebuilt = pd.concat([momentum_features(table), industry], axis=1)
    pd.testing.assert_frame_equal(table[rebuilt.columns], rebuilt)
    for cutoff in (300, 500, 650):
        prefix = table.loc[table.date_index <= cutoff].copy(deep=True)
        industry, _ = industry_features(prefix, bundle["meta"], bundle["calendar"])
        actual = pd.concat([momentum_features(prefix), industry], axis=1)
        pd.testing.assert_frame_equal(actual, rebuilt.loc[prefix.index])
    return {
        "status": "PASS",
        "rows": len(table),
        "features": len(bundle["features"]),
        "prefix_cutoffs": [300, 500, 650],
    }


def walk_forward_folds(calendar, train=252, validation=63, test=21, gap=6, first=125):
    """Create chronological train, validation and test windows with purging gaps."""
    if min(train, validation, test) <= 0 or min(gap, first) < 0:
        raise ValueError("Window lengths must be positive; gap and first must be nonnegative")
    folds = []
    start = first
    while True:
        train_end = start + train - 1
        valid_start = train_end + gap + 1
        valid_end = valid_start + validation - 1
        test_start = valid_end + gap + 1
        test_end = test_start + test - 1
        if test_end + 6 >= len(calendar):
            break
        folds.append(
            {
                "fold": len(folds) + 1,
                "train_start": start,
                "train_end": train_end,
                "validation_start": valid_start,
                "validation_end": valid_end,
                "test_start": test_start,
                "test_end": test_end,
            }
        )
        start += test
    return folds


def label_mask(table, horizon, start, end, available_before):
    """Calendar grouping plus actual, possibly delayed, label-end purging."""
    dates = table.date_index.to_numpy()
    ends = table[f"end{horizon}"].to_numpy()
    return (
        (dates >= start)
        & (dates <= end)
        & table.eligible.to_numpy()
        & (table[f"status{horizon}"].to_numpy() == 0)
        & (ends < available_before)
        & (ends > dates)
        & np.isfinite(table[f"y{horizon}"].to_numpy())
    )


def eligible_rows(table, start, end):
    """Select eligible signal rows within an inclusive historical date-index range."""
    return np.flatnonzero(
        (table.date_index.to_numpy() >= start)
        & (table.date_index.to_numpy() <= end)
        & table.eligible.to_numpy()
    )
