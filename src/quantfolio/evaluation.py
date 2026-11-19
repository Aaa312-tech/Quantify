"""Replay frozen forecasts and compare complete ledgers against the retained V6 evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .audit import execution_audit
from .dataset import check_integrity, load_bundle
from .metrics import stats
from .predictions import prediction_map
from .reconciliation import reconcile
from .rules import CONFIGS
from .settings import Paths
from .simulation import episodes, simulate
from .splits import walk_forward_folds
from .storage import digest, log, now, read, save, sha

POLICIES = ["v6_selected", "matched_momentum", "selected_legacy"] + [
    "fixed_" + name for name in CONFIGS
]


def load_predictions(bundle: dict, run: Path) -> tuple:
    """Combine non-overlapping outer folds and their validation-selected rule schedule."""
    maps = {"selected": {}, "momentum": {}}
    schedule = {}
    results = []
    folds = walk_forward_folds(bundle["calendar"])
    for fold in folds:
        folder = run / "training" / f"fold_{fold['fold']:02d}"
        result = read(folder / "result.json")
        if sha(folder / "outer_predictions.npz") != result["predictions_sha256"]:
            raise ValueError(f"Prediction hash mismatch: {folder}")
        with np.load(folder / "outer_predictions.npz") as stored:
            arrays = dict(stored)
        for name in maps:
            candidate = "momentum" if name == "momentum" else result["selected_candidate"]
            maps[name].update(prediction_map(bundle["table"], arrays, candidate))
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


def policy_inputs(name: str, maps: dict, schedule: dict) -> tuple:
    """Use exactly the documented forecast and turnover-rule combinations."""
    if name.startswith("fixed_"):
        return maps["momentum"], name[6:]
    if name == "v6_selected":
        return maps["selected"], schedule
    if name == "matched_momentum":
        return maps["momentum"], schedule
    if name == "selected_legacy":
        return maps["selected"], "baseline"
    raise ValueError(name)


def comparable_ledger(ledger: dict) -> dict:
    """Compare every deterministic account field, including rejected orders and decisions."""
    return ledger


def replay(paths: Paths, output: Path, costs: list[int], policies: list[str]) -> dict[str, Any]:
    """Re-run frozen V6 accounts; fail if refactoring changes any recorded economics."""
    from .storage import load_gzip

    integrity = check_integrity(paths)
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
                if digest(comparable_ledger(new["ledger"])) != digest(
                    comparable_ledger(old["ledger"])
                ):
                    raise AssertionError(f"Ledger mismatch: {key}/{policy}/{number}")
                check = reconcile(new["ledger"], new["summary"], bundle["meta"], bundle["actions"])
                audit = execution_audit(new["ledger"], bundle)
                if check["status"] != "PASS" or audit["status"] != "PASS":
                    raise AssertionError({"reconciliation": check, "execution": audit})
                reconciliation_checks += check["checks"]
                execution_checks += audit["checks"]
                accounts += 1
            results[key][policy] = {
                "aggregate": stats(actual),
                "continuous": summary,
                "accounts": len(paired),
                "full_ledger_exact": True,
            }
            log("Replay matched frozen ledgers", cost=key, policy=policy, accounts=len(paired))
            save(output / "progress.json", {"completed_at": now(), "results": results})
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
