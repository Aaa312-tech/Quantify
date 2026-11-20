"""Reload saved estimators and compare paper-account execution with frozen ledgers."""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from .dataset import (
    check_integrity,
    eligible_rows,
    label_mask,
    load_bundle,
    validate_features,
    walk_forward_folds,
)
from .models import fit_stage
from .settings import Paths
from .storage import now, read, save, sha


def verify(
    paths: Paths, output: Path | None = None, refit: bool = False, run: str | Path | None = None
) -> dict:
    """Validate every stored model against its predictions; refit only to a new output."""
    if refit and output is None:
        raise ValueError("Refit verification requires a new output directory")
    integrity = check_integrity(paths)
    bundle = load_bundle(paths, reference=run is None)
    source = paths.reference if run is None else paths.artifact_path(run, "experiments")
    if run is not None:
        from .evaluation import experiment_folds

        experiment_folds(paths, bundle, source)
    table = bundle["table"]
    feature_check = validate_features(bundle)
    matrix = table[bundle["features"]].to_numpy(dtype=np.float32)
    records = []
    result_paths = sorted(source.glob("training/fold_*/result.json"))
    if (source / "final_fit/result.json").is_file():
        result_paths.append(source / "final_fit/result.json")
    for result_path in result_paths:
        result = read(result_path)
        fold = result["fold"]
        stages = [
            ("validation_fit", fold["validation_start"], fold["validation_end"]),
            ("outer_fit", fold["test_start"], fold["test_end"]),
        ]
        for stage, start, end in stages:
            rows = eligible_rows(table, start, end)
            for family in ("ridge", "tree"):
                folder = result_path.parent / stage / family
                metadata = read(folder / "fit.json")
                if sha(folder / "model.joblib") != metadata["model_sha256"]:
                    raise ValueError(f"Model changed: {folder}")
                model = joblib.load(folder / "model.joblib")
                with threadpool_limits(limits=2):
                    actual = model.predict(matrix[rows])
                expected = np.load(folder / "predictions.npy")
                np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
                before = start + int(result["final_fit"] and stage == "outer_fit")
                if metadata["latest_training_label_end"] >= before:
                    raise AssertionError(f"Label availability violation: {folder}")
                records.append(
                    {
                        "path": folder.relative_to(paths.root).as_posix(),
                        "predictions": len(rows),
                        "max_error": float(np.max(np.abs(actual - expected))),
                        "label_availability_pass": True,
                    }
                )
    fresh = None
    if refit:
        fold = walk_forward_folds(bundle["calendar"])[0]
        training_rows = np.flatnonzero(
            label_mask(table, 5, fold["train_start"], fold["train_end"], fold["validation_start"])
        )
        prediction_rows = eligible_rows(table, fold["validation_start"], fold["validation_end"])
        predictions = fit_stage(
            bundle,
            training_rows,
            prediction_rows,
            output / "fresh_fit",
            {"purpose": "independent refit verification", "fold": fold},
        )
        fresh = {}
        for family, actual in predictions.items():
            expected = np.load(
                source / "training/fold_01/validation_fit" / family / "predictions.npy"
            )
            np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
            fresh[family] = {
                "predictions": len(actual),
                "max_error": float(np.max(np.abs(actual - expected))),
            }
    result = {
        "status": "PASS",
        "completed_at": now(),
        "source_run": source.relative_to(paths.root).as_posix(),
        "data_sha256": sha(paths.reference_bundle if run is None else paths.bundle),
        "integrity": integrity,
        "features": feature_check,
        "models_reloaded": len(records),
        "models": records,
        "fresh_refit": fresh,
    }
    if output:
        save(output / "verification.json", result, immutable=True)
    return result


