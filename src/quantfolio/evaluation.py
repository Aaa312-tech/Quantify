"""Evaluate trained forecasts and compare complete ledgers against frozen evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .dataset import check_integrity, eligible_rows, load_bundle, walk_forward_folds
from .market import EXECUTION_MODEL
from .metrics import episode_stats
from .models import CANDIDATES, prediction_map
from .rules import CONFIGS
from .settings import Paths
from .simulation import episodes, simulate
from .storage import digest, dump_gzip, load_gzip, log, now, read, save, sha
from .validation import execution_audit, reconcile

POLICIES = ["selected_strategy", "matched_momentum", "selected_baseline_rules"] + [
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
    if specification.get("execution_model") != EXECUTION_MODEL:
        raise ValueError("Training used another execution model; retrain before evaluating")
    if specification.get("selection_mode", "legacy") != paths.config.get(
        "selection_mode", "legacy"
    ):
        raise ValueError("Training used another model adoption policy")
    if specification.get("turnover_policy", "legacy") != paths.config.get(
        "turnover_policy", "legacy"
    ):
        raise ValueError("Training used another turnover policy")
    for name in (
        "simulation",
        "ledger",
        "market",
        "allocation",
        "risk",
        "rules",
        "metrics",
        "validation",
        "industries",
        "funds",
    ):
        filename = name + ".py"
        if specification.get("source_sha256", {}).get(filename) != sha(
            Path(__file__).parent / filename
        ):
            raise ValueError(f"Execution source changed since training: {filename}")
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
    turnover = paths.config.get("turnover_policy", "legacy")
    if any(choice.get("turnover_policy", "legacy") != turnover for choice in choices):
        raise ValueError("Training windows disagree about the turnover policy")
    evaluated = evaluate_accounts(
        bundle, maps, schedule, start, end, costs, policies, output, turnover=turnover
    )
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
        "execution_model": EXECUTION_MODEL,
        "source_run": run.relative_to(paths.root).as_posix(),
        "identity": status["identity"],
        "data_cutoff": paths.config["data_cutoff"],
        "primary_policy": "selected_strategy",
        "turnover_policy": turnover,
        "trade_start": str(bundle["calendar"][start + 1].date()),
        "trade_end": str(bundle["calendar"][end + 1].date()),
        "outer_signal_sessions": end - start + 1,
        "folds": len(folds),
        "fold_choices": [
            {key: choice[key] for key in ("fold", "selected_candidate", "selected_rules")}
            for choice in choices
        ],
        **evaluated,
        "input_sha256": {
            path.relative_to(paths.root).as_posix(): sha(path) for path in input_files
        },
    }
    save(output / "evaluation_summary.json", result, immutable=True)
    return result


def evaluate_accounts(
    bundle, maps, schedule, start, end, costs, policies, output, *, turnover="legacy"
):
    """Persist the same audited account matrix for retraining and execution diagnosis."""
    results, continuous, artifacts = {}, {}, {}
    reconciliation_checks = execution_checks = accounts = 0
    for cost in sorted(set(costs)):
        key = f"{cost:02d}bps"
        results[key], continuous[key] = {}, {}
        for policy in policies:
            forecasts, rules = policy_inputs(policy, maps, schedule)
            episodes_result = episodes(
                bundle, start, end, forecasts, cost / 10000, True, rules=rules, turnover=turnover
            )
            summary, ledger = simulate(
                bundle,
                start + 1,
                end + 1,
                forecasts,
                cost / 10000,
                True,
                rules=rules,
                turnover=turnover,
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
            log("Evaluated accounts", cost=key, policy=policy, accounts=len(episodes_result) + 1)
    return {
        "results": results,
        "continuous": continuous,
        "accounts": accounts,
        "reconciliation_checks": reconciliation_checks,
        "execution_checks": execution_checks,
        "artifacts": artifacts,
    }


def first_difference(before, after, path=""):
    """Find the first changed existing field, ignoring newly added audit metadata."""
    if isinstance(before, dict) and isinstance(after, dict):
        for key in before:
            difference = first_difference(before[key], after.get(key), f"{path}/{key}")
            if difference:
                return difference
        return None
    if isinstance(before, list) and isinstance(after, list):
        for index, (old, new) in enumerate(zip(before, after)):
            difference = first_difference(old, new, f"{path}/{index}")
            if difference:
                return difference
        if len(before) == len(after):
            return None
        return {"path": path + "/length", "before": len(before), "after": len(after)}
    if before != after:
        return {"path": path, "before": before, "after": after}
    return None


def execution_bridge(
    paths: Paths,
    output: Path,
    costs: list[int],
    policies: list[str],
    *,
    baseline: str | Path | None = None,
) -> dict:
    """Hold archived predictions and choices fixed while diagnosing corrected execution."""
    output = paths.output_path(output, "evaluations")
    if not costs or any(cost not in (10, 20, 30) for cost in costs):
        raise ValueError("Choose one or more slippage costs from 10, 20, 30 bps")
    if not policies or any(policy not in POLICIES for policy in policies):
        raise ValueError("Choose registered policies for the execution bridge")
    check_integrity(paths)
    bundle = load_bundle(paths, reference=baseline is None)
    comparison = (
        paths.reference if baseline is None else paths.artifact_path(baseline, "evaluations")
    )
    previous = read(comparison / "evaluation_summary.json")
    if baseline is not None:
        if (
            previous.get("purpose") not in {"EXECUTION_DIAGNOSTIC", "DATA_DIAGNOSTIC"}
            or previous["status"] != "COMPLETE"
            or previous["source_run"] != paths.reference.relative_to(paths.root).as_posix()
        ):
            raise ValueError("Data bridge requires a completed diagnostic with frozen predictions")
        for relative, expected in previous["artifacts"].items():
            if sha(comparison / relative) != expected:
                raise ValueError(f"Execution diagnostic artifact changed: {relative}")
        original = load_bundle(paths, reference=True)
        if (
            not original["calendar"].equals(bundle["calendar"])
            or original["features"] != bundle["features"]
        ):
            raise ValueError(
                "Fixed-prediction data bridge requires identical calendar and features"
            )
        import pandas as pd

        columns = ["date_index", "symbol", "eligible", *bundle["features"]]
        pd.testing.assert_frame_equal(original["table"][columns], bundle["table"][columns])
    maps, schedule, choices, start, end = load_predictions(bundle, paths.reference)
    protocol = (
        paths.root / "configs/protocols/causal_execution.json"
        if baseline is None
        else paths.protocol
    )
    specification = {
        "protocol_sha256": sha(protocol),
        "data_sha256": sha(paths.reference_bundle if baseline is None else paths.bundle),
        "comparison_summary_sha256": sha(comparison / "evaluation_summary.json"),
        "reference_manifest_sha256": sha(paths.manifest),
        "source_sha256": {
            file.name: sha(file) for file in sorted(Path(__file__).parent.glob("*.py"))
        },
        "costs_bps": costs,
        "policies": policies,
    }
    save(output / "protocol.json", specification, immutable=True)
    evaluated = evaluate_accounts(bundle, maps, schedule, start, end, costs, policies, output)
    differences = {}
    for relative in evaluated["artifacts"]:
        old = load_gzip(comparison / relative)
        new = load_gzip(output / relative)
        old_accounts = old if isinstance(old, list) else [old]
        new_accounts = new if isinstance(new, list) else [new]
        if len(old_accounts) != len(new_accounts):
            raise ValueError(f"Bridge account boundaries differ: {relative}")
        differences[relative] = [
            {
                "account": index,
                "start": prior["summary"]["start"],
                "before": prior["summary"],
                "after": current["summary"],
                "first_difference": {
                    section: first_difference(prior["ledger"][section], current["ledger"][section])
                    for section in ("decisions", "trades", "rejections", "curve")
                },
            }
            for index, (prior, current) in enumerate(zip(old_accounts, new_accounts))
        ]
    save(output / "differences.json", differences, immutable=True)
    result = {
        "schema_version": 1,
        "status": "COMPLETE",
        "purpose": "EXECUTION_DIAGNOSTIC" if baseline is None else "DATA_DIAGNOSTIC",
        "completed_at": now(),
        "decision": "RESEARCH_ONLY",
        "superiority_assessment": "NOT_APPLICABLE_TO_FIXED_OLD_SELECTIONS",
        "execution_model": EXECUTION_MODEL,
        "source_run": paths.reference.relative_to(paths.root).as_posix(),
        "comparison_run": comparison.relative_to(paths.root).as_posix(),
        "identity": digest(specification),
        "data_cutoff": str(bundle["cutoff"]),
        "primary_policy": "selected_strategy",
        "trade_start": str(bundle["calendar"][start + 1].date()),
        "trade_end": str(bundle["calendar"][end + 1].date()),
        "outer_signal_sessions": end - start + 1,
        "folds": len(choices),
        "fold_choices": [
            {key: choice[key] for key in ("fold", "selected_candidate", "selected_rules")}
            for choice in choices
        ],
        "differences_sha256": sha(output / "differences.json"),
        **evaluated,
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
    if name == "selected_baseline_rules":
        return maps["selected"], "baseline"
    raise ValueError(name)


def replay(paths: Paths, output: Path, costs: list[int], policies: list[str]) -> dict[str, Any]:
    """Re-run frozen accounts; fail if refactoring changes any recorded economics."""
    from .storage import load_gzip

    integrity = check_integrity(paths)
    reference_protocol = read(paths.reference / "protocol.json")
    if reference_protocol.get("specification", {}).get("execution_model") != EXECUTION_MODEL:
        raise ValueError(
            "The frozen reference uses the archived execution engine. "
            "Use revision e11bb36 for exact replay, or execution-bridge for corrected execution."
        )
    bundle = load_bundle(paths)
    maps, schedule, folds, start, end = load_predictions(bundle, paths.reference)
    results = {}
    accounts = 0
    reconciliation_checks = execution_checks = 0
    for cost in costs:
        key = f"{cost:02d}bps"
        results[key] = {}
        for policy in policies:
            forecasts, rules = policy_inputs(policy, maps, schedule)
            actual = episodes(bundle, start, end, forecasts, cost / 10000, True, rules=rules)
            expected = load_gzip(
                paths.reference / "evaluation" / key / (policy + ".ledgers.json.gz")
            )
            if len(actual) != len(expected):
                raise AssertionError(f"Account count mismatch: {key}/{policy}")
            summary, continuous = simulate(
                bundle, start + 1, end + 1, forecasts, cost / 10000, True, rules=rules
            )
            expected_continuous = load_gzip(
                paths.reference / "evaluation/continuous" / key / (policy + ".json.gz")
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
