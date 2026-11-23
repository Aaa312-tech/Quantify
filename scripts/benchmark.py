"""Repeat the registered E0 workloads in fresh processes; never fit a model.

Both implementations use the standard regression loader and the same input inventory.
Elapsed time excludes that loader's setup. Peak memory includes the common loader
and both imported implementations, so it is not a standalone package-import measure.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import logging
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import quantfolio
from quantfolio.regression import (
    baseline_package,
    check_inputs,
    read_baseline,
    replay_history,
    runtime_sources,
)
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

TASKS = ("integrity", "models", "historical_accounts", "paper")


def peak_memory():
    """OS-reported process peak, in bytes; do not confuse commit charge with working set.

    Windows fields: https://learn.microsoft.com/windows/win32/api/psapi/ns-psapi-process_memory_counters
    """
    if os.name != "nt":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1 if sys.platform == "darwin" else 1024
        )
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = []
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize


def run_task(paths, profile, module, task, output):
    selected_paths = module.settings.Paths(paths.root, paths.config_file)
    if task == "integrity":
        return module.dataset.check_integrity(selected_paths)
    if task == "models":
        result = module.verification.verify(
            selected_paths, output / "models", run=profile["training"]
        )
        if result["models_reloaded"] != profile["expected"]["models"] or any(
            item["max_error"] != 0 for item in result["models"]
        ):
            raise ValueError("Model inventory or exact prediction check failed")
        return result
    if task == "paper":
        result = module.verification.verify_paper(selected_paths, output / "paper")
        if (
            result["accounts"] != profile["expected"]["paper_accounts"]
            or result["sessions"] != profile["expected"]["paper_sessions"]
        ):
            raise ValueError("Paper replay inventory differs")
        return result
    bundle = module.dataset.load_bundle(selected_paths)
    run = paths.root / profile["training"]
    status, folds = module.evaluation.experiment_folds(selected_paths, bundle, run)
    maps, schedule, _, start, end = module.evaluation.load_predictions(
        bundle, run, folds, status["identity"]
    )
    source = paths.root / profile["evaluation"]
    reference = read(source / "evaluation_summary.json")
    with tempfile.TemporaryDirectory(prefix="quantfolio-benchmark-") as temporary:
        result = replay_history(
            bundle,
            maps,
            schedule,
            start,
            end,
            reference,
            source,
            Path(temporary),
            module.evaluation,
            "full",
            paths.config.get("turnover_policy", "legacy"),
        )
    if (
        result["accounts"] != profile["expected"]["historical_accounts"]
        or result["ledger_files"] != profile["expected"]["ledger_files"]
    ):
        raise ValueError("Historical replay inventory differs")
    return result


def worker(paths, output, baseline, implementation, task, iteration, manifest_hash):
    profile = read_baseline(paths)
    check_inputs(paths, profile)
    sources = runtime_sources()
    if sha(paths.manifest) != manifest_hash:
        raise ValueError("Benchmark archive inventory changed")
    with baseline_package(paths, profile, baseline) as (previous, _):
        module = previous if implementation == "baseline" else quantfolio
        started = now()
        cpu_start, start = time.process_time(), time.perf_counter()
        result = run_task(paths, profile, module, task, output)
        elapsed, cpu = time.perf_counter() - start, time.process_time() - cpu_start
        peak = peak_memory()
    if runtime_sources() != sources or sha(paths.manifest) != manifest_hash:
        raise ValueError("Benchmark dependencies changed during a sample")
    check_inputs(paths, profile)
    save(output / "validation.json", result, immutable=True)
    sample = {
        "status": "PASS",
        "task": task,
        "implementation": implementation,
        "iteration": iteration,
        "started_at": started,
        "completed_at": now(),
        "elapsed_seconds": elapsed,
        "cpu_seconds": cpu,
        "peak_memory_bytes": peak,
        "manifest_sha256": manifest_hash,
        "source_sha256": sources,
        "validation_sha256": sha(output / "validation.json"),
        "new_model_fits": 0,
    }
    save(output / "sample.json", sample, immutable=True)
    return sample


def summarize(samples, tasks, repeats):
    expected = {
        (task, implementation, index)
        for task in tasks
        for implementation in ("baseline", "current")
        for index in range(1, repeats + 1)
    }
    identities = [(item["task"], item["implementation"], item["iteration"]) for item in samples]
    if (
        len(identities) != len(set(identities))
        or set(identities) != expected
        or any(item["status"] != "PASS" for item in samples)
    ):
        raise ValueError("Incomplete or duplicate benchmark samples")
    rows = []
    for task in tasks:
        for metric in ("elapsed_seconds", "peak_memory_bytes"):
            values = {
                implementation: [
                    item[metric]
                    for item in samples
                    if item["task"] == task and item["implementation"] == implementation
                ]
                for implementation in ("baseline", "current")
            }
            if any(
                not all(math.isfinite(value) and value > 0 for value in series)
                for series in values.values()
            ):
                raise ValueError("Benchmark measurements must be positive")
            medians = {key: statistics.median(series) for key, series in values.items()}
            change = medians["current"] / medians["baseline"] - 1
            rows.append(
                {
                    "task": task,
                    "metric": metric,
                    "samples": values,
                    "medians": medians,
                    "relative_change": change,
                    "review_required": medians["current"] * 10 > medians["baseline"] * 11,
                }
            )
    return rows


def benchmark(paths, output, baseline, repeats, tasks):
    output = paths.output_path(output, "checks")
    profile = read_baseline(paths)
    check_inputs(paths, profile)
    manifest_hash = sha(paths.manifest)
    script = Path(__file__).resolve()
    header = {
        "schema_version": 1,
        "started_at": now(),
        "baseline_commit": baseline,
        "repeats": repeats,
        "tasks": tasks,
        "scope_sha256": sha(paths.root / "configs/regression.toml"),
        "financial_inputs_sha256": profile["inputs_sha256"],
        "manifest_sha256": manifest_hash,
        "source_sha256": runtime_sources(),
        "baseline_source_sha256": profile["source_sha256"],
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=paths.root, text=True
        ).strip(),
        "script_sha256": sha(script),
        "environment_locks_sha256": {
            name: sha(paths.root / name)
            for name in ("uv.lock", "environments/research-win-64.lock", "pyproject.toml")
        },
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "scipy", "scikit-learn", "joblib")
        },
        "method": "Fresh process per sample; alternate AB/BA order; common regression loader; OS process peak includes loader; no concurrent local benchmark workers",
        "archive_scope": "Same current registered archive for both implementations; E0 financial inputs unchanged",
        "new_model_fits": 0,
    }
    save(output / "run.json", header, immutable=True)
    (output / "logs").mkdir()
    samples = []
    try:
        for task in tasks:
            for index in range(1, repeats + 1):
                order = ("baseline", "current") if index % 2 else ("current", "baseline")
                for implementation in order:
                    folder = output / "samples" / task / f"{implementation}_{index}"
                    print(
                        json.dumps(
                            {
                                "status": "START",
                                "task": task,
                                "implementation": implementation,
                                "iteration": index,
                            }
                        ),
                        flush=True,
                    )
                    command = [
                        sys.executable,
                        "-B",
                        str(script),
                        "--project",
                        str(paths.root),
                        "--output",
                        str(folder),
                        "--baseline",
                        baseline,
                        "--worker",
                        implementation,
                        "--task",
                        task,
                        "--iteration",
                        str(index),
                        "--manifest-sha256",
                        manifest_hash,
                    ]
                    with (output / "logs" / f"{task}_{implementation}_{index}.log").open(
                        "x", encoding="utf-8"
                    ) as stream:
                        subprocess.run(
                            command,
                            cwd=paths.root,
                            stdout=stream,
                            stderr=subprocess.STDOUT,
                            check=True,
                        )
                    sample = read(folder / "sample.json")
                    if (
                        sample["source_sha256"] != header["source_sha256"]
                        or sample["manifest_sha256"] != manifest_hash
                        or sample["validation_sha256"] != sha(folder / "validation.json")
                    ):
                        raise ValueError("Samples used different sources or archive inventories")
                    if any(
                        sha(paths.root / name) != checksum
                        for name, checksum in header["environment_locks_sha256"].items()
                    ):
                        raise ValueError("Benchmark environment locks changed")
                    samples.append(sample)
                    print(
                        json.dumps(
                            {
                                key: sample[key]
                                for key in (
                                    "status",
                                    "task",
                                    "implementation",
                                    "iteration",
                                    "elapsed_seconds",
                                    "peak_memory_bytes",
                                )
                            }
                        ),
                        flush=True,
                    )
        if sha(script) != header["script_sha256"]:
            raise ValueError("Benchmark implementation changed")
        check_inputs(paths, profile)
        comparisons = summarize(samples, tasks, repeats)
        result = {
            "status": "REVIEW_REQUIRED"
            if any(row["review_required"] for row in comparisons)
            else "PASS",
            "completed_at": now(),
            "sample_count": len(samples),
            "comparisons": comparisons,
            "new_model_fits": 0,
        }
    except BaseException as error:
        save(
            output / "result.json",
            {"status": "FAIL", "error": str(error), "completed_samples": len(samples)},
            immutable=True,
        )
        raise
    save(output / "result.json", result, immutable=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--tasks", nargs="+", choices=TASKS, default=list(TASKS))
    parser.add_argument("--worker", choices=("baseline", "current"), help=argparse.SUPPRESS)
    parser.add_argument("--task", choices=TASKS, help=argparse.SUPPRESS)
    parser.add_argument("--iteration", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--manifest-sha256", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 3 or len(args.tasks) != len(set(args.tasks)):
        parser.error("Use at least three repetitions and distinct workloads")
    if args.worker and (args.task is None or args.iteration is None or not args.manifest_sha256):
        parser.error("A worker requires its workload, repetition and fixed manifest hash")
    paths = Paths(args.project)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.worker:
        output = paths.output_path(args.output, "checks")
        result = worker(
            paths,
            output,
            args.baseline,
            args.worker,
            args.task,
            args.iteration,
            args.manifest_sha256,
        )
    else:
        result = benchmark(paths, args.output, args.baseline, args.repeats, args.tasks)
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
