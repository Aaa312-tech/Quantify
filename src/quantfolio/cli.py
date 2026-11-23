"""Offline portfolio research: train, evaluate, report and verify."""

from __future__ import annotations

import argparse
import json
import logging

from . import __version__
from .settings import Paths, project_root


def _add_output(parser: argparse.ArgumentParser, location: str, *, required: bool = True) -> None:
    parser.add_argument("--output", required=required, help=f"New directory under {location}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantfolio", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--project", help="Project directory (or QUANTFOLIO_ROOT)")
    parser.add_argument(
        "--config", default="configs/project.toml", help="Project-relative TOML under configs"
    )
    parser.add_argument("--debug", action="store_true", help="Show traceback on research errors")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show the active strategy and validation status")
    commands.add_parser("runs", help="List frozen experiments, including failed and inactive runs")
    commands.add_parser("integrity", help="Check every registered file before publication")
    exit_study = commands.add_parser(
        "exit-study", help="Train the registered sell-budget comparison"
    )
    _add_output(exit_study, "artifacts/experiments")
    exit_evaluation = commands.add_parser(
        "evaluate-exits", help="Evaluate the registered sell-budget comparison"
    )
    exit_evaluation.add_argument("--run", required=True, help="Completed exit-study experiment")
    _add_output(exit_evaluation, "artifacts/evaluations")
    verification = commands.add_parser("verify", help="Check frozen files, features and models")
    _add_output(verification, "artifacts/checks", required=False)
    verification.add_argument("--refit", action="store_true", help="Refit fold 1's two estimators")
    verification.add_argument(
        "--run", help="Verify a new training run instead of the frozen reference"
    )
    regression = commands.add_parser(
        "regression", help="Replay the registered engineering baseline without fitting models"
    )
    regression.add_argument("--scope", choices=("core", "full"), default="core")
    regression.add_argument("--baseline", required=True, help="Registered Git baseline commit")
    _add_output(regression, "artifacts/checks")
    regression.add_argument(
        "--register", action="store_true", help="Register source compatibility after full success"
    )
    repair = commands.add_parser(
        "repair-data", help="Build the registered corporate-action snapshot"
    )
    _add_output(repair, "data/processed")
    repair.add_argument(
        "--protocol",
        default="configs/protocols/action_repair.json",
        help="Registered repair protocol",
    )
    distribution_review = commands.add_parser(
        "review-distributions", help="Review the registered dividend source inventory"
    )
    distribution_review.add_argument("--protocol", required=True)
    industry_review = commands.add_parser(
        "review-industries", help="Cross-check registered historical industry source tables"
    )
    industry_review.add_argument("--protocol", required=True)
    industry_repair = commands.add_parser(
        "repair-industries", help="Build a registered dated-industry data control"
    )
    industry_repair.add_argument("--protocol", required=True)
    industry_repair.add_argument(
        "--variant", required=True, choices=("coverage", "static", "dated")
    )
    fund_repair = commands.add_parser(
        "repair-funds", help="Build a registered ETF identity or reported-exposure control"
    )
    fund_repair.add_argument("--protocol", required=True)
    fund_repair.add_argument(
        "--variant", required=True, choices=("coverage", "identity", "reported")
    )
    status_repair = commands.add_parser(
        "repair-status", help="Rebuild eligibility from reviewed historical warning notices"
    )
    _add_output(status_repair, "data/processed")
    status_repair.add_argument(
        "--protocol", required=True, help="Registered status repair protocol"
    )
    resumption = commands.add_parser(
        "repair-resumption", help="Rebuild labels from a reviewed suspension and resumption"
    )
    _add_output(resumption, "data/processed")
    resumption.add_argument("--protocol", required=True, help="Registered resumption protocol")
    refresh = commands.add_parser("refresh-data", help="Build a reviewed market-data extension")
    _add_output(refresh, "data/processed")
    refresh.add_argument("--protocol", required=True, help="Registered market refresh protocol")
    collection = commands.add_parser(
        "fetch-bars", help="Collect completed daily bars for separate review"
    )
    collection.add_argument("--date", required=True, help="Completed session in YYYY-MM-DD form")
    _add_output(collection, "data/raw")
    collection.add_argument(
        "--retry", help="Completed raw collection with failed or missing requests"
    )
    status_collection = commands.add_parser(
        "fetch-status", help="Collect retrospective daily flags for separate source review"
    )
    _add_output(status_collection, "data/raw")
    status_collection.add_argument("--retry", help="Finished collection with missing histories")
    disclosures = commands.add_parser(
        "fetch-disclosures", help="Archive issuer notices for unresolved historical events"
    )
    _add_output(disclosures, "data/raw")
    disclosures.add_argument(
        "--retry", help="Finished source collection with failed or missing requests"
    )
    status_audit = commands.add_parser(
        "audit-status", help="Compare daily flags with frozen history"
    )
    status_audit.add_argument(
        "--collection", required=True, action="append", help="Finished raw batch"
    )
    _add_output(status_audit, "artifacts/checks")
    replay = commands.add_parser(
        "replay", aliases=["backtest"], help="Compare the frozen reference accounts exactly"
    )
    bridge = commands.add_parser(
        "execution-bridge", help="Diagnose execution with frozen predictions"
    )
    for command, location in ((replay, "artifacts/replays"), (bridge, "artifacts/evaluations")):
        _add_output(command, location)
        command.add_argument("--cost", type=int, choices=[10, 20, 30], action="append")
        command.add_argument("--primary-only", action="store_true")
    data_bridge = commands.add_parser(
        "data-bridge", help="Compare repaired data with fixed predictions"
    )
    data_bridge.add_argument(
        "--baseline", required=True, help="Completed execution bridge under artifacts/evaluations"
    )
    _add_output(data_bridge, "artifacts/evaluations")
    training = commands.add_parser("train", help="Train a new run using the registered protocol")
    _add_output(training, "artifacts/experiments")
    training.add_argument("--first", type=int, default=1)
    training.add_argument("--last", type=int, help="Last window (default: all complete windows)")
    training.add_argument("--include-latest", action="store_true")
    study = commands.add_parser("study", help="Refit and select the registered research recipes")
    _add_output(study, "artifacts/experiments")
    study.add_argument("--protocol", default="configs/protocols/strategy_study.json")
    study_evaluation = commands.add_parser(
        "evaluate-study", help="Evaluate frozen research recipes"
    )
    study_evaluation.add_argument("--run", required=True, help="Completed study directory")
    _add_output(study_evaluation, "artifacts/evaluations")
    evaluation = commands.add_parser("evaluate", help="Backtest a completed training run")
    evaluation.add_argument(
        "--run", required=True, help="Training directory under artifacts/experiments"
    )
    _add_output(evaluation, "artifacts/evaluations")
    evaluation.add_argument("--cost", type=int, choices=[10, 20, 30], action="append")
    evaluation.add_argument(
        "--all-policies", action="store_true", help="Include all 19 rule comparisons"
    )
    account = commands.add_parser("account-init", help="Create an initial virtual cash account")
    paper_check = commands.add_parser(
        "verify-paper", help="Compare daily paper workflow with all active frozen accounts"
    )
    _add_output(paper_check, "artifacts/checks")
    account.add_argument("--account-id", required=True)
    account.add_argument("--as-of", help="Completed closing date; defaults to the active cutoff")
    _add_output(account, "artifacts/accounts")
    plan = commands.add_parser("plan", help="Freeze a conditional next-opening paper plan")
    plan.add_argument("--account", required=True, help="Manual or paper closing-account JSON")
    plan.add_argument("--cost", type=int, choices=[10, 20, 30], default=10)
    _add_output(plan, "artifacts/plans")
    resolve = commands.add_parser(
        "resolve-plan", help="Recalculate paper quantities at supplied opening quotes"
    )
    resolve.add_argument("--plan", required=True, help="Frozen plan.json")
    resolve.add_argument("--quotes", required=True, help="Manually supplied opening quotes JSON")
    _add_output(resolve, "artifacts/plans")
    closing = commands.add_parser(
        "account-close", help="Mark paper positions and carry account state forward"
    )
    closing.add_argument("--resolution", required=True, help="Frozen resolution.json")
    closing.add_argument(
        "--quotes", required=True, help="Manually supplied completed closing quotes JSON"
    )
    _add_output(closing, "artifacts/accounts")
    report = commands.add_parser(
        "report", help="Render reference results or an evaluated experiment"
    )
    report_source = report.add_mutually_exclusive_group()
    report_source.add_argument("--run", help="Evaluated directory under artifacts/evaluations")
    report_source.add_argument(
        "--reference", action="store_true", help="Render the archived reference"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "verify" and args.refit and not args.output:
        parser.error("--refit requires --output artifacts/checks/<new-name>")
    if args.command == "regression" and args.register and args.scope != "full":
        parser.error("--register requires --scope full")
    if args.command == "train" and (
        args.first < 1 or (args.last is not None and args.last < args.first)
    ):
        parser.error("require 1 <= --first <= --last")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        result = run(args, Paths(project_root(args.project), args.config))
    except (OSError, ValueError, AssertionError, KeyError) as error:
        if args.debug:
            raise
        message = f"Missing artifact field: {error}" if isinstance(error, KeyError) else str(error)
        parser.exit(1, f"quantfolio: {message}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))


def _project_status(paths: Paths) -> dict:
    """Read the active evaluation and verify its training and frozen-file identities."""
    from .market import EXECUTION_MODEL
    from .status_data import load_status_review
    from .storage import read, sha

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


def run(args: argparse.Namespace, paths: Paths) -> dict:
    if args.command == "runs":
        from .runs import run_index

        return run_index(paths)
    if args.command == "integrity":
        from .dataset import check_integrity

        return check_integrity(paths)
    if args.command == "regression":
        from .regression import run_regression

        return run_regression(
            paths, args.output, args.baseline, scope=args.scope, register=args.register
        )
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
        return _project_status(paths)
    if args.command == "verify":
        from .verification import verify

        output = paths.output_path(args.output, "checks") if args.output else None
        return verify(paths, output, args.refit, args.run)
    if args.command == "repair-data":
        from .snapshots import repair_snapshot

        return repair_snapshot(paths, args.output, args.protocol)
    if args.command == "review-distributions":
        from .distributions import review_distributions

        return review_distributions(paths, args.protocol)
    if args.command == "review-industries":
        from .industry_sources import review_industries

        return review_industries(paths, args.protocol)

    if args.command == "repair-funds":
        from .funds import build_fund_snapshot

        return build_fund_snapshot(paths, args.protocol, args.variant)
    if args.command == "repair-industries":
        from .industries import build_industry_snapshot

        return build_industry_snapshot(paths, args.protocol, args.variant)
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

        return collect_disclosures(paths, args.output, args.retry)
    if args.command == "audit-status":
        from .status_data import audit_status_history

        return audit_status_history(paths, args.collection, args.output)
    if args.command == "refresh-data":
        from .refresh import refresh_snapshot

        return refresh_snapshot(paths, args.output, args.protocol)
    if args.command in ("replay", "backtest", "execution-bridge"):
        from .evaluation import POLICIES, execution_bridge, replay

        output = (
            args.output
            if args.command == "execution-bridge"
            else paths.output_path(args.output, "replays")
        )
        policies = ["selected_strategy", "matched_momentum"] if args.primary_only else POLICIES
        costs = sorted(set(args.cost or [10, 20, 30]))
        if args.command == "execution-bridge":
            return execution_bridge(paths, output, costs, policies)
        return replay(paths, output, costs, policies)
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
    if args.command == "data-bridge":
        from .evaluation import POLICIES, execution_bridge

        return execution_bridge(paths, args.output, [10, 20, 30], POLICIES, baseline=args.baseline)
    if args.command == "evaluate":
        from .evaluation import evaluate

        return evaluate(paths, args.run, args.output, args.cost or [10, 20, 30], args.all_policies)
    from .reporting import build_report

    return build_report(paths, args.run, reference=args.reference)
