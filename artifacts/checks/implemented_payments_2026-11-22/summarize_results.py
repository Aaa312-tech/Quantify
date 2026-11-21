"""Attribute return and quality changes without selecting the repair by performance."""

from pathlib import Path

import numpy as np

from quantfolio.allocation import costs
from quantfolio.dataset import load_bundle, load_snapshot
from quantfolio.feasibility import fit_parity
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
pfile = root / "configs/protocols/implemented_payments.json"
p = read(pfile)
paths = Paths(root, p["configuration"])
checks, run = root / p["checks_output"], root / p["training_output"]
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
evidence = {}
for name in (
    "snapshot_review.json",
    "compatibility/validation.json",
    "training_validation.json",
    "training_review.json",
    "account_review.json",
    "exposure_review.json",
    "settlement_review.json",
):
    item = read(checks / name)
    assert item["status"] == "PASS" and item["source_sha256"] == sources
    evidence[name] = sha(checks / name)
training = read(checks / "training_review.json")
accounts = read(checks / "account_review.json")
summary = {
    "before": read(root / p["baseline_evaluation"] / "evaluation_summary.json"),
    "fixed": read(root / p["fixed_forecast_output"] / "summary.json"),
    "after": read(root / p["evaluation_output"] / "evaluation_summary.json"),
}
assert all(value["accounts"] == 1425 for value in summary.values())
assert summary["before"]["trade_start"] == summary["after"]["trade_start"]
assert summary["before"]["trade_end"] == summary["after"]["trade_end"]
fitted = fit_parity(root / p["baseline_run"], run)
assert len(fitted["models"]) == 48 and len(fitted["forecasts"]) == 12
assert all(row["model_equal"] and row["predictions_equal"] for row in fitted["models"])
for record in fitted["forecasts"]:
    before_file = root / p["baseline_run"] / record["path"]
    after_file = run / record["path"]
    with np.load(before_file) as before, np.load(after_file) as after:
        np.testing.assert_array_equal(before["row_indices"], after["row_indices"])
        record["changed_arrays"] = [
            name
            for name in before.files
            if not np.array_equal(before[name], after[name], equal_nan=True)
        ]
        record["calibration_max_abs_change"] = {
            name: float(np.max(np.abs(after[name] - before[name]), initial=0))
            for name in ("common_mu", "common_margin")
        }
    record["before_sha256"] = sha(before_file)
    record["after_sha256"] = sha(after_file)
fitted["all_forecasts_identical"] = all(row["arrays_equal"] for row in fitted["forecasts"])
fitted["status"] = "RECORDED"
fitted["scope"] = (
    "Payment-only integration preserves fitted estimators and their predictions exactly. "
    "Forecast calibration and selected rules are compared separately because cash settlement "
    "can affect inner-account gates. Old-input compatibility is a separate exact gate."
)
save(checks / "fit_comparison.json", fitted, immutable=True)
bundle = load_bundle(paths)
old = load_snapshot(paths, root / p["source_bundle"], p["source_data_cutoff"])
remaining = {}
for arm, data in (("before", old), ("after", bundle)):
    missing_pay = [
        a for a in data["actions"] if a["cash_per_share"] > 0 and not a.get("cash_pay_date")
    ]
    missing_list = [
        a for a in data["actions"] if a["share_multiplier"] != 1 and not a.get("share_list_date")
    ]
    remaining[arm] = {
        "payment_dates": len(missing_pay),
        "share_listing_dates": len(missing_list),
        "events_missing_either": len(
            {(a["symbol"], a["ex_date"]) for a in missing_pay + missing_list}
        ),
    }
result = read(run / "final_fit/result.json")
with np.load(run / "final_fit/outer_predictions.npz") as forecast:
    rows, mu, margin = forecast["row_indices"], forecast["common_mu"], forecast["common_margin"]
