"""Frozen dataset loading, checksums and source compatibility."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import joblib
import numpy as np
import pandas as pd

from .features import industry_features, momentum_features
from .integrity import dependency_compatible, runtime_sources
from .settings import Paths
from .storage import read, sha


def check_integrity(paths: Paths) -> dict[str, Any]:
    """Verify every file in the integrity manifest."""
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


def source_compatible(paths: Paths, subject: Path, expected: dict, current: dict) -> bool:
    """Accept matching source hashes or registered compatibility evidence."""
    if expected == current:
        return True
    if not expected or any(not isinstance(value, str) for value in expected.values()):
        return False
    root = paths.root.resolve()
    subject = subject.resolve()
    if not subject.is_relative_to(root) or not subject.is_file():
        return False
    try:
        manifest = read(paths.manifest)["files"]
    except (OSError, ValueError, KeyError):
        return False
    relative = subject.relative_to(root).as_posix()
    subject_hash = sha(subject)
    if manifest.get(relative) != subject_hash:
        return False
    runtime = runtime_sources(Path(__file__).parent)
    if current != {name: runtime.get(name) for name in expected}:
        return False

    if dependency_compatible(paths, subject, expected, Path(__file__).parent):
        return True

    def frozen_record(name, expected_hash):
        path = (root / name).resolve()
        if (
            not path.is_relative_to(root / "artifacts" / "checks")
            or manifest.get(name) != expected_hash
            or sha(path) != expected_hash
        ):
            raise ValueError("Unregistered source-equivalence evidence")
        return read(path)

    for name, certificate_hash in manifest.items():
        if not Path(name).match("artifacts/checks/*/source_compatibility.json"):
            continue
        try:
            certificate = frozen_record(name, certificate_hash)
            if (
                certificate["schema_version"] != 1
                or certificate["kind"] != "EXECUTION_REFACTOR"
                or certificate["runtime_sha256"] != runtime
                or certificate["subjects"].get(relative)
                != {"sha256": subject_hash, "source_sha256": expected}
            ):
                continue
            evidence = certificate["evidence"]
            required = {"EXECUTION_DIFFERENTIAL", "HISTORICAL_LEDGER_PARITY"}
            if set(evidence) != required:
                continue
            for kind, item in evidence.items():
                report = frozen_record(item["path"], item["sha256"])
                if (
                    report["status"] != "PASS"
                    or report["kind"] != kind
                    or report["runtime_sha256"] != runtime
                    or report["subjects"] != certificate["subjects"]
                ):
                    break
            else:
                return True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return False


def load_bundle(paths: Paths, *, reference: bool = False) -> dict[str, Any]:
    """Load the active or reference snapshot after hash verification."""
    path = paths.reference_bundle if reference else paths.bundle
    cutoff = (
        paths.config.get("reference_data_cutoff", paths.config["data_cutoff"])
        if reference
        else paths.config["data_cutoff"]
    )
    return load_snapshot(paths, path, cutoff)


def fund_mode(meta):
    """Read the common control mode declared by fund histories."""
    modes = set()
    has_history = False
    for instrument in meta.values():
        if "fund_history" not in instrument:
            continue
        has_history = True
        if instrument["kind"] != "etf":
            raise ValueError("Fund history requires an ETF")
        for entry in instrument["fund_history"]:
            profile = entry.get("profile")
            if profile is None:
                continue
            mode = profile.get("mode")
            if mode not in {"coverage", "identity", "reported"}:
                raise ValueError("Fund history has an unknown control")
            modes.add(mode)
    if not has_history:
        return None
    if len(modes) != 1:
        raise ValueError("Fund histories require one consistent declared control")
    return modes.pop()


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
    if bundle.get("fund_mode") != fund_mode(bundle["meta"]):
        raise ValueError("Fund mode differs from the stored profile histories")
    if str(bundle["cutoff"])[:10] != cutoff:
        raise ValueError("Dataset cutoff differs from the registered inputs")
    aliases = paths.archive.get("features", {})
    columns = [aliases.get(name, name) for name in bundle["table"].columns]
    features = [aliases.get(name, name) for name in bundle["features"]]
    if len(set(columns)) != len(columns) or len(set(features)) != len(features):
        raise ValueError("Archived feature names collide with current columns")
    # Apply archived aliases without changing model input order.
    bundle["table"].columns = columns
    bundle["features"] = features
    return cast(dict[str, Any], bundle)


def validate_features(bundle: dict[str, Any]) -> dict[str, Any]:
    """Rebuild derived features and compare historical prefixes."""
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
    folds: list[dict[str, int]] = []
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
