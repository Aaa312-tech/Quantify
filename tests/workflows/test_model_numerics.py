"""Float32 rounding cases and predictions captured with the original MKL environment."""

import numpy as np
import pytest
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer

from quantfolio.models import float32_fma, make_model, predict_model


@pytest.mark.parametrize(
    "bits,expected",
    [
        ((0x3F800001, 0x3FC00000, 0xA1800000), 0x3FC00001),
        ((0x3F800001, 0x3FC00000, 0x21800000), 0x3FC00002),
        ((0x3F800003, 0x3FC00000, 0x21800000), 0x3FC00005),
        ((0xBF800001, 0x3FC00000, 0x21800000), 0xBFC00001),
        ((0xBF800003, 0x3FC00000, 0xA1800000), 0xBFC00005),
        ((0x7F7FFFFF, 0x40000000, 0xFF7FFFFF), 0x7F7FFFFF),
        ((0x00000001, 0x3F000000, 0x00000000), 0x00000000),
        ((0x00000001, 0x3F000000, 0x00000001), 0x00000002),
        ((0x3F800001, 0x3F7FFFFF, 0xBF800000), 0x337FFFFE),
        ((0x7F7FFFFF, 0x40000000, 0x00000000), 0x7F800000),
        ((0x80000000, 0x3F800000, 0x80000000), 0x80000000),
        ((0x3F800000, 0x3F800000, 0xBF800000), 0x00000000),
        ((0x41F80000, 0x7D042108, 0xBF800000), 0x7F7FFFFF),
        ((0xC1F80000, 0x7D042108, 0x3F800000), 0xFF7FFFFF),
        ((0x41F80000, 0x7D042108, 0x3F800000), 0x7F800000),
        ((0xC1F80000, 0x7D042108, 0xBF800000), 0xFF800000),
        ((0x41F80000, 0x7D042108, 0x00000000), 0x7F800000),
        ((0xC1F80000, 0x7D042108, 0x00000000), 0xFF800000),
    ],
)
def test_fused_add_matches_independent_c_runtime_bits(bits, expected):
    left, right, total = np.array(bits, dtype=np.uint32).view(np.float32)
    actual = float32_fma(np.array([left]), right, np.array([total]))
    assert actual.view(np.uint32)[0] == expected


@pytest.fixture
def frozen_linear_case():
    random = np.random.default_rng(20261122)
    matrix = random.standard_normal((7, 61), dtype=np.float32)
    estimator = Ridge()
    estimator.coef_ = random.standard_normal(61, dtype=np.float32)
    estimator.intercept_ = np.float32(0.125)
    estimator.n_features_in_ = 61
    return make_pipeline(FunctionTransformer(), estimator), matrix


@pytest.mark.parametrize("layout", ["row", "column", "reversed"])
def test_ridge_preserves_original_prediction_bits_and_input_buffers(frozen_linear_case, layout):
    model, matrix = frozen_linear_case
    expected = np.array(
        [1094955364, 3239676186, 3247746310, 3248014371, 3232150471, 3246991023, 1082183363],
        dtype=np.uint32,
    )
    if layout == "column":
        matrix = np.asfortranarray(matrix)
    elif layout == "reversed":
        matrix, expected = matrix[::-1], expected[::-1]
    before, coefficients = matrix.copy(), model[-1].coef_.copy()
    actual = predict_model(model, matrix)
    np.testing.assert_array_equal(actual.view(np.uint32), expected)
    np.testing.assert_array_equal(matrix, before)
    np.testing.assert_array_equal(model[-1].coef_, coefficients)


@pytest.mark.parametrize("kind", ["matrix64", "coefficients64", "multioutput", "tree"])
def test_other_estimator_formats_use_the_original_predictor(frozen_linear_case, monkeypatch, kind):
    model, matrix = frozen_linear_case
    if kind == "matrix64":
        matrix = matrix.astype(np.float64)
    elif kind == "coefficients64":
        model[-1].coef_ = model[-1].coef_.astype(np.float64)
    elif kind == "multioutput":
        model[-1].coef_ = np.stack([model[-1].coef_] * 2)
    else:
        model = make_model("tree")
    expected = object()
    monkeypatch.setattr(model, "predict", lambda data: expected)
    assert predict_model(model, matrix) is expected


def test_ridge_rejects_coefficient_dimension_mismatch(frozen_linear_case):
    model, matrix = frozen_linear_case
    model[-1].coef_ = model[-1].coef_[:-1]
    with pytest.raises(ValueError, match="coefficient counts"):
        predict_model(model, matrix)