def _paper_account(bundle, forecasts, rules, reference, cost, *, turnover="legacy"):
    """Replay a complete stored account through daily manual-account round trips."""
    from .accounts import new_account, restore_account, snapshot_account
    from .market import EXECUTION_MODEL
    from .metrics import account_stats
    from .planning import make_intent, resolve_intent
    from .rules import config_for

    dates = {str(day.date()): index for index, day in enumerate(bundle["calendar"])}
    start, end = dates[reference["summary"]["start"]], dates[reference["summary"]["end"]]
    account = new_account(str(bundle["calendar"][start - 1].date()), "historical_diagnostic")
    calendar = {"sessions": list(dates)}
    curve, trades, events, rejections, decisions, marks = [], [], [], [], [], []
    paper_checks = 0
    for index in range(start, end + 1):
        date, previous = bundle["calendar"][index], bundle["calendar"][index - 1]
        day, signal = str(date.date()), str(previous.date())
        view = {**bundle, "calendar": bundle["calendar"][:index]}
        config = config_for(rules, index - 1)
        intent = make_intent(
            view, account, forecasts[index - 1], config, day, cost, turnover=turnover
        )
        actions = [
            {**action, "paper_meta": bundle["meta"][action["symbol"]]}
            for action in bundle["actions"]
            if action["ex_date"] == day or action.get("record_date") in (signal, day)
        ]
        quotes = {}
        for symbol in intent["contracts"]:
            raw = bundle["raws"][symbol]
            quote = raw.at[date, "open"] if date in raw.index else np.nan
            quotes[symbol] = {
                "open": float(quote) if np.isfinite(quote) else None,
                "market_at": day + "T09:25:00+08:00",
                "suspended": bundle.get("opening_status", {})
                .get(day, {})
                .get(symbol, {})
                .get("suspended", False),
            }
        result = resolve_intent(intent, account, actions, calendar, quotes)
        paper_checks += result["audit"]["checks"]
        # Per-day executors count fills from zero; the frozen account counts cumulatively.
        marks.extend(
            {**mark, "after_fill": mark["after_fill"] + len(trades)}
            for mark in result["intermediate_weights"]
        )
        trades.extend(result["trades"])
        events.extend(result["events"])
        rejections.extend(result["rejections"])
        decision = result["decision"]
        decision.update(
            signal_date=signal,
            execution_date=day,
            drawdown=intent["state"]["drawdown"],
            scheduled=intent["scheduled"],
            forced=intent["forced"],
            recovery=intent["state"],
            rule_config=config,
            target_weights=intent["target_weights"],
        )
        decisions.append(decision)
        if turnover != "legacy":
            decision["turnover_policy"] = turnover
        book, risk = restore_account(result["opening_account"], bundle["meta"])
        closing = {s: float(bundle["raws"][s].at[date, "close"]) for s in book.lots}
        if not all(np.isfinite(value) and value > 0 for value in closing.values()):
            raise ValueError("Paper replay needs an observed closing mark for every holding")
        book.snapshot_record_dates(date, actions)
        values, nav = book.values(closing), book.nav(closing)
        state = intent["state"]
        curve.append(
            {
                "date": day,
                "prior_close_nav": intent["prior_close_nav"],
                "nav": nav,
                "cash": book.cash / 100,
                "holdings_value": sum(values.values()),
                "receivables": sum(r["cents"] for r in book.receivables) / 100,
                "positions": {
                    s: {
                        "quantity": book.quantity(s),
                        "available": book.available(s, date),
                        "close": closing[s],
                        "value": value,
                    }
                    for s, value in values.items()
                },
                "risk_level": state["level"],
                "max_nav_weight": max(values.values(), default=0) / nav,
                "risk_cap": intent["cap"],
                "risk_phase": state["phase"],
                "risk_peak": state["peak"],
                "risk_drawdown": state["drawdown"],
                "can_buy": state["can_buy"],
                "exposure_above_target": sum(values.values()) / nav > intent["cap"] + 0.01,
            }
        )
        account = snapshot_account(
            book, result["opening_account"], day, risk, index - start + 1, None
        )
        account["valuation"] = {"nav": nav, "positions": values, "quotes": closing}
    book.trades, book.events, book.rejections = trades, events, rejections
    ledger = {
        "execution": {"model": EXECUTION_MODEL, "slippage_rate": cost},
        "curve": curve,
        "trades": trades,
        "events": events,
        "rejections": rejections,
        "quality": [],
        "decisions": decisions,
        "intermediate_weights": marks,
    }
    if turnover != "legacy":
        ledger["execution"]["turnover_policy"] = turnover
    return {
        "summary": account_stats(book, curve, decisions, [], "latched"),
        "ledger": ledger,
    }, paper_checks


