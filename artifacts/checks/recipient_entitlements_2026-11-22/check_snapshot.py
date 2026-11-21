"""Reconcile every numeric snapshot cell against independently frozen expectations."""

from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.dataset import load_snapshot, validate_features
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, digest, load_gzip, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fingerprint(frame):
    result = {}
    for name in frame:
        series = frame[name]
        if not pd.api.types.is_numeric_dtype(series):
            continue
        values = series.to_numpy(copy=True)
        if values.dtype.kind == "f":
            values[np.isnan(values)] = np.nan
            values[values == 0] = 0
        result[str(name)] = {
            "sha256": array_sha(values),
            "dtype": str(values.dtype),
            "missing": int(series.isna().sum()),
        }
    return result


root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
base = root / p["checks_output"]
require(not (root / p["training_output"]).exists(), "Training precedes input acceptance")
features = read(base / "feature_expectations.json")
labels = read(base / "label_scope.json")
require(features["status"] == "EXPECTED_FEATURES_FROZEN", "Independent features incomplete")
require(labels["status"] == "EXPECTED_LABELS_FROZEN", "Independent labels incomplete")
require(
    features["checker_sha256"] == sha(base / "expect_features.py"),
    "Executed feature source changed",
)
require(labels["checker_sha256"] == sha(base / "check_labels.py"), "Executed label source changed")
require(
    labels["expectations_sha256"] == sha(base / "label_expectations.json.gz"),
    "Independent labels changed",
)
old = load_snapshot(paths, root / p["source_bundle"], p["source_data_cutoff"])
bundle_path = root / p["snapshot_output"] / "bundle.joblib"
new = load_snapshot(paths, bundle_path, p["source_data_cutoff"])
require(new.keys() == old.keys(), "Snapshot sections differ")
for key in ("meta", "opening_status", "features", "cutoff", "fund_mode"):
    require(new.get(key) == old.get(key), "Unexpected metadata change: " + key)
require(new["fund_mode"] == "reported", "Fund mode changed")
pd.testing.assert_index_equal(new["calendar"], old["calendar"], exact=True)
pd.testing.assert_index_equal(new["table"].columns, old["table"].columns, exact=True)
pd.testing.assert_index_equal(new["table"].index, old["table"].index, exact=True)
require(new["raws"].keys() == old["raws"].keys(), "Raw universe differs")
for symbol, frame in new["raws"].items():
    pd.testing.assert_frame_equal(frame, old["raws"][symbol], check_exact=True)
for section in ("panels", "scores"):
    require(new[section].keys() == old[section].keys(), "Numeric panel inventory differs")
    for name, frame in new[section].items():
        pd.testing.assert_index_equal(frame.index, old[section][name].index, exact=True)
        pd.testing.assert_index_equal(frame.columns, old[section][name].columns, exact=True)
        require(
            fingerprint(frame) == features[section][name],
            "Independent panel mismatch: " + section + "/" + name,
        )
for name in ("symbol", "industry", "date_index", "eligible"):
    pd.testing.assert_series_equal(old["table"][name], new["table"][name], check_exact=True)
label_columns = [
    f"{name}{horizon}" for horizon in (1, 3, 5) for name in ("y", "end", "status", "delay")
]
non_labels = [name for name in new["table"] if name not in label_columns]
require(
    fingerprint(new["table"][non_labels]) == features["table"], "Independent table feature mismatch"
)
expected_labels = old["table"][label_columns].copy(deep=True)
expectations = load_gzip(base / "label_expectations.json.gz")
seen = set()
for record in expectations:
    position, horizon = record["row"], record["horizon"]
    require((position, horizon) not in seen, "Duplicate independent label tuple")
    seen.add((position, horizon))
    row = old["table"].iloc[position]
    require(
        row.symbol == record["symbol"] and row.date_index == record["date_index"] and row.eligible,
        "Independent label row differs",
    )
    names = [name + str(horizon) for name in ("y", "end", "status", "delay")]
    before = [None if pd.isna(row[name]) else row[name] for name in names]
    require(before == record["before"], "Independent parent label differs")
    for name, value in zip(names, record["after"], strict=True):
        expected_labels.iat[position, expected_labels.columns.get_loc(name)] = (
            np.nan if value is None else value
        )
require(
    len(seen) == labels["independent_label_tuples_per_input"], "Label expectation inventory differs"
)
pd.testing.assert_frame_equal(expected_labels, new["table"][label_columns], check_exact=True)
require(new["actions"] == read(base / "expected_actions.json"), "Independent action mismatch")
require(
    digest(new["actions"]) == features["actions_sha256"] == labels["actions_sha256"],
    "Numeric reviews bind different actions",
)
changes = read(bundle_path.parent / "action_changes.json")
require(changes == read(base / "expected_action_changes.json"), "Applied field changes differ")
counts = Counter(field for record in changes for field in record["fields"])
require(
    {**counts, "events": len(changes)} == p["expected_changes"], "Registered field scope differs"
)
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
require(
    sources
    == new["provenance"]["source_sha256"]
    == read(base / "implementation.json")["source_sha256"],
    "Executed runtime identity differs",
)
actual_changes = read(bundle_path.parent / "field_changes.json")["table"]
require(
    {name: actual_changes[name] for name in non_labels if actual_changes[name]}
    == features["expected_changed_cells"],
    "Feature change attribution differs",
)
actual_labels = load_gzip(bundle_path.parent / "label_changes.json.gz")
require(len(actual_labels) == labels["expected_changed_tuples"], "Label changes differ")
require(
    dict(Counter(str(record["horizon"]) for record in actual_labels))
    == labels["expected_changes_by_horizon"],
    "Label horizon attribution differs",
)
save(
    base / "numeric_acceptance.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "source_sha256": sources,
        "bundle_sha256": sha(bundle_path),
        "parent_bundle_sha256": sha(root / p["source_bundle"]),
        "actions_sha256": digest(new["actions"]),
        "feature_expectations_sha256": sha(base / "feature_expectations.json"),
        "label_scope_sha256": sha(base / "label_scope.json"),
        "rows": len(new["table"]),
        "columns": len(new["table"].columns),
        "independent_label_tuples": len(seen),
        "changed_events": len(changes),
        "changed_fields": dict(counts),
        "changed_label_tuples": len(actual_labels),
        "changed_labels_by_horizon": labels["expected_changes_by_horizon"],
        "changed_non_label_cells": features["expected_changed_cells"],
        "all_values_outside_independently_predicted_changes_exact": True,
        "raw_calendar_metadata_status_eligibility_exact": True,
        "full_feature_panels_scores_and_cross_section_ranks_match_independent_expectations": True,
        "features": validate_features(new),
        "training_started": False,
        "activated": False,
        "limitations": p["limitations"],
    },
    immutable=True,
)
print("INDEPENDENT_NUMERIC_ACCEPTANCE_PASSED", len(actual_labels), "label changes", flush=True)
