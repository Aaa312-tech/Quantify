"""Construct the registered snapshot after old-input and independent action checks."""

import logging
from pathlib import Path

from quantfolio.dataset import load_snapshot
from quantfolio.settings import Paths
from quantfolio.snapshots import repair_snapshot
from quantfolio.storage import digest, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
checks = root / p["checks_output"]
require(
    read(checks / "compatibility/validation.json")["status"] == "PASS",
    "Old-input compatibility incomplete",
)
scope = read(checks / "action_scope.json")
require(scope["status"] == "PASS_ACTION_SCOPE_ONLY", "Independent actions incomplete")
require(not (root / p["training_output"]).exists(), "Training started before numeric acceptance")
audit = repair_snapshot(paths, p["snapshot_output"], protocol=pfile)
snapshot = root / p["snapshot_output"] / "bundle.joblib"
bundle = load_snapshot(paths, snapshot, p["source_data_cutoff"])
require(
    bundle["actions"] == read(checks / "expected_actions.json"),
    "Independent corrected actions differ",
)
require(digest(bundle["actions"]) == scope["actions_sha256"], "Corrected action identity differs")
require(
    sha(paths.configuration_path) == p["active_configuration_sha256"],
    "Active configuration changed",
)
save(
    checks / "snapshot_construction.json",
    {
        "status": "BUILT_PENDING_INDEPENDENT_NUMERIC_ACCEPTANCE",
        "completed_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "bundle_sha256": sha(snapshot),
        "actions_sha256": digest(bundle["actions"]),
        "audit": audit,
        "independent_actions_exact": True,
        "activated": False,
        "next_step": "Reconcile all independently rebuilt features, ranks and labels; freeze fitting expectations before fitting.",
    },
    immutable=True,
)
print("SNAPSHOT_BUILT_PENDING_NUMERIC_ACCEPTANCE", sha(snapshot), flush=True)
