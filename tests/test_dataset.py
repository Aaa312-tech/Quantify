"""Archived column names must not change model inputs or the stored snapshot."""

import joblib
import numpy as np
import pandas as pd
import pytest

from quantfolio.dataset import load_bundle
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
