"""Small, explicit command-line interface for offline research and maintenance."""

from __future__ import annotations

import argparse
import json
import logging
import os

from .settings import Paths, project_root
from .storage import read


def main(argv: list[str] | None = None) -> None:
    """Parse a research command; never connect to a broker or messaging application."""
    parser = argparse.ArgumentParser(prog="quantfolio", description=__doc__)
    parser.add_argument("--project", help="Project directory (or QUANTFOLIO_ROOT)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show retained results and their validation status")
    verification = commands.add_parser(
        "verify", help="Verify inputs, all models and causal features"
    )
    verification.add_argument("--output", help="New directory under artifacts/checks")
    verification.add_argument(
        "--refit", action="store_true", help="Also refit fold 1's two estimators"
    )
    backtest = commands.add_parser("backtest", help="Replay and compare frozen outer ledgers")
    backtest.add_argument("--output", required=True, help="New directory under artifacts/replays")
    backtest.add_argument("--cost", type=int, choices=[10, 20, 30], action="append")
    backtest.add_argument("--primary-only", action="store_true")
    training = commands.add_parser("train", help="Fit the same V6 protocol into a new experiment")
    training.add_argument(
        "--output", required=True, help="New directory under artifacts/experiments"
    )
    training.add_argument("--first", type=int, default=1)
    training.add_argument("--last", type=int, default=11)
    training.add_argument("--include-latest", action="store_true")
    commands.add_parser("report", help="Build the local report from retained evidence")
    args = parser.parse_args(argv)
    paths = Paths(project_root(args.project))
    os.environ["QUANTFOLIO_ROOT"] = str(paths.root)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "status":
        result = read(paths.reference / "evaluation_summary.json")
        result = {
            "version": "V6",
            "decision": result["decision"],
            "data_cutoff": "2026-11-18",
            "test_start": result["trade_start"],
            "test_end": result["trade_end"],
            "gates": result["gates"],
            "continuous": {
                key: {
                    name: values["v6_selected"]["summary"][name]
                    for name in ("return", "max_drawdown", "trades", "fees")
                }
                for key, values in result["continuous"].items()
            },
        }
    elif args.command == "verify":
        from .verification import verify

        if args.refit and not args.output:
            parser.error("--refit requires --output artifacts/checks/<new-name>")
        output = paths.new_output(args.output, "checks") if args.output else None
        result = verify(paths, output, args.refit)
    elif args.command == "backtest":
        from .evaluation import POLICIES, replay

        output = paths.new_output(args.output, "replays")
        policies = ["v6_selected", "matched_momentum"] if args.primary_only else POLICIES
        result = replay(paths, output, sorted(set(args.cost or [10, 20, 30])), policies)
    elif args.command == "train":
        from .training import run_training

        output = paths.new_output(args.output, "experiments")
        result = run_training(paths, output, args.first, args.last, args.include_latest)
    else:
        from .reporting import build_report

        result = build_report(paths)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
