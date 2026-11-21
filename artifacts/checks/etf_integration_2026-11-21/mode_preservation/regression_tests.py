"""Dataset rebuilds must preserve the declared fund control and reject silent audit loss."""

from copy import deepcopy

import joblib
import pytest
from test_funds import entry, profile
from test_preprocessing import inputs

from quantfolio.dataset import load_bundle
from quantfolio.preprocessing import build_dataset, compare_history
from quantfolio.settings import Paths
from quantfolio.storage import save, sha


def fund_dataset():
    meta, raws = inputs()
    meta["sh510300"]["fund_history"] = [entry(raws["sh510300"].index[0], profile())]
    return build_dataset(meta, raws, [])


def test_rebuild_preserves_reported_fund_mode():
    assert fund_dataset().get("fund_mode") == "reported"


def test_history_comparison_rejects_dropped_mode():
    before = fund_dataset()
    before["fund_mode"] = "reported"
    after = deepcopy(before)
    del after["fund_mode"]
    with pytest.raises(ValueError, match="[Ff]und"):
        compare_history(before, after)


@pytest.mark.parametrize("mode", [None, "coverage"])
def test_loader_rejects_missing_or_conflicting_mode(tmp_path, mode):
    bundle = fund_dataset()
    bundle.pop("fund_mode", None)
    if mode is not None:
        bundle["fund_mode"] = mode
    file = tmp_path / "bundle.joblib"
    joblib.dump(bundle, file)
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "project.toml").write_text(
        'bundle = "bundle.joblib"\nmanifest = "configs/manifest.json"\n'
        'archive = "configs/archive.toml"\n' + f'data_cutoff = "{bundle["cutoff"]}"\n',
        encoding="utf-8",
    )
    (folder / "archive.toml").write_text("", encoding="utf-8")
    save(folder / "manifest.json", {"files": {"bundle.joblib": sha(file)}})
    with pytest.raises(ValueError, match="[Ff]und"):
        load_bundle(Paths(tmp_path))
