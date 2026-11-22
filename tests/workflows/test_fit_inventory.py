"""Fit comparisons must reject incomplete runs at any registered window count."""

import shutil

import numpy as np
import pytest

from quantfolio.feasibility import fit_parity
from quantfolio.storage import digest, read, save


def fitted_run(root, windows=1, *, first=1, last=None, latest=False):
    last = windows if last is None else last
    folds = [{"fold": index, "test_start": index * 21} for index in range(1, windows + 1)]
    specification = {"folds": folds, "selected_folds": [first, last], "include_latest": latest}
    identity = digest(specification)
    save(root / "protocol.json", {"identity": identity, "specification": specification})
    save(
        root / "training_status.json",
        {
            "identity": identity,
            "status": "COMPLETE" if first == 1 and last == windows else "PARTIAL",
            "folds_completed": last - first + 1,
            "latest_fit_included": latest,
        },
    )
    folders = {f"training/fold_{fold['fold']:02d}": fold for fold in folds[first - 1 : last]}
    if latest:
        folders["final_fit"] = {"fold": "final"}
    for folder, fold in folders.items():
        base = root / folder
        save(
            base / "result.json",
            {
                "identity": identity,
                "fold": fold,
                "final_fit": folder == "final_fit",
                "selected_candidate": "momentum",
                "selected_rules": "baseline",
            },
        )
        np.savez(base / "outer_predictions.npz", row_indices=[0, 1], common_mu=[0.1, 0.2])
        for stage in ("validation_fit", "outer_fit"):
            for family in ("ridge", "tree"):
                fitted = base / stage / family
                fitted.mkdir(parents=True)
                (fitted / "model.joblib").write_bytes(b"comparison fixture, never deserialized")
                np.save(fitted / "predictions.npy", np.array([0.1, 0.2]))
    return root


@pytest.mark.parametrize("windows", [1, 11, 12])
@pytest.mark.parametrize("latest", [False, True])
def test_comparison_covers_the_registered_number_of_fits(tmp_path, windows, latest):
    before = fitted_run(tmp_path / "before", windows, latest=latest)
    after = shutil.copytree(before, tmp_path / "after")
    result = fit_parity(before, after)
    assert result["status"] == "PASS"
    assert len(result["models"]) == 4 * (windows + latest)
    assert len(result["forecasts"]) == windows + latest


def test_explicit_partial_range_has_its_own_complete_inventory(tmp_path):
    before = fitted_run(tmp_path / "before", 12, first=3, last=5, latest=True)
    after = shutil.copytree(before, tmp_path / "after")
    result = fit_parity(before, after)
    assert result["status"] == "PASS"
    assert len(result["models"]) == 16 and len(result["forecasts"]) == 4


@pytest.mark.parametrize("filename", ["model.joblib", "outer_predictions.npz"])
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_missing_or_extra_artifacts_never_pass(tmp_path, filename, change):
    before = fitted_run(tmp_path / "before", 11, latest=True)
    after = shutil.copytree(before, tmp_path / "after")
    if change == "missing":
        next(after.rglob(filename)).unlink()
    else:
        (after / filename).write_bytes(b"unregistered")
    with pytest.raises(ValueError, match="inventory differs"):
        fit_parity(before, after)


@pytest.mark.parametrize(
    "field,value",
    [
        ("identity", "wrong"),
        ("folds_completed", 2),
        ("status", "PARTIAL"),
        ("latest_fit_included", True),
    ],
)
def test_inconsistent_completion_is_rejected(tmp_path, field, value):
    before = fitted_run(tmp_path / "before")
    after = shutil.copytree(before, tmp_path / "after")
    status = read(after / "training_status.json")
    status[field] = value
    save(after / "training_status.json", status)
    with pytest.raises(ValueError, match="Fit comparison training"):
        fit_parity(before, after)


def test_empty_or_different_registered_inventory_is_rejected(tmp_path):
    empty = fitted_run(tmp_path / "empty", 0)
    before = fitted_run(tmp_path / "before", 1)
    after = fitted_run(tmp_path / "after", 2)
    with pytest.raises(ValueError, match="training range"):
        fit_parity(empty, empty)
    with pytest.raises(ValueError, match="different registered artifacts"):
        fit_parity(before, after)


@pytest.mark.parametrize("artifact", ["model", "predictions", "forecast"])
def test_changed_model_or_prediction_is_not_parity(tmp_path, artifact):
    before = fitted_run(tmp_path / "before")
    after = shutil.copytree(before, tmp_path / "after")
    if artifact == "model":
        next(after.rglob("model.joblib")).write_bytes(b"changed estimator")
    elif artifact == "predictions":
        np.save(next(after.rglob("predictions.npy")), np.array([0.1, 0.21]))
    else:
        np.savez(
            next(after.rglob("outer_predictions.npz")), row_indices=[0, 1], common_mu=[0.1, 0.21]
        )
    assert fit_parity(before, after)["status"] == "FAIL"


@pytest.mark.parametrize(
    "field,value", [("identity", "wrong"), ("fold", {"fold": 2}), ("final_fit", True)]
)
def test_window_identity_must_match_registration(tmp_path, field, value):
    before = fitted_run(tmp_path / "before")
    after = shutil.copytree(before, tmp_path / "after")
    file = after / "training/fold_01/result.json"
    result = read(file)
    result[field] = value
    save(file, result)
    with pytest.raises(ValueError, match="window identity"):
        fit_parity(before, after)
