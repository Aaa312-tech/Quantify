"""Compare report bytes with the pre-decomposition implementation in a temporary project."""

import argparse
import hashlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

from quantfolio.settings import Paths
from quantfolio.status_data import load_status_review
from quantfolio.storage import now, read, save, sha

BASELINE = "5188d2be846a471382e8ab46fffd8997bba3f259"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    paths = Paths(root)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    original_source = subprocess.check_output(
        ["git", "show", BASELINE + ":src/quantfolio/reporting.py"], cwd=root
    )
    original = types.ModuleType("quantfolio._report_before_decomposition")
    original.__file__ = "git:" + BASELINE + ":src/quantfolio/reporting.py"
    exec(compile(original_source, original.__file__, "exec"), original.__dict__)
    spec = importlib.util.spec_from_file_location("quantfolio._report_candidate", args.candidate)
    candidate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = candidate
    spec.loader.exec_module(candidate)
    reviewed_status = load_status_review(paths)
    report = {
        "status": "FAIL", "started_at": now(), "baseline_commit": BASELINE,
        "baseline_source_sha256": hashlib.sha256(original_source).hexdigest(),
        "candidate_source_sha256": sha(args.candidate), "script_sha256": sha(__file__),
        "scope": "Frozen current and reference report inputs; no model fitting or data rebuild",
        "cases": [],
    }
    try:
        for name, reference, explicit in (
            ("current", False, False), ("explicit", False, True), ("reference", True, False)
        ):
            source = paths.reference if reference else root / paths.config["evaluation"]
            summary = read(source / "evaluation_summary.json")
            cost = min(summary["results"], key=lambda key: int(key[:-3]))
            inputs = [source / "evaluation_summary.json"]
            for policy in (summary["primary_policy"], "matched_momentum"):
                filename = paths.archive.get("policies", {}).get(policy, policy) if reference else policy
                inputs.append(source / f"evaluation/continuous/{cost}/{filename}.json.gz")
            if reference:
                inputs.append(paths.benchmark)
            with tempfile.TemporaryDirectory(prefix="quantfolio-report-") as directory:
                temporary = Paths(Path(directory))
                for file in (paths.configuration_path, root / paths.config["archive"], *inputs):
                    target = temporary.root / file.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, target)
                input_hashes = {file.relative_to(root).as_posix(): sha(file) for file in inputs}
                save(temporary.manifest, {"files": input_hashes}, immutable=True)
                run = paths.config["evaluation"] if explicit else None
                with patch.object(original, "load_status_review", return_value=reviewed_status):
                    before = original.build_report(temporary, run, reference=reference)
                destination = Path(before["report"]).parent
                expected = {file.name: file.read_bytes() for file in destination.iterdir()}
                with patch.object(candidate, "load_status_review", return_value=reviewed_status):
                    after = candidate.build_report(temporary, run, reference=reference)
                actual = {file.name: file.read_bytes() for file in destination.iterdir()}
                assert before == after and expected == actual, name
                if name == "current":
                    shutil.copyfile(destination / "equity_curve.png", args.output / "equity_curve.png")
                report["cases"].append({
                    "name": name, "inputs_sha256": input_hashes, "bytes_equal": True,
                    "outputs_sha256": {key: hashlib.sha256(value).hexdigest() for key, value in actual.items()},
                })
        report["status"] = "PASS"
    finally:
        report["completed_at"] = now()
        save(args.output / "result.json", report, immutable=True)
    print(report["status"], len(report["cases"]), "HTML and PNG pairs equal")


if __name__ == "__main__":
    main()
