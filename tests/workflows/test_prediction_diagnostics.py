import numpy as np
import pytest
from scripts.diagnose_predictions import compare_predictions, diagnose, fingerprint, inspect_model
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from quantfolio.settings import Paths


def test_diagnostic_keeps_sub_tolerance_differences_visible():
    expected = np.array([1e-10, 1.0], dtype=np.float64)
    actual = expected.copy()
    actual[0] += 1e-14
    result = compare_predictions(actual, expected)
    assert not result["exact"]
    assert result["different"] == 1
    assert result["above_1e_12"] == 0


@pytest.mark.parametrize(
    "actual", [np.array([np.nan]), np.array([np.inf]), np.array([1], dtype=np.float32), np.ones(2)]
)
def test_diagnostic_rejects_nonfinite_and_structural_changes(actual):
    assert not compare_predictions(actual, np.ones(1))["exact"]


def test_array_fingerprint_separates_values_from_layout():
    matrix = np.arange(12, dtype=np.float32).reshape(4, 3)
    row_major = fingerprint(matrix)
    column_major = fingerprint(np.asfortranarray(matrix))
    assert row_major["sha256"] == column_major["sha256"]
    assert row_major["strides"] != column_major["strides"]


def test_pipeline_diagnostics_do_not_fit_or_change_model(monkeypatch):
    matrix = np.array([[1, np.nan], [2, 4], [3, 8], [4, 9]], dtype=np.float32)
    model = make_pipeline(SimpleImputer(add_indicator=True), StandardScaler(), Ridge())
    model.fit(matrix, np.array([0, 1, 3, 2], dtype=np.float32))
    expected = model.predict(matrix)
    coefficients = model[-1].coef_.copy()

    def forbid_fit(*args, **kwargs):
        pytest.fail("Diagnostics must not fit models")

    monkeypatch.setattr(model, "fit", forbid_fit)
    record, actual = inspect_model(model, matrix, expected)
    assert record["comparison"]["exact"]
    assert record["staged_comparison"]["exact"]
    assert list(record["stages"]) == ["input", "simpleimputer", "standardscaler"]
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(model[-1].coef_, coefficients)


def test_diagnostics_refuse_existing_or_external_output(tmp_path):
    paths = Paths(tmp_path)
    with pytest.raises(ValueError, match="artifacts/checks"):
        diagnose(paths, tmp_path / "outside")
    output = tmp_path / "artifacts/checks/existing"
    output.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="unused"):
        diagnose(paths, output)
