"""Fixed estimator definitions used by the retained V6 experiment."""

from __future__ import annotations

from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def make_model(name):
    """Construct the fixed Ridge or histogram-tree estimator used by the V6 protocol."""
    imputer = SimpleImputer(strategy="median", keep_empty_features=True, add_indicator=True)
    if name == "ridge":
        return make_pipeline(imputer, StandardScaler(), Ridge(alpha=1000.0))
    if name != "tree":
        raise ValueError(name)
    return make_pipeline(
        imputer,
        HistGradientBoostingRegressor(
            loss="squared_error",
            max_leaf_nodes=7,
            max_iter=100,
            learning_rate=0.05,
            min_samples_leaf=150,
            l2_regularization=30.0,
            early_stopping=False,
            random_state=20261119,
        ),
    )
