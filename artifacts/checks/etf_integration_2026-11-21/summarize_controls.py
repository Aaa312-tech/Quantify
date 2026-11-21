"""Summarize the preregistered three-arm comparison without selecting by profit."""

from pathlib import Path

import numpy as np

from quantfolio.allocation import costs
from quantfolio.dataset import load_bundle
from quantfolio.feasibility import fit_parity
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
pfile = root / "configs/protocols/etf_integration.json"
p = read(pfile)
checks = root / p["checks_output"]
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
parity = read(checks / "implementation/validation.json")
snapshot = read(checks / "snapshot_review.json")
exposure = read(checks / "reported/exposure_review.json")
assert parity["status"] == snapshot["status"] == exposure["status"] == "PASS"
assert parity["source_sha256"] == snapshot["source_sha256"] == exposure["source_sha256"] == sources
original_sources = sources
summary = {"baseline": read(root / p["baseline_evaluation"] / "evaluation_summary.json")}
arms, comparison, evidence = {}, {}, {}
previous = "baseline"
for variant, setting in p["variants"].items():
    paths = Paths(root, setting["configuration"])
    folder, run = root / setting["checks_output"], root / setting["training_output"]
    validation = read(folder / "training_validation.json")
    training = read(folder / "training_review.json")
    accounts = read(folder / "account_review.json")
    assert validation["status"] == training["status"] == accounts["status"] == "PASS"
    assert sources == validation["source_sha256"] == accounts["source_sha256"]
    assert original_sources == training["source_sha256"] == validation["source_sha256"]
    assert (
        validation["models_reloaded"] == 48
        and validation["account_audits"] == 1425
        and validation["paper"]["accounts"] == 75
    )
    assert training["warning_fit_occurrences"] == training["warning_reference_occurrences"] == 0
    summary[variant] = read(root / setting["evaluation_output"] / "evaluation_summary.json")
    bridge = read(root / setting["fixed_forecast_output"] / "summary.json")
    assert original_sources == bridge["source_sha256"] and bridge["accounts"] == 1425
    assert summary[variant]["trade_start"] == summary["baseline"]["trade_start"]
    assert summary[variant]["trade_end"] == summary["baseline"]["trade_end"]
    old_run = root / (
        p["baseline_run"] if previous == "baseline" else p["variants"][previous]["training_output"]
    )
    fitted = fit_parity(old_run, run)
    if variant == "coverage":
        assert fitted["status"] == "PASS"
        assert all(row["before"] == row["after"] for row in fitted["choices"])
        assert all(item["returns_changed"] == 0 for item in accounts["comparisons"].values())
    if variant == "reported":
        assert all(row["model_equal"] for row in fitted["models"])
    fitted["all_fits_identical"] = fitted["status"] == "PASS"
    fitted["status"] = "RECORDED"
    fitted["scope"] = (
        "Changed data can change fits, forecasts and choices; byte differences are attribution, not a failed integration check."
    )
    save(folder / "fit_comparison.json", fitted, immutable=True)
    bundle = load_bundle(paths)
    result = read(run / "final_fit/result.json")
    with np.load(run / "final_fit/outer_predictions.npz") as forecast:
        rows, mu, margin = forecast["row_indices"], forecast["common_mu"], forecast["common_margin"]
    symbols = bundle["table"].iloc[rows].symbol.tolist()
    assert len(set(symbols)) == len(mu) == len(margin)
    signal = {
        "status": "RECORDED",
        "signal_date": p["source_data_cutoff"],
        "training_identity": validation["identity"],
        "model": result["selected_candidate"],
        "rules": result["selected_rules"],
        "forecast_sha256": sha(run / "final_fit/outer_predictions.npz"),
        "bundle_sha256": sha(paths.bundle),
        "forecast_rows": len(rows),
        "horizon_sessions": 5,
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
    save(folder / "latest_signal_review.json", signal, immutable=True)
    rows = {}
    for cost in ("10bps", "20bps", "30bps"):
        original, fixed, after = [
            item["continuous"][cost]["selected_strategy"]["summary"]
            for item in (summary[previous], bridge, summary[variant])
        ]
        rows[cost] = {
            "before": original,
            "fixed_forecasts": fixed,
            "after": after,
            "matched_momentum": summary[variant]["continuous"][cost]["matched_momentum"]["summary"],
            "data_execution_percentage_points": (fixed["return"] - original["return"]) * 100,
            "retraining_selection_percentage_points": (after["return"] - fixed["return"]) * 100,
            "excess_return": after["return"]
            - summary[variant]["continuous"][cost]["matched_momentum"]["summary"]["return"],
            "ten_day": {
                name: summary[variant]["results"][cost][name]["aggregate"]
                for name in ("selected_strategy", "matched_momentum")
            },
        }
    arms[variant] = {
        "role": setting["role"],
        "previous_arm": previous,
        "identity": validation["identity"],
        "models_changed": sum(not row["model_equal"] for row in fitted["models"]),
        "forecast_archives_changed": sum(not row["arrays_equal"] for row in fitted["forecasts"]),
        "choices_changed": sum(row["before"] != row["after"] for row in fitted["choices"]),
        "selections": training["selections"],
        "qualified_candidate_windows": training["qualified_candidate_windows"],
        "account_comparisons": accounts["comparisons"],
        "inner_quality_occurrences": training["quality_occurrences"],
        "inner_unresolved_occurrences": training["unresolved_occurrences"],
        "outer": {
            name: {
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
            for name, item in accounts["arms"].items()
        },
        "latest_signal": signal,
        "comparison": rows,
    }
    for filename in (
        "training_validation.json",
        "training_review.json",
        "account_review.json",
        "fit_comparison.json",
        "latest_signal_review.json",
    ):
        evidence[(folder / filename).relative_to(root).as_posix()] = sha(folder / filename)
    previous = variant
for name, result in summary.items():
    comparison[name] = {
        cost: {"continuous": result["continuous"][cost], "ten_day": result["results"][cost]}
        for cost in ("10bps", "20bps", "30bps")
    }
save(
    checks / "attribution.json",
    {
        "status": "COMPLETE",
        "completed_at": now(),
        "source_sha256": sources,
        "protocol_sha256": sha(pfile),
        "registration_commit": "f6b07fc8c590f131d59c8681c8a5d5e8206ad06e",
        "period": {
            "start": summary["baseline"]["trade_start"],
            "end": summary["baseline"]["trade_end"],
        },
        "arms": arms,
        "all_policy_metrics": comparison,
        "evidence_sha256": evidence,
        "fits": 144,
        "models_reloaded": 144,
        "retrained_accounts": 4275,
        "fixed_forecast_accounts": 4275,
        "paper_accounts": 225,
        "superiority_assessment": "NOT_ESTABLISHED",
        "forward_observations_added": 0,
        "activated": False,
        "complete_economic_exposure_certified": False,
        "economic_exposure_upper_bound": None,
        "exposure_review_sha256": sha(checks / "reported/exposure_review.json"),
        "difference_scope": "Full curve differences also include newly disclosed exposure metadata. Net-return changes and trade differences are reported separately; additional metadata alone is not an economic change.",
        "limitations": [
            *p["limitations"],
            "Coverage and identity are attribution controls, not alternative return-selected strategies.",
            "Reported proxy caps do not certify full current economic exposure. Identity grouping and quantitative holdings overlap are different concepts.",
            "Successful source checks do not clear 720 unresolved distribution reviews or certify all terms of field-scoped verified events.",
            "Changing costs changes orders and risk paths; nonmonotonic cost outcomes do not guarantee higher returns.",
        ],
    },
    immutable=True,
)
for variant, arm in arms.items():
    print(
        variant,
        {
            cost: {
                key: value[key]
                for key in (
                    "data_execution_percentage_points",
                    "retraining_selection_percentage_points",
                    "excess_return",
                )
            }
            for cost, value in arm["comparison"].items()
        },
        flush=True,
    )
print("ALL REGISTERED ATTRIBUTION COMPLETE", flush=True)
