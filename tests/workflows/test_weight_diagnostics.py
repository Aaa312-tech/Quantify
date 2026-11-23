from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from scripts import diagnose_weights

from quantfolio import models
from quantfolio.dataset import label_mask
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, read, save, sha


def weight_table():
    dates = np.repeat([1, 2, 5, 6], 4)
    return pd.DataFrame(
        {
            "date_index": dates,
            "end5": dates + 1,
            "status5": 0,
            "eligible": True,
            "symbol": ["a", "b", "c", "d"] * 4,
            "industry": "one",
            "y5": [0.1, -0.2, 0.3, 0.02] * 4,
            "momentum_percentile": 0.8,
        }
    )


def test_weight_trace_preserves_inputs_without_fitting(monkeypatch):
    def forbid_fit(*args, **kwargs):
        pytest.fail("Weight diagnostics must not construct or fit estimators")

    monkeypatch.setattr(models, "make_model", forbid_fit)
    monkeypatch.setattr(models, "fit_stage", forbid_fit)
    table = weight_table()
    before = table.copy(deep=True)
    arrays = diagnose_weights.weight_stages(table, np.arange(len(table)))
    assert array_sha(arrays["weights"]) == array_sha(arrays["native_weights"])
    np.testing.assert_array_equal(arrays["daily_count"], np.full(len(table), 4))
    pd.testing.assert_frame_equal(before, table)


@pytest.mark.parametrize("fault", ["unregistered", "bytes", "partial", "duplicate"])
def test_weight_reference_rejects_untrusted_or_incomplete_evidence(tmp_path, fault):
    reference = {
        "status": "PASS",
        "scope": "core" if fault == "partial" else "full",
        "baseline_commit": "baseline",
        "profile_sha256": "profile",
        "new_model_fits": 0,
        "training": {
            "fit_inputs": [
                {"window": "fold_01", "stage": "validation"},
                {"window": "fold_01", "stage": "validation" if fault == "duplicate" else "outer"},
            ]
        },
    }
    save(tmp_path / "reference.json", reference)
    digest = "wrong" if fault == "bytes" else sha(tmp_path / "reference.json")
    save(
        tmp_path / "manifest.json",
        {"files": {} if fault == "unregistered" else {"reference.json": digest}},
    )
    baseline = {
        "baseline_commit": "baseline",
        "profile_sha256": "profile",
        "expected": {"training_windows": 1},
    }
    paths = SimpleNamespace(root=tmp_path, manifest=tmp_path / "manifest.json")
    with pytest.raises(ValueError):
        diagnose_weights.read_reference(paths, "reference.json", baseline)


@pytest.mark.parametrize("drift", [False, True])
def test_weight_diagnostics_keep_one_ulp_drift_as_failure(tmp_path, monkeypatch, drift):
    table = weight_table()
    fold = {
        "train_start": 1,
        "train_end": 2,
        "validation_start": 5,
        "validation_end": 6,
        "test_start": 9,
    }
    records = []
    for stage, end, available in (("validation", 2, 5), ("outer", 6, 9)):
        rows = np.flatnonzero(label_mask(table, 5, 1, end, available))
        selected, target, weights, _ = models.training_targets(table, rows)
        if drift:
            weights[0] = np.nextafter(weights[0], np.inf)
        records.append(
            {
                "window": "fold_01",
                "stage": stage,
                "fold": fold,
                "train_rows_sha256": array_sha(rows),
                "fit_rows_sha256": array_sha(selected),
                "target_sha256": array_sha(target),
                "weights_sha256": array_sha(weights),
            }
        )
    monkeypatch.setattr(diagnose_weights, "read_baseline", lambda paths: {})
    monkeypatch.setattr(diagnose_weights, "check_inputs", lambda *args: None)
    monkeypatch.setattr(diagnose_weights, "read_reference", lambda *args: (records, "frozen"))
    monkeypatch.setattr(diagnose_weights, "load_bundle", lambda paths: {"table": table})
    monkeypatch.setattr(diagnose_weights, "environment", lambda: {})
    monkeypatch.setattr(diagnose_weights.subprocess, "check_output", lambda *args, **kwargs: "test")
    output = tmp_path / "artifacts/checks/weights"
    result = diagnose_weights.diagnose(Paths(tmp_path), output, "reference.json")
    assert result["status"] == ("FAIL" if drift else "PASS")
    assert len(result["records"]) == 2
    assert all(item["checks"]["weights"] != drift for item in result["records"])
    assert result["new_model_fits"] == 0
    assert read(output / "result.json") == result
    assert len(list(output.rglob("stages.npz"))) == 2


def test_weight_diagnostics_refuse_existing_or_external_output(tmp_path):
    paths = Paths(tmp_path)
    with pytest.raises(ValueError, match="artifacts/checks"):
        diagnose_weights.diagnose(paths, tmp_path / "outside", "reference.json")
    output = tmp_path / "artifacts/checks/existing"
    output.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="unused"):
        diagnose_weights.diagnose(paths, output, "reference.json")