def verify_paper(paths: Paths, output: Path) -> dict:
    """Compare all 75 active-policy accounts, not just individual fills or final returns."""
    from .evaluation import experiment_folds, first_difference, load_predictions
    from .planning import source_hashes
    from .storage import digest, load_gzip, log
    from .validation import execution_audit, reconcile

    output = paths.output_path(output, "checks")
    integrity = check_integrity(paths)
    bundle = load_bundle(paths)
    evaluation = paths.artifact_path(paths.config["evaluation"], "evaluations")
    result = read(evaluation / "evaluation_summary.json")
    run = paths.artifact_path(result["source_run"], "experiments")
    status, folds = experiment_folds(paths, bundle, run)
    forecasts, rules, _, _, _ = load_predictions(bundle, run, folds, status["identity"])
    records = []
    accounting_checks = execution_checks = paper_checks = 0
    for cost in (10, 20, 30):
        key = f"{cost:02d}bps"
        names = [
            f"evaluation/{key}/selected_strategy.ledgers.json.gz",
            f"evaluation/continuous/{key}/selected_strategy.json.gz",
        ]
        for relative in names:
            file = evaluation / relative
            if sha(file) != result["artifacts"][relative]:
                raise ValueError("Frozen paper comparison account changed")
            stored = load_gzip(file)
            references = stored if isinstance(stored, list) else [stored]
            for index, reference in enumerate(references):
                expected = {k: reference[k] for k in ("summary", "ledger")}
                actual, checks = _paper_account(
                    bundle,
                    forecasts["selected"],
                    rules,
                    expected,
                    cost / 10000,
                    turnover=paths.config.get("turnover_policy", "legacy"),
                )
                audit = execution_audit(actual["ledger"], bundle)
                reconciliation = reconcile(
                    actual["ledger"], actual["summary"], bundle["meta"], bundle["actions"]
                )
                record = {
                    "path": relative,
                    "account_index": index,
                    "sessions": actual["summary"]["sessions"],
                    "full_account_equal": actual == expected,
                    "expected_sha256": digest(expected),
                    "actual_sha256": digest(actual),
                    "first_difference": first_difference(expected, actual),
                    "reconciliation": reconciliation,
                    "execution_audit": audit,
                    "paper_fill_checks": checks,
                }
                records.append(record)
                accounting_checks += reconciliation["checks"]
                execution_checks += audit["checks"]
                paper_checks += checks
                if (
                    actual != expected
                    or audit["status"] != "PASS"
                    or reconciliation["status"] != "PASS"
                ):
                    save(
                        output / "verification.json",
                        {"status": "FAIL", "records": records},
                        immutable=True,
                    )
                    raise ValueError(
                        f"Paper workflow differs from its frozen baseline: {relative}/{index}: {record['first_difference']}"
                    )
            log("Paper workflow account comparison complete", cost=key, source=relative)
    report = {
        "status": "PASS",
        "completed_at": now(),
        "scope": "Historical integration replay, not a forward recommendation or observation.",
        "source_evaluation": evaluation.relative_to(paths.root).as_posix(),
        "evaluation_sha256": sha(evaluation / "evaluation_summary.json"),
        "data_sha256": sha(paths.bundle),
        "source_sha256": {**source_hashes(), "verification": sha(Path(__file__))},
        "accounts": len(records),
        "sessions": sum(r["sessions"] for r in records),
        "integrity": integrity,
        "reconciliation_checks": accounting_checks,
        "execution_checks": execution_checks,
        "paper_fill_checks": paper_checks,
        "records": records,
    }
    save(output / "verification.json", report, immutable=True)
    return {k: v for k, v in report.items() if k != "records"}
