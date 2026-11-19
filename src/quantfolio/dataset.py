"""Load the frozen, self-contained research bundle and validate portable integrity."""

from __future__ import annotations

from typing import Any

import joblib
import pandas as pd

from .features import features
from .settings import Paths
from .storage import read, sha


def check_integrity(paths: Paths) -> dict[str, Any]:
    """Verify every retained input and reference file before deserializing models."""
    manifest = read(paths.manifest)
    errors = []
    for relative, expected in manifest["files"].items():
        path = (paths.root / relative).resolve()
        if not path.is_relative_to(paths.root.resolve()):
            raise ValueError(f"Manifest path escapes project: {relative}")
        if not path.is_file() or sha(path) != expected:
            errors.append(relative)
    if errors:
        raise ValueError(f"Reference integrity failed: {errors[:10]}")
    return {"status": "PASS", "files": len(manifest["files"])}


def load_bundle(paths: Paths) -> dict[str, Any]:
    """Load only the local, hash-verified trusted bundle; never arbitrary downloaded pickle."""
    manifest = read(paths.manifest)
    relative = paths.bundle.relative_to(paths.root).as_posix()
    if sha(paths.bundle) != manifest["files"][relative]:
        raise ValueError("Dataset hash mismatch")
    bundle = joblib.load(paths.bundle)
    required = {"meta", "raws", "actions", "panels", "calendar", "table", "features", "cutoff"}
    if required - bundle.keys():
        raise ValueError(f"Missing dataset fields: {required - bundle.keys()}")
    return bundle


def validate_features(bundle: dict[str, Any]) -> dict[str, Any]:
    """Rebuild industry features and prove future rows do not change historical prefixes."""
    table = bundle["table"]
    rebuilt, _ = features(table.copy(deep=True), bundle["meta"])
    pd.testing.assert_frame_equal(table[rebuilt.columns], rebuilt)
    for cutoff in (300, 500, 650):
        prefix = table.loc[table.date_index <= cutoff].copy(deep=True)
        actual, _ = features(prefix, bundle["meta"])
        pd.testing.assert_frame_equal(actual, rebuilt.loc[prefix.index])
    return {
        "status": "PASS",
        "rows": len(table),
        "features": len(bundle["features"]),
        "prefix_cutoffs": [300, 500, 650],
    }
