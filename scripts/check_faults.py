"""Confirm that existing tests detect four financial and input-integrity defects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

FAULTS = (
    (
        "future_price",
        "market.py",
        'o = float(raw.at[date, "open"])',
        'o = float(raw.at[date, "close"])',
        "tests/portfolio/test_opening_causality.py::test_later_bar_fields_do_not_select_opening_orders",
    ),
    (
        "currency_unit",
        "ledger.py",
        "Decimal(str(x)) * 100",
        "Decimal(str(x)) * 1",
        "tests/portfolio/test_opening_causality.py::test_affordable_quantity_reserves_cost_and_ledger_rejects_overspend",
    ),
    (
        "missing_trade",
        "ledger.py",
        "self.trades.append(",
        "[].append(",
        "tests/portfolio/test_opening_causality.py::test_slippage_is_charged_once_without_changing_flat_market_quote",
    ),
    (
        "input_hash",
        "regression.py",
        "if sha(path) != expected:",
        "if False:",
        "tests/workflows/test_regression.py::test_registered_file_hash_is_checked",
    ),
)


def run_tests(root: Path, source: Path, nodes: list[str], output: Path) -> tuple[int, int, int]:
    environment = {**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"}
    with output.with_suffix(".log").open("xb") as log:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "-q",
                *nodes,
                "--junitxml=" + str(output.with_suffix(".xml")),
            ],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=log,
            check=False,
        )
    suites = ET.parse(output.with_suffix(".xml")).getroot().iter("testsuite")
    totals = [suite.attrib for suite in suites]
    return (
        result.returncode,
        sum(int(item["failures"]) for item in totals),
        sum(int(item["errors"]) for item in totals),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = root / "src"
    report = {
        "status": "FAIL",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
        "faults": [],
    }
    try:
        code, failures, errors = run_tests(
            root, source, [item[-1] for item in FAULTS], output / "control"
        )
        if (code, failures, errors) != (0, 0, 0):
            raise ValueError("The unchanged control tests must pass before injecting faults")
        with tempfile.TemporaryDirectory(prefix="quantfolio-faults-") as temporary:
            folder = Path(temporary)
            for name, module, before, after, node in FAULTS:
                candidate = folder / name
                shutil.copytree(
                    source / "quantfolio",
                    candidate / "quantfolio",
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
                path = candidate / "quantfolio" / module
                original = path.read_bytes()
                text = original.decode("utf-8")
                if text.count(before) != 1:
                    raise ValueError(f"Fault location is no longer unique: {name}")
                path.write_text(text.replace(before, after), encoding="utf-8", newline="\n")
                code, failures, errors = run_tests(root, candidate, [node], output / name)
                detected = code == 1 and failures > 0 and errors == 0
                report["faults"].append(
                    {
                        "name": name,
                        "module": module,
                        "source_sha256": hashlib.sha256(original).hexdigest(),
                        "detected": detected,
                        "returncode": code,
                        "test_failures": failures,
                        "collection_errors": errors,
                    }
                )
                if not detected:
                    raise ValueError(f"The defect was not detected by its behavioral test: {name}")
        report["status"] = "PASS"
    finally:
        (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
