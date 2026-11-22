"""Archived column names must not change model inputs or the stored snapshot."""

import joblib
import numpy as np
import pandas as pd
import pytest

from quantfolio.dataset import load_bundle, load_snapshot
from quantfolio.settings import Paths
from quantfolio.storage import save, sha


@pytest.mark.parametrize("collision", [False, True])
def test_archived_feature_names_preserve_values_order_and_snapshot(tmp_path, collision):
    table = pd.DataFrame(
        {"old_factor": [1.0, np.nan, 3.0], "unchanged": [7.0, 8.0, 9.0]}, dtype="float32"
    )
    if collision:
        table["rank_factor"] = table.old_factor
    original = {
        "table": table,
        "features": ["unchanged", "old_factor"],
        "cutoff": "2026-11-18",
        "meta": {},
        "raws": {},
        "actions": {},
        "panels": {},
        "calendar": [],
    }
    joblib.dump(original, tmp_path / "bundle.joblib")
    checksum = sha(tmp_path / "bundle.joblib")
    config = tmp_path / "configs"
    config.mkdir()
    (config / "project.toml").write_text(
        'bundle = "bundle.joblib"\n'
        'manifest = "configs/integrity_manifest.json"\n'
        'archive = "configs/archive.toml"\n'
        'data_cutoff = "2026-11-18"\n',
        encoding="utf-8",
    )
    (config / "archive.toml").write_text(
        '[features]\nold_factor = "rank_factor"\n', encoding="utf-8"
    )
    save(config / "integrity_manifest.json", {"files": {"bundle.joblib": checksum}})
    if collision:
        with pytest.raises(ValueError, match="collide"):
            load_bundle(Paths(tmp_path))
    else:
        loaded = load_bundle(Paths(tmp_path))
        assert loaded["features"] == ["unchanged", "rank_factor"]
        pd.testing.assert_frame_equal(
            loaded["table"], table.rename(columns={"old_factor": "rank_factor"})
        )
        np.testing.assert_array_equal(
            loaded["table"][loaded["features"]].to_numpy(),
            original["table"][original["features"]].to_numpy(),
        )
    assert sha(tmp_path / "bundle.joblib") == checksum


def test_reference_snapshot_remains_independent_of_active_data(tmp_path):
    config = tmp_path / "configs"
    config.mkdir()
    (config / "project.toml").write_text(
        'bundle = "active.joblib"\nreference_bundle = "reference.joblib"\n'
        'data_cutoff = "2026-11-19"\nreference_data_cutoff = "2026-11-18"\n'
        'manifest = "configs/manifest.json"\narchive = "configs/archive.toml"\n',
        encoding="utf-8",
    )
    (config / "archive.toml").write_text("", encoding="utf-8")
    hashes = {}
    for name, cutoff, value in (("reference", "2026-11-18", 1.0), ("active", "2026-11-19", 2.0)):
        bundle = {
            "table": pd.DataFrame({"factor": [value]}),
            "features": ["factor"],
            "cutoff": cutoff,
            "meta": {},
            "raws": {},
            "actions": [],
            "panels": {},
            "calendar": [],
        }
        path = tmp_path / f"{name}.joblib"
        joblib.dump(bundle, path)
        hashes[path.name] = sha(path)
    save(config / "manifest.json", {"files": hashes})
    paths = Paths(tmp_path)
    assert load_bundle(paths)["table"].at[0, "factor"] == 2.0
    assert load_bundle(paths, reference=True)["table"].at[0, "factor"] == 1.0
    assert sha(tmp_path / "reference.joblib") == hashes["reference.joblib"]
    assert (
        load_snapshot(paths, tmp_path / "reference.joblib", "2026-11-18")["cutoff"] == "2026-11-18"
    )
    with pytest.raises(ValueError, match="cutoff"):
        load_snapshot(paths, tmp_path / "reference.joblib", "2026-11-19")
    with pytest.raises(ValueError, match="inside"):
        load_snapshot(paths, tmp_path.parent / "outside.joblib", "2026-11-18")
