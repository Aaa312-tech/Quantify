"""Industry-residual targets, causal prediction blends and factor diagnostics."""

from __future__ import annotations

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from .calibration import map_calibration, rank_by_date
from .estimators import make_model
from .storage import array_sha, digest, log, now, read, save, sha

CANDIDATES = ["momentum", "ridge25", "ridge50", "tree25", "tree50"]


def industry_residuals(sub):
    """Subtract leave-own-stock-out industry returns from already-purged labels."""
    group = sub.groupby(["date_index", "industry"], sort=False).y5
    count = group.transform("count").to_numpy()
    # Leave-own-stock-out industry return: all input labels have already been purged.
    other = (group.transform("sum").to_numpy() - sub.y5.to_numpy()) / np.maximum(1, count - 1)
    return sub.y5.to_numpy() - other, count >= 3


def training_targets(table, rows):
    """Build weighted daily residual ranks inside the eligible momentum candidate pool."""
    sub = table.iloc[rows]
    relative, supported = industry_residuals(sub)
    pool = supported & (sub.momentum_percentile.to_numpy() >= 0.70)
    s = sub.iloc[np.flatnonzero(pool)].copy()
    s["residual"] = np.round(relative[pool], 12)
    target = s.groupby("date_index", sort=False).residual.rank(pct=True).to_numpy() - 0.5
    days = s.groupby("date_index", sort=False).symbol.transform("size").to_numpy()
    groups = s.groupby(["date_index", "industry"], sort=False).symbol.transform("size").to_numpy()
    sectors = s.groupby("date_index", sort=False).industry.transform("nunique").to_numpy()
    weights = (0.5 / days + 0.5 / (groups * sectors)) * np.exp2(
        -(s.date_index.max() - s.date_index.to_numpy()) / 126
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


def fit_stage(bundle, train_rows, pred_rows, path, identity):
    """Fit two fixed estimators or verify and reuse identical cached training artifacts."""
    path.mkdir(parents=True, exist_ok=True)
    t = bundle["table"]
    rows, target, weights, info = training_targets(t, train_rows)
    X = t[bundle["features"]].to_numpy(dtype=np.float32)
    ident = {
        **identity,
        "train_rows_sha256": array_sha(train_rows),
        "fit_rows_sha256": array_sha(rows),
        "prediction_rows_sha256": array_sha(pred_rows),
        "model_code_sha256": sha(__file__),
    }
    out = {}
    for name in ["ridge", "tree"]:
        folder = path / name
        folder.mkdir(exist_ok=True)
        key = digest({**ident, "family": name})
        p = folder / "fit.json"
        if p.exists():
            m = read(p)
            assert m["identity"] == key and sha(folder / "model.joblib") == m["model_sha256"]
            assert sha(folder / "predictions.npy") == m["predictions_sha256"]
            out[name] = np.load(folder / "predictions.npy")
            continue
        log("Fitting industry-residual model", stage=str(path), family=name, rows=len(rows))
        model = make_model(name)
        last = list(model.named_steps)[-1]
        with threadpool_limits(limits=2):
            model.fit(X[rows], target, **{last + "__sample_weight": weights})
            pred = model.predict(X[pred_rows])
        assert np.isfinite(pred).all()
        joblib.dump(model, folder / "model.joblib", compress=3)
        np.save(folder / "predictions.npy", pred)
        save(
            p,
            {
                "identity": key,
                "completed_at": now(),
                "train_samples": len(rows),
                "prediction_samples": len(pred_rows),
                "target_details": info,
                "latest_training_signal": int(t.iloc[train_rows].date_index.max()),
                "latest_training_label_end": int(t.iloc[train_rows].end5.max()),
                "target_bounds": [float(target.min()), float(target.max())],
                "parameters": model.get_params()[last].get_params(),
                "model_sha256": sha(folder / "model.joblib"),
                "predictions_sha256": sha(folder / "predictions.npy"),
            },
            immutable=True,
        )
        out[name] = pred
    return out


def neutral_scores(table, rows, pred):
    """Remove same-date industry prediction means and shrink small industry groups."""
    s = table.iloc[rows][["date_index", "industry"]].copy()
    s["pred"] = np.asarray(pred)
    g = s.groupby(["date_index", "industry"], sort=False).pred
    n = g.transform("count").to_numpy()
    neutral = (s.pred.to_numpy() - g.transform("mean").to_numpy()) * n / (n + 5)
    return neutral, n >= 3


def candidate_scores(table, rows, predictions):
    """Blend industry-neutral model ranks with momentum only within supported candidates."""
    trend = rank_by_date(table, rows, table.iloc[rows].momentum_score.to_numpy())
    out = {"momentum": trend}
    for family in ["ridge", "tree"]:
        neutral, supported = neutral_scores(table, rows, predictions[family])
        pool = (trend >= 0.70) & supported
        r = 0.70 + 0.30 * rank_by_date(table, rows[pool], neutral[pool])
        for weight in [0.25, 0.50]:
            v = trend.copy()
            v[pool] = (1 - weight) * trend[pool] + weight * r
            out[family + str(round(weight * 100))] = rank_by_date(table, rows, v)
    return out


def forecasts_by_date(table, rows, rank, original_rank, cal):
    """Build a signal-date map with ranking, calibrated returns and uncertainty margins."""
    mean, margin = map_calibration(original_rank, cal)
    out = {}
    for d, s, r, m, e in zip(
        table.iloc[rows].date_index, table.iloc[rows].symbol, rank, mean, margin
    ):
        out.setdefault(int(d), {})[str(s)] = {"rank": float(r), "mu": float(m), "margin": float(e)}
    return out


def factor_check(table, rows, scores, available_before):
    """Form cohorts BEFORE checking label availability. Only available labels enter metrics."""
    sub = table.iloc[rows].copy()
    pool = sub.momentum_percentile.to_numpy() >= 0.70
    buckets = np.zeros(len(sub), int)
    ranks = rank_by_date(table, rows[pool], np.asarray(scores)[pool])
    buckets[pool] = np.minimum(4, np.maximum(0, np.ceil(ranks * 5).astype(int) - 1)) + 1
    good = (
        (sub.status5.to_numpy() == 0)
        & np.isfinite(sub.y5.to_numpy())
        & (sub.end5.to_numpy() < available_before)
    )
    valid = sub.iloc[np.flatnonzero(good)].copy()
    relative, supported = industry_residuals(valid)
    valid["residual"] = relative
    valid["supported"] = supported
    valid["score"] = np.asarray(scores)[good]
    valid["bucket"] = buckets[good]
    selected = valid[(valid.bucket > 0) & valid.supported]
    daily = []
    for d, g in selected.groupby("date_index", sort=True):
        if len(g) < 10 or g.score.nunique() < 2:
            continue
        raw = float(g[["score", "y5"]].corr(method="spearman").iloc[0, 1])
        ic = float(g[["score", "residual"]].corr(method="spearman").iloc[0, 1])
        q = g.groupby("bucket").residual.mean()
        if 1 not in q or 5 not in q:
            continue
        daily.append(
            {
                "date_index": int(d),
                "raw_ic": raw,
                "industry_ic": ic,
                "q5_minus_q1": float(q[5] - q[1]),
                "top_gross": float(g.loc[g.bucket == 5, "y5"].mean()),
                "rows": len(g),
            }
        )
    origin = int(sub.date_index.min())
    blocks = {}
    for d in daily:
        blocks.setdefault((d["date_index"] - origin) // 10, []).append(d["q5_minus_q1"])
    blockmeans = [float(np.mean(v)) for k, v in sorted(blocks.items()) if k < 6]
    denom = int((pool & good).sum())
    coverage = len(selected) / max(1, denom)
    result = {
        "dates": len(daily),
        "coverage": coverage,
        "mean_raw_ic": float(np.mean([d["raw_ic"] for d in daily])) if daily else 0.0,
        "mean_industry_ic": float(np.mean([d["industry_ic"] for d in daily])) if daily else 0.0,
        "mean_industry_top_bottom": float(np.mean([d["q5_minus_q1"] for d in daily]))
        if daily
        else 0.0,
        "positive_ten_signal_day_blocks": sum(x > 0 for x in blockmeans),
        "block_spreads": blockmeans,
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
