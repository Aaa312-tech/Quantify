"""Industry-residual targets, causal prediction blends and factor diagnostics."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict, cast

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .calibration import map_calibration, rank_by_date
from .storage import array_sha, digest, log, now, read, save, sha

CANDIDATES = ["momentum", "ridge25", "ridge50", "tree25", "tree50"]


def make_model(name):
    """Construct the Ridge or histogram-tree estimator fixed by the experiment protocol."""
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


def float32_fma(left, right, total):
    """Round a float32 product and sum once, correcting float64 midpoint ties."""
    product = left.astype(np.float64) * np.float64(right)
    addend = total.astype(np.float64)
    summed = product + addend
    with np.errstate(over="ignore"):
        rounded = summed.astype(np.float32)
        direction = np.where(summed > rounded.astype(np.float64), np.inf, -np.inf).astype(
            np.float32
        )
        adjacent = np.nextafter(rounded, direction)
    midpoint = (rounded.astype(np.float64) + adjacent.astype(np.float64)) * 0.5
    ties = np.isfinite(rounded) & (summed == midpoint)
    if ties.any():
        # Recover the addition residual before choosing a side of the float32 midpoint.
        virtual = summed[ties] - product[ties]
        error = (product[ties] - (summed[ties] - virtual)) + (addend[ties] - virtual)
        move = ((error > 0) & (adjacent[ties] > rounded[ties])) | (
            (error < 0) & (adjacent[ties] < rounded[ties])
        )
        rounded[ties] = np.where(move, adjacent[ties], rounded[ties])
    return rounded


def predict_model(model, matrix):
    """Preserve the frozen float32 Ridge reduction independently of BLAS dispatch."""
    estimator = model.steps[-1][1]
    if not isinstance(estimator, Ridge):
        return model.predict(matrix)
    transformed = model[:-1].transform(matrix)
    coefficients = estimator.coef_
    if (
        transformed.dtype != np.float32
        or coefficients.dtype != np.float32
        or coefficients.ndim != 1
    ):
        return model.predict(matrix)
    if transformed.shape[1] != len(coefficients):
        raise ValueError("Ridge feature and coefficient counts differ")
    lanes = np.zeros((len(transformed), 8), dtype=np.float32)
    for column, coefficient in enumerate(coefficients):
        lane = column % 8
        lanes[:, lane] = float32_fma(transformed[:, column], coefficient, lanes[:, lane])
    a, b, c, d, e, f, g, h = lanes.T
    return (((a + b) + (e + f)) + ((c + d) + (g + h))) + estimator.intercept_


def industry_residuals(sub):
    """Subtract leave-own-stock-out industry returns from already-purged labels."""
    industry_returns = sub.groupby(["date_index", "industry"], sort=False).y5
    group_size = industry_returns.transform("count").to_numpy()
    peer_mean = (industry_returns.transform("sum").to_numpy() - sub.y5.to_numpy()) / np.maximum(
        1, group_size - 1
    )
    return sub.y5.to_numpy() - peer_mean, group_size >= 3


def training_targets(table, rows):
    """Build weighted daily residual ranks inside the eligible momentum candidate pool."""
    labels = table.iloc[rows]
    residuals, supported = industry_residuals(labels)
    pool = supported & (labels.momentum_percentile.to_numpy() >= 0.70)
    candidates = labels.iloc[np.flatnonzero(pool)].copy()
    candidates["residual"] = np.round(residuals[pool], 12)
    target = candidates.groupby("date_index", sort=False).residual.rank(pct=True).to_numpy() - 0.5
    daily_count = candidates.groupby("date_index", sort=False).symbol.transform("size").to_numpy()
    industry_count = (
        candidates.groupby(["date_index", "industry"], sort=False)
        .symbol.transform("size")
        .to_numpy()
    )
    sector_count = (
        candidates.groupby("date_index", sort=False).industry.transform("nunique").to_numpy()
    )
    weights = (0.5 / daily_count + 0.5 / (industry_count * sector_count)) * np.exp2(
        -(candidates.date_index.max() - candidates.date_index.to_numpy()) / 126
    )
    weights /= weights.mean()
    return (
        rows[pool],
        target,
        weights,
        {
            "supported_training_fraction": float(supported.mean()),
            "pool_samples": int(pool.sum()),
            "target": "Daily rank of leave-one-out industry residual legal5-session return",
            "minimum_label_peers": 3,
            "labels_used_for_industry_reference": len(rows),
        },
    )


@pd.option_context("compute.use_numexpr", False)
def fit_stage(bundle, train_rows, pred_rows, path, identity):
    """Fit two fixed estimators or verify and reuse identical cached training artifacts."""
    path.mkdir(parents=True, exist_ok=True)
    table = bundle["table"]
    fit_rows, target, weights, target_details = training_targets(table, train_rows)
    matrix = table[bundle["features"]].to_numpy(dtype=np.float32)
    cache_identity = {
        **identity,
        "train_rows_sha256": array_sha(train_rows),
        "fit_rows_sha256": array_sha(fit_rows),
        "prediction_rows_sha256": array_sha(pred_rows),
        "model_code_sha256": sha(__file__),
    }
    predictions = {}
    for name in ["ridge", "tree"]:
        folder = path / name
        folder.mkdir(exist_ok=True)
        key = digest({**cache_identity, "family": name})
        metadata_path = folder / "fit.json"
        if metadata_path.exists():
            metadata = read(metadata_path)
            if (
                metadata["identity"] != key
                or sha(folder / "model.joblib") != metadata["model_sha256"]
            ):
                raise ValueError(f"Cached model identity or hash mismatch: {folder}")
            if sha(folder / "predictions.npy") != metadata["predictions_sha256"]:
                raise ValueError(f"Cached prediction hash mismatch: {folder}")
            predictions[name] = np.load(folder / "predictions.npy")
            continue
        log("Fitting industry-residual model", stage=str(path), family=name, rows=len(fit_rows))
        model = make_model(name)
        estimator_step = list(model.named_steps)[-1]
        with threadpool_limits(limits=2):
            model.fit(matrix[fit_rows], target, **{estimator_step + "__sample_weight": weights})
            predicted = predict_model(model, matrix[pred_rows])
        if not np.isfinite(predicted).all():
            raise ValueError(f"Model produced non-finite predictions: {name}")
        joblib.dump(model, folder / "model.joblib", compress=3)
        np.save(folder / "predictions.npy", predicted)
        save(
            metadata_path,
            {
                "identity": key,
                "completed_at": now(),
                "train_samples": len(fit_rows),
                "prediction_samples": len(pred_rows),
                "target_details": target_details,
                "latest_training_signal": int(table.iloc[train_rows].date_index.max()),
                "latest_training_label_end": int(table.iloc[train_rows].end5.max()),
                "target_bounds": [float(target.min()), float(target.max())],
                "parameters": model.get_params()[estimator_step].get_params(),
                "model_sha256": sha(folder / "model.joblib"),
                "predictions_sha256": sha(folder / "predictions.npy"),
            },
            immutable=True,
        )
        predictions[name] = predicted
    return predictions


def neutral_scores(table, rows, pred):
    """Remove same-date industry prediction means and shrink small industry groups."""
    frame = table.iloc[rows][["date_index", "industry"]].copy()
    frame["pred"] = np.asarray(pred)
    industry_groups = frame.groupby(["date_index", "industry"], sort=False).pred
    group_size = industry_groups.transform("count").to_numpy()
    neutral = (
        (frame.pred.to_numpy() - industry_groups.transform("mean").to_numpy())
        * group_size
        / (group_size + 5)
    )
    return neutral, group_size >= 3


def candidate_scores(table, rows, predictions):
    """Blend industry-neutral model ranks with momentum only within supported candidates."""
    momentum_rank = rank_by_date(table, rows, table.iloc[rows].momentum_score.to_numpy())
    scores = {"momentum": momentum_rank}
    for family in ["ridge", "tree"]:
        neutral, supported = neutral_scores(table, rows, predictions[family])
        pool = (momentum_rank >= 0.70) & supported
        model_rank = 0.70 + 0.30 * rank_by_date(table, rows[pool], neutral[pool])
        for weight in [0.25, 0.50]:
            blended = momentum_rank.copy()
            blended[pool] = (1 - weight) * momentum_rank[pool] + weight * model_rank
            scores[family + str(round(weight * 100))] = rank_by_date(table, rows, blended)
    return scores


class Forecast(TypedDict):
    """Signal-close rank and calibrated return/margin, all expressed as fractions."""

    rank: float
    mu: float
    margin: float


def _group_forecasts(
    dates: Iterable[int],
    symbols: Iterable[str],
    ranks: Iterable[float],
    means: Iterable[float],
    margins: Iterable[float],
) -> dict[int, dict[str, Forecast]]:
    forecasts: dict[int, dict[str, Forecast]] = {}
    for date_index, symbol, rank, mean, margin in zip(dates, symbols, ranks, means, margins):
        forecasts.setdefault(int(date_index), {})[str(symbol)] = {
            "rank": float(rank),
            "mu": float(mean),
            "margin": float(margin),
        }
    return forecasts


def forecasts_by_date(table, rows, rank, original_rank, cal) -> dict[int, dict[str, Forecast]]:
    """Calibrate ranks and group forecasts by signal date."""
    mean, margin = map_calibration(original_rank, cal)
    return _group_forecasts(
        table.iloc[rows].date_index, table.iloc[rows].symbol, rank, mean, margin
    )


def factor_check(table, rows, scores, available_before):
    """Form cohorts before filtering labels to avoid selecting on future availability."""
    signals = table.iloc[rows].copy()
    pool = signals.momentum_percentile.to_numpy() >= 0.70
    buckets = np.zeros(len(signals), int)
    ranks = rank_by_date(table, rows[pool], np.asarray(scores)[pool])
    buckets[pool] = np.minimum(4, np.maximum(0, np.ceil(ranks * 5).astype(int) - 1)) + 1
    available_labels = (
        (signals.status5.to_numpy() == 0)
        & np.isfinite(signals.y5.to_numpy())
        & (signals.end5.to_numpy() < available_before)
    )
    valid = signals.iloc[np.flatnonzero(available_labels)].copy()
    relative, supported = industry_residuals(valid)
    valid["residual"] = relative
    valid["supported"] = supported
    valid["score"] = np.asarray(scores)[available_labels]
    valid["bucket"] = buckets[available_labels]
    selected = valid[(valid.bucket > 0) & valid.supported]
    daily = []
    for date_index, daily_scores in selected.groupby("date_index", sort=True):
        if len(daily_scores) < 10 or daily_scores.score.nunique() < 2:
            continue
        raw_ic = float(daily_scores[["score", "y5"]].corr(method="spearman").iloc[0, 1])
        industry_ic = float(daily_scores[["score", "residual"]].corr(method="spearman").iloc[0, 1])
        bucket_returns = daily_scores.groupby("bucket").residual.mean()
        if 1 not in bucket_returns or 5 not in bucket_returns:
            continue
        daily.append(
            {
                "date_index": int(date_index),
                "raw_ic": raw_ic,
                "industry_ic": industry_ic,
                "q5_minus_q1": float(bucket_returns[5] - bucket_returns[1]),
                "top_gross": float(daily_scores.loc[daily_scores.bucket == 5, "y5"].mean()),
                "rows": len(daily_scores),
            }
        )
    origin = int(signals.date_index.min())
    blocks: dict[int, list[float]] = {}
    for day in daily:
        blocks.setdefault((cast(int, day["date_index"]) - origin) // 10, []).append(
            day["q5_minus_q1"]
        )
    block_means = [
        float(np.mean(spreads)) for block, spreads in sorted(blocks.items()) if block < 6
    ]
    eligible_labels = int((pool & available_labels).sum())
    coverage = len(selected) / max(1, eligible_labels)
    result: dict = {
        "dates": len(daily),
        "coverage": coverage,
        "mean_raw_ic": float(np.mean([day["raw_ic"] for day in daily])) if daily else 0.0,
        "mean_industry_ic": float(np.mean([day["industry_ic"] for day in daily])) if daily else 0.0,
        "mean_industry_top_bottom": float(np.mean([day["q5_minus_q1"] for day in daily]))
        if daily
        else 0.0,
        "positive_ten_signal_day_blocks": sum(spread > 0 for spread in block_means),
        "block_spreads": block_means,
        "last_label_end": int(valid.end5.max()) if len(valid) else None,
        "available_before": int(available_before),
        "daily": daily,
        "gross_diagnostic_not_portfolio": True,
    }
    result["requirements"] = {
        "at_least_40_dates": len(daily) >= 40,
        "coverage_at_least_80pct": coverage >= 0.8,
        "industry_ic_above_001": result["mean_industry_ic"] > 0.01,
        "top_group_advantage_positive": result["mean_industry_top_bottom"] > 0,
        "at_least_four_positive_blocks": result["positive_ten_signal_day_blocks"] >= 4,
    }
    result["pass"] = all(result["requirements"].values())
    return result


def industry_holdout_check(table, rows, scores, available_before):
    """Remove the five largest signal-date industries without using labels to choose them."""
    signals = table.iloc[rows]
    pool = signals.momentum_percentile >= 0.70
    counts = signals.loc[pool, "industry"].value_counts()
    industries = sorted(counts.index, key=lambda name: (-int(counts[name]), str(name)))
    results = []
    if len(industries) >= 6:
        for industry in industries[:5]:
            retained = (signals.industry != industry).to_numpy()
            result = factor_check(
                table, rows[retained], np.asarray(scores)[retained], available_before
            )
            industry_ic = result["mean_industry_ic"]
            spread = result["mean_industry_top_bottom"]
            requirements = {
                "at_least_40_dates": result["dates"] >= 40,
                "coverage_at_least_80pct": result["coverage"] >= 0.8,
                "positive_industry_ic": bool(np.isfinite(industry_ic) and industry_ic > 0),
                "positive_residual_spread": bool(np.isfinite(spread) and spread > 0),
            }
            results.append(
                {
                    "excluded_industry": str(industry),
                    "signal_pool_rows": int(counts[industry]),
                    "dates": result["dates"],
                    "coverage": result["coverage"],
                    "mean_industry_ic": float(industry_ic) if np.isfinite(industry_ic) else None,
                    "mean_industry_top_bottom": float(spread) if np.isfinite(spread) else None,
                    "last_label_end": result["last_label_end"],
                    "requirements": requirements,
                    "pass": all(requirements.values()),
                }
            )
    return {
        "pass": len(results) == 5 and all(result["pass"] for result in results),
        "represented_industries": len(industries),
        "available_before": int(available_before),
        "industry_selection_uses_labels": False,
        "holdouts": results,
        "historical_industry_point_in_time_certified": False,
    }


def prediction_map(table, arrays, name) -> dict[int, dict[str, Forecast]]:
    """Group stored outer predictions by signal date."""
    signals = table.iloc[arrays["row_indices"]]
    return _group_forecasts(
        signals.date_index,
        signals.symbol,
        arrays[name + "_rank"],
        arrays["common_mu"],
        arrays["common_margin"],
    )
