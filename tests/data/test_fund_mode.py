"""Dataset rebuilds must preserve the declared fund control and reject silent audit loss."""

from copy import deepcopy

import joblib
import pytest

from quantfolio.dataset import fund_mode, load_bundle, load_snapshot
from quantfolio.preprocessing import build_dataset, compare_history
from quantfolio.settings import Paths
from quantfolio.snapshots import repair_snapshot
from quantfolio.storage import save, sha
from tests.factories import fund_history_entry, fund_profile, raw_history


def fund_dataset():
    meta, raws = raw_history()
    meta["sh510300"]["fund_history"] = [
        fund_history_entry(raws["sh510300"].index[0], fund_profile())
    ]
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


@pytest.mark.parametrize("mode", ["coverage", "identity", "reported"])
def test_each_registered_control_survives_rebuilding(mode):
    meta, raws = raw_history()
    value = fund_profile()
    value["mode"] = mode
    meta["sh510300"]["fund_history"] = [fund_history_entry(raws["sh510300"].index[0], value)]
    rebuilt = build_dataset(meta, raws, [])
    assert rebuilt["fund_mode"] == mode
    assert compare_history(rebuilt, deepcopy(rebuilt))["status"] == "PASS"


@pytest.mark.parametrize("case", ["mixed", "unknown", "unidentified", "stock"])
def test_fund_controls_cannot_be_ambiguous(case):
    meta, raws = raw_history()
    instrument = meta["sh510300"]
    first = fund_history_entry(raws["sh510300"].index[0], fund_profile())
    instrument["fund_history"] = [first]
    if case == "mixed":
        second = deepcopy(first)
        second["profile"]["mode"] = "identity"
        instrument["fund_history"].append(second)
    elif case == "unknown":
        first["profile"]["mode"] = "unregistered"
    elif case == "unidentified":
        first["profile"] = None
        first["industry"] = None
    else:
        instrument["kind"] = "stock"
    with pytest.raises(ValueError, match="Fund"):
        fund_mode(meta)


def test_label_only_repair_keeps_reported_audits_enabled(tmp_path):
    bundle = fund_dataset()
    file = tmp_path / "bundle.joblib"
    joblib.dump(bundle, file)
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "project.toml").write_text(
        'bundle = "bundle.joblib"\nmanifest = "configs/manifest.json"\n'
        'archive = "configs/archive.toml"\n' + f'data_cutoff = "{bundle["cutoff"]}"\n',
        encoding="utf-8",
    )
    (folder / "archive.toml").write_text(
        '[action_fields]\ndeclared_payment_date = "declared_payment_date"\n', encoding="utf-8"
    )
    save(folder / "manifest.json", {"files": {"bundle.joblib": sha(file)}})
    review = folder / "review.json"
    save(review, {"data_snapshot_sha256": sha(file), "facts": []})
    parent = folder / "protocols/parent.json"
    save(parent, {})
    protocol = folder / "protocols/repair.json"
    save(
        protocol,
        {
            "review": "configs/review.json",
            "review_sha256": sha(review),
            "source_bundle": "bundle.joblib",
            "source_bundle_sha256": sha(file),
            "parent_protocol": "configs/protocols/parent.json",
        },
    )
    paths = Paths(tmp_path)
    audit = repair_snapshot(paths, "data/processed/repaired", "configs/protocols/repair.json")
    assert audit["status"] == "PASS"
    repaired = load_snapshot(
        paths, tmp_path / "data/processed/repaired/bundle.joblib", bundle["cutoff"]
    )
    assert repaired["fund_mode"] == "reported"
    assert compare_history(bundle, repaired)["status"] == "PASS"
    assert repaired["actions"] == bundle["actions"]
