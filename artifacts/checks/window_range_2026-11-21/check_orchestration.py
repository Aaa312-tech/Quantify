"""Capture real-calendar fitting requests without fitting or inventing observations."""

import importlib.util
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from quantfolio import training
from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
p = read(root / "configs/protocols/window_range.json")
checks = root / p["checks_output"]
implementation = read(checks / "implementation.json")
assert implementation["status"] == "PASS"
assert sha(paths.bundle) == p["bundle_sha256"]
baseline_sources = root / ".git/window_range_baseline_sources"
assert not baseline_sources.exists()
baseline_sources.mkdir()
for name, expected in p["baseline_source_sha256"].items():
    data = subprocess.check_output(
        ["git", "show", p["baseline_commit"] + ":src/quantfolio/" + name]
    )
    file = baseline_sources / name
    file.write_bytes(data)
    assert sha(file) == expected
spec = importlib.util.spec_from_file_location(
    "quantfolio._range_before", baseline_sources / "training.py"
)
before = importlib.util.module_from_spec(spec)
spec.loader.exec_module(before)
traces = {}
for name, module in (("before", before), ("after", training)):
    output = checks / "orchestration" / name
    calls = []

    def capture(bundle, fold, folder, identity, **options):
        calls.append(
            {
                "fold": deepcopy(fold),
                "path": folder.relative_to(output).as_posix(),
                "identity": identity,
                "options": options,
            }
        )

    # Hash guards and actual calendar loading remain live. Only numerical fitting
    # is intercepted, and these diagnostic files live outside experiments.
    with patch.object(module, "run_window", capture):
        result = module.run_training(paths, output, include_latest=True)
    registration = read(output / "protocol.json")
    assert all(call["identity"] == result["identity"] for call in calls)
    assert result["status"] == "COMPLETE" and result["folds_completed"] == p["current_folds"]
    assert registration["specification"]["selected_folds"] == [1, p["current_folds"]]
    expected_sources = (
        p["baseline_source_sha256"] if name == "before" else implementation["source_sha256"]
    )
    assert registration["specification"]["source_sha256"] == expected_sources
    traces[name] = {"calls": calls, "registration": registration, "status": result}
before_spec = deepcopy(traces["before"]["registration"]["specification"])
after_spec = deepcopy(traces["after"]["registration"]["specification"])
before_spec.pop("source_sha256")
after_spec.pop("source_sha256")
assert before_spec == after_spec


def without_identity(rows):
    return [{k: v for k, v in row.items() if k != "identity"} for row in rows]


assert without_identity(traces["before"]["calls"]) == without_identity(traces["after"]["calls"])
assert len(traces["after"]["calls"]) == p["current_folds"] + 1 == 12
original = read(root / p["baseline_run"] / "protocol.json")
assert original == {**traces["before"]["registration"], "registered_at": original["registered_at"]}
assert len(load_bundle(paths)["calendar"]) == p["current_sessions"]
save(
    checks / "orchestration.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "source_sha256": implementation["source_sha256"],
        "baseline_source_sha256": p["baseline_source_sha256"],
        "bundle_sha256": sha(paths.bundle),
        "calendar_sessions": p["current_sessions"],
        "historical_window_calls": p["current_folds"],
        "latest_window_calls": 1,
        "calls_exact_except_honest_source_identity": True,
        "registered_specification_exact_except_sources": True,
        "original_protocol_reproduced": True,
        "traces": traces,
        "refits": 0,
        "future_prices_created": False,
        "active_inputs_changed": False,
        "scope": "NON_FITTING_ORCHESTRATION_TRACE. Actual frozen calendar and normal integrity checks; run_window calls captured only. Diagnostic COMPLETE statuses mean scheduling completion, not fitted models, and cannot be passed as experiment paths. No new estimator or forecast was created.",
    },
    immutable=True,
)
print(
    "REAL CALENDAR ORCHESTRATION EXACT",
    len(traces["after"]["calls"]),
    "calls; refits 0",
    flush=True,
)
