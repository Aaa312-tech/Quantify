"""Inspect future calendar boundaries without inventing prices or running future accounts."""

import ast
import inspect
from pathlib import Path

import pandas as pd

from quantfolio.dataset import load_snapshot, walk_forward_folds
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.training import run_training

root = Path.cwd()
p = read(root / "configs/protocols/companion_payments.json")
checks = root / p["checks_output"]
bundle_file = root / p["snapshot_output"] / "bundle.joblib"
bundle = load_snapshot(Paths(root), bundle_file, p["source_data_cutoff"])
calendar_file = root / "configs/trading_calendar_2026.json"
registered = read(calendar_file)
future = [day for day in registered["sessions"] if day > p["source_data_cutoff"]]
calendar = bundle["calendar"].append(pd.to_datetime(future))
assert calendar.is_unique and calendar.is_monotonic_increasing
default = inspect.signature(run_training).parameters["last"].default
cli = ast.parse((root / "src/quantfolio/cli.py").read_text(encoding="utf-8"))
defaults = [
    keyword.value.value
    for node in ast.walk(cli)
    if isinstance(node, ast.Call)
    and node.args
    and isinstance(node.args[0], ast.Constant)
    and node.args[0].value == "--last"
    for keyword in node.keywords
    if keyword.arg == "default" and isinstance(keyword.value, ast.Constant)
]
assert defaults == [default] == [11]
current = len(walk_forward_folds(bundle["calendar"]))
assert current == 11
boundaries, prior = [], current
for length in range(len(bundle["calendar"]) + 1, len(calendar) + 1):
    folds = walk_forward_folds(calendar[:length])
    if len(folds) == prior:
        continue
    boundaries.append(
        {
            "conditional_data_cutoff": str(calendar[length - 1].date()),
            "observed_session_count_required": length,
            "complete_windows": len(folds),
            "default_last": default,
            "default_status": "COMPLETE" if default == len(folds) else "PARTIAL",
            "last_test_start": str(calendar[folds[-1]["test_start"]].date()),
            "last_test_end": str(calendar[folds[-1]["test_end"]].date()),
        }
    )
    prior = len(folds)
save(
    checks / "window_growth.json",
    {
        "status": "MAINTENANCE_FINDING",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "bundle_sha256": sha(bundle_file),
        "calendar_sha256": sha(calendar_file),
        "source_sha256": {
            name: sha(root / "src/quantfolio" / name)
            for name in ("dataset.py", "training.py", "cli.py")
        },
        "current_sessions": len(bundle["calendar"]),
        "current_windows": current,
        "default_last": default,
        "boundaries": boundaries,
        "runtime_changed": False,
        "future_prices_created": False,
        "future_training_performed": False,
        "scope": "Calendar-only conditional calculation. If every subsequent scheduled session acquires reviewed data, the fixed last=11 default eventually yields PARTIAL instead of a complete run. This does not assert future quotes, fills or returns and does not affect the current 11-window snapshot. Register a separate maintenance change before adopting a dynamic default; preserve explicit partial ranges and current model/account parity.",
    },
    immutable=True,
)
print("CALENDAR GROWTH FINDING", boundaries, flush=True)
