"""Evaluate trained forecasts and compare complete ledgers against frozen evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .dataset import check_integrity, eligible_rows, load_bundle, walk_forward_folds
from .metrics import episode_stats
from .models import CANDIDATES, prediction_map
from .rules import CONFIGS
from .settings import Paths
from .simulation import episodes, simulate
from .storage import digest, dump_gzip, log, now, read, save, sha
from .validation import execution_audit, reconcile

POLICIES = ["selected_strategy", "matched_momentum", "selected_legacy"] + [
    "fixed_" + name for name in CONFIGS
]


def load_predictions(
    bundle: dict, run: Path, folds: list | None = None, identity: str | None = None
) -> tuple:
    """Combine non-overlapping outer folds and their validation-selected rule schedule."""
    maps = {"selected": {}, "momentum": {}}
    schedule = {}
    results = []
    folds = walk_forward_folds(bundle["calendar"]) if folds is None else folds
    if not folds:
        raise ValueError("No completed outer folds to evaluate")
    for fold in folds:
        folder = run / "training" / f"fold_{fold['fold']:02d}"
        result = read(folder / "result.json")
        if (
            result["fold"] != fold
            or result["final_fit"]
            or result["outer_results_used_for_selection"]
        ):
            raise ValueError(f"Invalid outer-fold boundaries or selection provenance: {folder}")
        if identity is not None and result["identity"] != identity:
            raise ValueError(f"Fold belongs to another experiment: {folder}")
        if (
            result["selected_candidate"] not in CANDIDATES
            or result["selected_rules"] not in CONFIGS
        ):
            raise ValueError(f"Unknown selected model or rule: {folder}")
        if sha(folder / "outer_predictions.npz") != result["predictions_sha256"]:
            raise ValueError(f"Prediction hash mismatch: {folder}")
        with np.load(folder / "outer_predictions.npz") as stored:
            arrays = dict(stored)
        expected_rows = eligible_rows(bundle["table"], fold["test_start"], fold["test_end"])
        rows = arrays["row_indices"]
        if rows.dtype.kind not in "iu" or not np.array_equal(rows, expected_rows):
            raise ValueError(
                f"Prediction rows are duplicated, missing or outside this fold: {folder}"
            )
        for key in {
            "common_mu",
            "common_margin",
            "momentum_rank",
            result["selected_candidate"] + "_rank",
        }:
            if arrays[key].shape != rows.shape or not np.isfinite(arrays[key]).all():
                raise ValueError(f"Invalid prediction array {key}: {folder}")
        for name in maps:
            candidate = "momentum" if name == "momentum" else result["selected_candidate"]
            forecasts = prediction_map(bundle["table"], arrays, candidate)
            if maps[name].keys() & forecasts.keys():
                raise ValueError(f"Overlapping prediction dates: {folder}")
            maps[name].update(forecasts)
        schedule.update(
            {
                day: result["selected_rules"]
                for day in range(fold["test_start"], fold["test_end"] + 1)
            }
        )
        results.append(result)
    start, end = folds[0]["test_start"], folds[-1]["test_end"]
    expected = list(range(start, end + 1))
    if not sorted(maps["selected"]) == sorted(maps["momentum"]) == sorted(schedule) == expected:
        raise ValueError("Prediction coverage is incomplete or overlapping")
    return maps, schedule, results, start, end


def experiment_folds(paths: Paths, bundle: dict, run: Path) -> tuple[dict, list]:
    """Accept only completed, contiguous windows tied to this dataset and protocol."""
    protocol = read(run / "protocol.json")
    status = read(run / "training_status.json")
    specification = protocol["specification"]
    identity = digest(specification)
    if protocol["identity"] != identity or status["identity"] != identity:
        raise ValueError("Training protocol identity mismatch")
    if specification["data_sha256"] != sha(paths.bundle) or specification["plan_sha256"] != sha(
        paths.protocol
    ):
        raise ValueError("Training used a different dataset or experiment protocol")
    available = walk_forward_folds(bundle["calendar"])
    if specification["folds"] != available:
        raise ValueError("Registered training windows differ from the dataset calendar")
    first, last = specification["selected_folds"]
    if not 1 <= first <= last <= len(available):
        raise ValueError("Invalid registered fold range")
    expected_status = "COMPLETE" if first == 1 and last == len(available) else "PARTIAL"
    if status["status"] != expected_status or status["folds_completed"] != last - first + 1:
        raise ValueError("Training has not completed its registered fold range")
    if str(status["data_cutoff"])[:10] != paths.config["data_cutoff"]:
        raise ValueError("Training cutoff differs from the active dataset")
    return status, available[first - 1 : last]


def evaluate(
    paths: Paths, run: Path, output: Path, costs: list[int], all_policies: bool = False
) -> dict:
    """Backtest a completed training run; partial windows retain their actual scope."""
    run = paths.artifact_path(run, "experiments")
    output = paths.output_path(output, "evaluations")
    if not costs or any(cost not in (10, 20, 30) for cost in costs):
        raise ValueError("Choose one or more slippage costs from 10, 20, 30 bps")
    check_integrity(paths)
    bundle = load_bundle(paths)
    status, folds = experiment_folds(paths, bundle, run)
    maps, schedule, choices, start, end = load_predictions(bundle, run, folds, status["identity"])
    policies = POLICIES if all_policies else ["selected_strategy", "matched_momentum"]
    results, continuous, artifacts = {}, {}, {}
    reconciliation_checks = execution_checks = accounts = 0
    for cost in sorted(set(costs)):
        key = f"{cost:02d}bps"
        results[key], continuous[key] = {}, {}
        for policy in policies:
            forecasts, rules = policy_inputs(policy, maps, schedule)
            episodes_result = episodes(
                bundle, start, end, forecasts, cost / 10000, True, rules=rules
            )
            summary, ledger = simulate(
                bundle, start + 1, end + 1, forecasts, cost / 10000, True, rules=rules
            )
            account = {"summary": summary, "ledger": ledger}
            for current in [*episodes_result, account]:
                check = reconcile(
                    current["ledger"], current["summary"], bundle["meta"], bundle["actions"]
                )
                audit = execution_audit(current["ledger"], bundle)
                if check["status"] != "PASS" or audit["status"] != "PASS":
                    raise ValueError(f"Account audit failed: {key}/{policy}: {check}, {audit}")
                reconciliation_checks += check["checks"]
                execution_checks += audit["checks"]
                accounts += 1
            for relative, content in [
                (f"evaluation/{key}/{policy}.ledgers.json.gz", episodes_result),
                (f"evaluation/continuous/{key}/{policy}.json.gz", account),
            ]:
                path = output / relative
                dump_gzip(path, content)
                artifacts[relative] = sha(path)
            results[key][policy] = {"aggregate": episode_stats(episodes_result)}
            continuous[key][policy] = {"summary": summary}
            log("Evaluated training run", cost=key, policy=policy, folds=len(folds))
    input_files = [run / "protocol.json", run / "training_status.json"]
    for fold in folds:
        input_files.extend(
            run / "training" / f"fold_{fold['fold']:02d}" / name
            for name in ("result.json", "outer_predictions.npz")
        )
    result = {
        "schema_version": 1,
        "status": status["status"],
        "completed_at": now(),
        "decision": "RESEARCH_ONLY",
        "superiority_assessment": "NOT_PERFORMED",
        "source_run": run.relative_to(paths.root).as_posix(),
        "identity": status["identity"],
        "data_cutoff": paths.config["data_cutoff"],
        "primary_policy": "selected_strategy",
        "trade_start": str(bundle["calendar"][start + 1].date()),
        "trade_end": str(bundle["calendar"][end + 1].date()),
        "outer_signal_sessions": end - start + 1,
        "folds": len(folds),
        "fold_choices": [
            {key: choice[key] for key in ("fold", "selected_candidate", "selected_rules")}
            for choice in choices
        ],
        "results": results,
        "continuous": continuous,
        "accounts": accounts,
        "reconciliation_checks": reconciliation_checks,
        "execution_checks": execution_checks,
        "input_sha256": {
            path.relative_to(paths.root).as_posix(): sha(path) for path in input_files
        },
        "artifacts": artifacts,
    }
    save(output / "evaluation_summary.json", result, immutable=True)
    return result


def policy_inputs(name: str, maps: dict, schedule: dict) -> tuple:
    """Use exactly the documented forecast and turnover-rule combinations."""
    if name.startswith("fixed_"):
        return maps["momentum"], name[6:]
    if name == "selected_strategy":
        return maps["selected"], schedule
    if name == "matched_momentum":
        return maps["momentum"], schedule
    if name == "selected_legacy":
        return maps["selected"], "baseline"
    raise ValueError(name)


def replay(paths: Paths, output: Path, costs: list[int], policies: list[str]) -> dict[str, Any]:
    """Re-run frozen accounts; fail if refactoring changes any recorded economics."""
    from .storage import load_gzip

    integrity = check_integrity(paths)
    reference_primary = read(paths.reference / "evaluation_summary.json")["primary_policy"]
    bundle = load_bundle(paths)
    maps, schedule, folds, start, end = load_predictions(bundle, paths.reference)
    results = {}
    accounts = 0
    reconciliation_checks = execution_checks = 0
    for cost in costs:
        key = f"{cost:02d}bps"
        results[key] = {}
        for policy in policies:
            reference_policy = reference_primary if policy == "selected_strategy" else policy
            forecasts, rules = policy_inputs(policy, maps, schedule)
            actual = episodes(bundle, start, end, forecasts, cost / 10000, True, rules=rules)
            expected = load_gzip(
                paths.reference / "evaluation" / key / (reference_policy + ".ledgers.json.gz")
            )
            if len(actual) != len(expected):
                raise AssertionError(f"Account count mismatch: {key}/{policy}")
            summary, continuous = simulate(
                bundle, start + 1, end + 1, forecasts, cost / 10000, True, rules=rules
            )
            expected_continuous = load_gzip(
                paths.reference / "evaluation/continuous" / key / (reference_policy + ".json.gz")
            )
            paired = list(zip(actual, expected)) + [
                ({"summary": summary, "ledger": continuous}, expected_continuous)
            ]
            for number, (new, old) in enumerate(paired):
                if new["summary"] != old["summary"]:
                    raise AssertionError(f"Summary mismatch: {key}/{policy}/{number}")
                if digest(new["ledger"]) != digest(old["ledger"]):
                    raise AssertionError(f"Ledger mismatch: {key}/{policy}/{number}")
                check = reconcile(new["ledger"], new["summary"], bundle["meta"], bundle["actions"])
                audit = execution_audit(new["ledger"], bundle)
                if check["status"] != "PASS" or audit["status"] != "PASS":
                    raise AssertionError({"reconciliation": check, "execution": audit})
                reconciliation_checks += check["checks"]
                execution_checks += audit["checks"]
                accounts += 1
            results[key][policy] = {
                "aggregate": episode_stats(actual),
                "continuous": summary,
                "accounts": len(paired),
                "full_ledger_exact": True,
            }
            log("Replay matched frozen ledgers", cost=key, policy=policy, accounts=len(paired))
    result = {
        "status": "PASS",
        "completed_at": now(),
        "reference_integrity": integrity,
        "costs_bps": costs,
        "policies": policies,
        "accounts": accounts,
        "reconciliation_checks": reconciliation_checks,
        "execution_checks": execution_checks,
        "folds": len(folds),
        "results": results,
        "scope": "Refactor parity, not new alpha evidence",
    }
    save(output / "verification.json", result, immutable=True)
    return result
