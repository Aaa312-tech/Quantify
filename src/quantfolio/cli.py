"""Offline portfolio research: train, evaluate, report and verify."""

from __future__ import annotations

import argparse
import json
import logging

from . import __version__
from .settings import Paths, project_root


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="quantfolio", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--project", help="Project directory (or QUANTFOLIO_ROOT)")
    parser.add_argument("--debug", action="store_true", help="Show traceback on research errors")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show the active strategy and validation status")
    exit_study = commands.add_parser(
        "exit-study", help="Train the registered sell-budget comparison"
    )
    exit_study.add_argument(
        "--output", required=True, help="New directory under artifacts/experiments"
    )
    exit_evaluation = commands.add_parser(
        "evaluate-exits", help="Evaluate the registered sell-budget comparison"
    )
    exit_evaluation.add_argument("--run", required=True, help="Completed exit-study experiment")
    exit_evaluation.add_argument(
        "--output", required=True, help="New directory under artifacts/evaluations"
    )
    verification = commands.add_parser("verify", help="Check frozen files, features and models")
    verification.add_argument("--output", help="New directory under artifacts/checks")
    verification.add_argument("--refit", action="store_true", help="Refit fold 1's two estimators")
    verification.add_argument(
        "--run", help="Verify a new training run instead of the frozen reference"
    )
    repair = commands.add_parser(
        "repair-data", help="Build the registered corporate-action snapshot"
    )
    repair.add_argument("--output", required=True, help="New directory under data/processed")
    repair.add_argument(
        "--protocol",
        default="configs/protocols/action_repair.json",
        help="Registered repair protocol",
    )
    status_repair = commands.add_parser(
        "repair-status", help="Rebuild eligibility from reviewed historical warning notices"
    )
    status_repair.add_argument("--output", required=True, help="New directory under data/processed")
    status_repair.add_argument(
        "--protocol", required=True, help="Registered status repair protocol"
    )
    resumption = commands.add_parser(
        "repair-resumption", help="Rebuild labels from a reviewed suspension and resumption"
    )
    resumption.add_argument("--output", required=True, help="New directory under data/processed")
    resumption.add_argument("--protocol", required=True, help="Registered resumption protocol")
    refresh = commands.add_parser("refresh-data", help="Build a reviewed market-data extension")
    refresh.add_argument("--output", required=True, help="New directory under data/processed")
    refresh.add_argument("--protocol", required=True, help="Registered market refresh protocol")
    collection = commands.add_parser(
        "fetch-bars", help="Collect completed daily bars for separate review"
    )
    collection.add_argument("--date", required=True, help="Completed session in YYYY-MM-DD form")
    collection.add_argument("--output", required=True, help="New directory under data/raw")
    collection.add_argument(
        "--retry", help="Completed raw collection with failed or missing requests"
    )
    status_collection = commands.add_parser(
        "fetch-status", help="Collect retrospective daily flags for separate source review"
    )
    status_collection.add_argument("--output", required=True, help="New directory under data/raw")
    status_collection.add_argument("--retry", help="Finished collection with missing histories")
    disclosures = commands.add_parser(
        "fetch-disclosures", help="Archive issuer notices for unresolved historical events"
    )
    disclosures.add_argument("--output", required=True, help="New directory under data/raw")
    status_audit = commands.add_parser(
        "audit-status", help="Compare daily flags with frozen history"
    )
    status_audit.add_argument(
        "--collection", required=True, action="append", help="Finished raw batch"
    )
    status_audit.add_argument(
        "--output", required=True, help="New directory under artifacts/checks"
    )
    replay = commands.add_parser(
        "replay", aliases=["backtest"], help="Compare the frozen reference accounts exactly"
    )
    replay.add_argument("--output", required=True, help="New directory under artifacts/replays")
    replay.add_argument("--cost", type=int, choices=[10, 20, 30], action="append")
    replay.add_argument("--primary-only", action="store_true")
    bridge = commands.add_parser(
        "execution-bridge", help="Diagnose execution with frozen predictions"
    )
    bridge.add_argument("--output", required=True, help="New directory under artifacts/evaluations")
    bridge.add_argument("--cost", type=int, choices=[10, 20, 30], action="append")
    bridge.add_argument("--primary-only", action="store_true")
    data_bridge = commands.add_parser(
        "data-bridge", help="Compare repaired data with fixed predictions"
    )
    data_bridge.add_argument(
        "--baseline", required=True, help="Completed execution bridge under artifacts/evaluations"
    )
    data_bridge.add_argument(
        "--output", required=True, help="New directory under artifacts/evaluations"
    )
    training = commands.add_parser("train", help="Train a new run using the registered protocol")
    training.add_argument(
        "--output", required=True, help="New directory under artifacts/experiments"
    )
    training.add_argument("--first", type=int, default=1)
    training.add_argument("--last", type=int, default=11)
    training.add_argument("--include-latest", action="store_true")
    study = commands.add_parser("study", help="Refit and select the registered research recipes")
    study.add_argument("--output", required=True, help="New directory under artifacts/experiments")
    study.add_argument("--protocol", default="configs/protocols/strategy_study.json")
    study_evaluation = commands.add_parser(
        "evaluate-study", help="Evaluate frozen research recipes"
    )
    study_evaluation.add_argument("--run", required=True, help="Completed study directory")
    study_evaluation.add_argument(
        "--output", required=True, help="New directory under artifacts/evaluations"
    )
    evaluation = commands.add_parser("evaluate", help="Backtest a completed training run")
    evaluation.add_argument(
        "--run", required=True, help="Training directory under artifacts/experiments"
    )
    evaluation.add_argument(
        "--output", required=True, help="New directory under artifacts/evaluations"
    )
    evaluation.add_argument("--cost", type=int, choices=[10, 20, 30], action="append")
    evaluation.add_argument(
        "--all-policies", action="store_true", help="Include all 19 rule comparisons"
    )
    account = commands.add_parser("account-init", help="Create an initial virtual cash account")
    paper_check = commands.add_parser(
        "verify-paper", help="Compare daily paper workflow with all active frozen accounts"
    )
    paper_check.add_argument("--output", required=True, help="New directory under artifacts/checks")
    account.add_argument("--account-id", required=True)
    account.add_argument("--as-of", help="Completed closing date; defaults to the active cutoff")
    account.add_argument("--output", required=True, help="New directory under artifacts/accounts")
    plan = commands.add_parser("plan", help="Freeze a conditional next-opening paper plan")
    plan.add_argument("--account", required=True, help="Manual or paper closing-account JSON")
    plan.add_argument("--cost", type=int, choices=[10, 20, 30], default=10)
    plan.add_argument("--output", required=True, help="New directory under artifacts/plans")
    resolve = commands.add_parser(
        "resolve-plan", help="Recalculate paper quantities at supplied opening quotes"
    )
    resolve.add_argument("--plan", required=True, help="Frozen plan.json")
    resolve.add_argument("--quotes", required=True, help="Manually supplied opening quotes JSON")
    resolve.add_argument("--output", required=True, help="New directory under artifacts/plans")
    closing = commands.add_parser(
        "account-close", help="Mark paper positions and carry account state forward"
    )
    closing.add_argument("--resolution", required=True, help="Frozen resolution.json")
    closing.add_argument(
        "--quotes", required=True, help="Manually supplied completed closing quotes JSON"
    )
    closing.add_argument("--output", required=True, help="New directory under artifacts/accounts")
    report = commands.add_parser(
        "report", help="Render reference results or an evaluated experiment"
    )
    report_source = report.add_mutually_exclusive_group()
    report_source.add_argument("--run", help="Evaluated directory under artifacts/evaluations")
    report_source.add_argument(
        "--reference", action="store_true", help="Render the archived reference"
    )
    args = parser.parse_args(argv)
    if args.command == "verify" and args.refit and not args.output:
        parser.error("--refit requires --output artifacts/checks/<new-name>")
    if args.command == "train" and not 1 <= args.first <= args.last:
        parser.error("require 1 <= --first <= --last")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        result = run(args, Paths(project_root(args.project)))
    except (OSError, ValueError, AssertionError, KeyError) as error:
        if args.debug:
            raise
        message = f"Missing artifact field: {error}" if isinstance(error, KeyError) else str(error)
        parser.exit(1, f"quantfolio: {message}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))


def run(args: argparse.Namespace, paths: Paths) -> dict:
    from .storage import read, sha

    if args.command == "exit-study":
        from .feasibility import run_exit_study

        return run_exit_study(paths, args.output)
    if args.command == "evaluate-exits":
        from .feasibility import evaluate_exit_study

        return evaluate_exit_study(paths, args.run, args.output)
    if args.command == "account-init":
        from .planning import initialize_account

        return initialize_account(paths, args.output, args.account_id, args.as_of)
    if args.command == "verify-paper":
        from .verification import verify_paper

        return verify_paper(paths, args.output)
    if args.command == "plan":
        from .planning import freeze_plan

        return freeze_plan(paths, args.account, args.output, args.cost)
    if args.command == "resolve-plan":
        from .planning import resolve_plan

        return resolve_plan(paths, args.plan, args.quotes, args.output)
    if args.command == "account-close":
        from .planning import close_account

        return close_account(paths, args.resolution, args.quotes, args.output)
    if args.command == "status":
        from .market import EXECUTION_MODEL
        from .status_data import load_status_review

        active = paths.config.get("evaluation")
        source = paths.artifact_path(active, "evaluations") if active else paths.reference
        path = source / "evaluation_summary.json"
        manifest = read(paths.manifest)
        if sha(path) != manifest["files"][path.relative_to(paths.root).as_posix()]:
            raise ValueError("Configured evaluation summary hash mismatch")
        result = read(path)
        corrected = False
        no_positive_excess = all(
            values[result["primary_policy"]]["summary"]["return"]
            <= values["matched_momentum"]["summary"]["return"]
            for values in result["continuous"].values()
        )
        if active:
            from .evaluation import experiment_folds, load_bundle

            training = paths.artifact_path(result["source_run"], "experiments")
            status, _ = experiment_folds(paths, load_bundle(paths), training)
            if result["identity"] != status["identity"]:
                raise ValueError("Configured evaluation and training identities differ")
            corrected = status["status"] == "COMPLETE" and status.get("latest_fit_included", False)
        return {
            "software_version": __version__,
            "strategy": paths.config["strategy"],
            "strategy_name": paths.config["strategy_name"],
            "decision": result["decision"],
            "execution_model": EXECUTION_MODEL,
            "displayed_results_scope": "CAUSAL_RETRAINING"
            if active
            else "ARCHIVED_EXECUTION_WITH_KNOWN_CAUSALITY_DEFECT",
            "corrected_retraining_completed": corrected,
            "source_evaluation": source.relative_to(paths.root).as_posix(),
            "data_cutoff": paths.config["data_cutoff"],
            "historical_status_review": load_status_review(paths),
            "test_start": result["trade_start"],
            "test_end": result["trade_end"],
            "gates": result.get("gates", {"superiority": False}),
            "superiority_assessment": "NO_POSITIVE_EXCESS_TO_MATCHED_MOMENTUM"
            if active and no_positive_excess
            else result.get("superiority_assessment", "NOT_PASSED"),
            "continuous": {
                key: {
                    name: values[result["primary_policy"]]["summary"][name]
                    for name in ("return", "max_drawdown", "trades", "fees")
                }
                for key, values in result["continuous"].items()
            },
        }
    if args.command == "verify":
        from .verification import verify

        output = paths.output_path(args.output, "checks") if args.output else None
        return verify(paths, output, args.refit, args.run)
    if args.command == "repair-data":
        from .snapshots import repair_snapshot

        return repair_snapshot(paths, args.output, args.protocol)
    if args.command == "repair-status":
        from .status_repair import repair_status_snapshot

        return repair_status_snapshot(paths, args.output, args.protocol)
    if args.command == "repair-resumption":
        from .status_repair import repair_resumption_snapshot

        return repair_resumption_snapshot(paths, args.output, args.protocol)
    if args.command == "fetch-bars":
        from .market_data import fetch_bars

        return fetch_bars(paths, args.date, args.output, args.retry)
    if args.command == "fetch-status":
        from .status_data import fetch_status_history

        return fetch_status_history(paths, args.output, args.retry)
    if args.command == "fetch-disclosures":
        from .disclosures import collect_disclosures

        return collect_disclosures(paths, args.output)
    if args.command == "audit-status":
        from .status_data import audit_status_history

        return audit_status_history(paths, args.collection, args.output)
    if args.command == "refresh-data":
        from .refresh import refresh_snapshot

        return refresh_snapshot(paths, args.output, args.protocol)
    if args.command in ("replay", "backtest"):
        from .evaluation import POLICIES, replay

        output = paths.output_path(args.output, "replays")
        policies = ["selected_strategy", "matched_momentum"] if args.primary_only else POLICIES
        return replay(paths, output, sorted(set(args.cost or [10, 20, 30])), policies)
    if args.command == "train":
        from .training import run_training

        output = paths.output_path(args.output, "experiments")
        return run_training(paths, output, args.first, args.last, args.include_latest)
    if args.command == "study":
        from .study import run_study

        return run_study(paths, args.output, args.protocol)
    if args.command == "evaluate-study":
        from .study import evaluate_study

        return evaluate_study(paths, args.run, args.output)
    if args.command == "execution-bridge":
        from .evaluation import POLICIES, execution_bridge

        policies = ["selected_strategy", "matched_momentum"] if args.primary_only else POLICIES
        return execution_bridge(
            paths, args.output, sorted(set(args.cost or [10, 20, 30])), policies
        )
    if args.command == "data-bridge":
        from .evaluation import POLICIES, execution_bridge

        return execution_bridge(paths, args.output, [10, 20, 30], POLICIES, baseline=args.baseline)
    if args.command == "evaluate":
        from .evaluation import evaluate

        return evaluate(paths, args.run, args.output, args.cost or [10, 20, 30], args.all_policies)
    from .reporting import build_report

    return build_report(paths, args.run, reference=args.reference)