symbols = bundle["table"].iloc[rows].symbol.tolist()
assert len(set(symbols)) == len(mu) == len(margin)
signal = {
    "status": "RECORDED",
    "signal_date": p["source_data_cutoff"],
    "training_identity": result["identity"],
    "model": result["selected_candidate"],
    "rules": result["selected_rules"],
    "forecast_rows": len(rows),
    "horizon_sessions": 5,
    "forecast_sha256": sha(run / "final_fit/outer_predictions.npz"),
    "bundle_sha256": sha(paths.bundle),
    "cost_scenarios": {},
}
for cost in (10, 20, 30):
    rates = np.array([sum(costs(bundle["meta"], symbol, cost / 10000)) for symbol in symbols])
    net = mu - margin - rates
    signal["cost_scenarios"][str(cost) + "bps"] = {
        "passes_new_buy_edge_filter": int((net > 0).sum()),
        "minimum_edge": float(net.min()),
        "median_edge": float(np.median(net)),
        "maximum_edge": float(net.max()),
    }
save(checks / "latest_signal_review.json", signal, immutable=True)
comparison = {}
for cost in ("10bps", "20bps", "30bps"):
    before, fixed, after = [
        summary[name]["continuous"][cost]["selected_strategy"]["summary"]
        for name in ("before", "fixed", "after")
    ]
    momentum = summary["after"]["continuous"][cost]["matched_momentum"]["summary"]
    comparison[cost] = {
        "before": before,
        "fixed_forecasts": fixed,
        "after": after,
        "matched_momentum": momentum,
        "data_execution_percentage_points": (fixed["return"] - before["return"]) * 100,
        "retraining_selection_percentage_points": (after["return"] - fixed["return"]) * 100,
        "excess_return": after["return"] - momentum["return"],
        "ten_day": {
            name: summary["after"]["results"][cost][name]["aggregate"]
            for name in ("selected_strategy", "matched_momentum")
        },
    }
for name in ("fit_comparison.json", "latest_signal_review.json"):
    evidence[name] = sha(checks / name)
save(
    checks / "attribution.json",
    {
        "status": "COMPLETE",
        "completed_at": now(),
        "protocol_sha256": sha(pfile),
        "source_sha256": sources,
        "data_sha256": sha(paths.bundle),
        "period": {"start": summary["after"]["trade_start"], "end": summary["after"]["trade_end"]},
        "fits": 48,
        "models_reloaded": 48,
        "retrained_accounts": 1425,
        "fixed_forecast_accounts": 1425,
        "paper_accounts": 75,
        "models_identical": sum(r["model_equal"] for r in fitted["models"]),
        "models_changed": sum(not r["model_equal"] for r in fitted["models"]),
        "forecast_archives_changed": sum(not r["arrays_equal"] for r in fitted["forecasts"]),
        "choices_changed": sum(r["before"] != r["after"] for r in fitted["choices"]),
        "selections": training["selections"],
        "qualified_candidate_windows": training["qualified_candidate_windows"],
        "account_comparisons": accounts["comparisons"],
        "comparison": comparison,
        "remaining_missing_dates": remaining,
        "source_events_unverified": 418,
        "inner_quality_occurrences": training["quality_occurrences"],
        "inner_unresolved_occurrences": training["unresolved_occurrences"],
        "outer": {
            arm: {
                key: value
                for key, value in item.items()
                if key
                in (
                    "accounts",
                    "quality_occurrences",
                    "unresolved_occurrences",
                    "held_reclassification_occurrences",
                    "buy_rejection_counts",
                )
            }
            for arm, item in accounts["arms"].items()
        },
        "latest_signal": signal,
        "evidence_sha256": evidence,
        "superiority_assessment": "NOT_ESTABLISHED",
        "forward_observations_added": 0,
        "activated": False,
        "complete_economic_exposure_certified": False,
        "economic_exposure_upper_bound": None,
        "limitations": p["limitations"],
    },
    immutable=True,
)
print(
    "ATTRIBUTION COMPLETE",
    {
        cost: {
            "net_return": row["after"]["return"],
            "data_change_pp": row["data_execution_percentage_points"],
            "refit_change_pp": row["retraining_selection_percentage_points"],
            "excess": row["excess_return"],
        }
        for cost, row in comparison.items()
    },
    flush=True,
)
