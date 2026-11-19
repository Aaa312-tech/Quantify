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
    verification = commands.add_parser("verify", help="Check frozen files, features and models")
    verification.add_argument("--output", help="New directory under artifacts/checks")
    verification.add_argument("--refit", action="store_true", help="Refit fold 1's two estimators")
    replay = commands.add_parser(
        "replay", aliases=["backtest"], help="Compare the frozen reference accounts exactly"
    )
    replay.add_argument("--output", required=True, help="New directory under artifacts/replays")
    replay.add_argument("--cost", type=int, choices=[10, 20, 30], action="append")
    replay.add_argument("--primary-only", action="store_true")
    training = commands.add_parser("train", help="Train a new run using the registered protocol")
    training.add_argument(
        "--output", required=True, help="New directory under artifacts/experiments"
    )
    training.add_argument("--first", type=int, default=1)
    training.add_argument("--last", type=int, default=11)
    training.add_argument("--include-latest", action="store_true")
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
    report = commands.add_parser(
        "report", help="Render reference results or an evaluated experiment"
    )
    report.add_argument("--run", help="Evaluated directory under artifacts/evaluations")
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

    if args.command == "status":
        path = paths.reference / "evaluation_summary.json"
        manifest = read(paths.manifest)
        if sha(path) != manifest["files"][path.relative_to(paths.root).as_posix()]:
            raise ValueError("Reference evaluation summary hash mismatch")
        result = read(path)
        return {
            "software_version": __version__,
            "strategy": paths.config["strategy"],
            "strategy_name": paths.config["strategy_name"],
            "decision": result["decision"],
            "data_cutoff": paths.config["data_cutoff"],
            "test_start": result["trade_start"],
            "test_end": result["trade_end"],
            "gates": result["gates"],
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
        return verify(paths, output, args.refit)
    if args.command in ("replay", "backtest"):
        from .evaluation import POLICIES, replay

        output = paths.output_path(args.output, "replays")
        policies = ["selected_strategy", "matched_momentum"] if args.primary_only else POLICIES
        return replay(paths, output, sorted(set(args.cost or [10, 20, 30])), policies)
    if args.command == "train":
        from .training import run_training

        output = paths.output_path(args.output, "experiments")
        return run_training(paths, output, args.first, args.last, args.include_latest)
    if args.command == "evaluate":
        from .evaluation import evaluate

        return evaluate(paths, args.run, args.output, args.cost or [10, 20, 30], args.all_policies)
    from .reporting import build_report

    return build_report(paths, args.run)
