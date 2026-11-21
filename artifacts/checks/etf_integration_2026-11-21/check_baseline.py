"""Verify the entire pre-registration frozen inventory without rewriting any file."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd().resolve()
protocol = read(root / "configs/protocols/etf_integration.json")
raw = subprocess.check_output(
    ["git", "show", protocol["baseline_commit"] + ":configs/integrity_manifest.json"]
)
import hashlib

assert hashlib.sha256(raw).hexdigest() == protocol["baseline_manifest_sha256"]
baseline = json.loads(raw)
assert len(baseline["files"]) == protocol["baseline_frozen_files"] == 29762


def check(item):
    name, expected = item
    file = (root / name).resolve()
    if not file.is_relative_to(root) or not file.is_file() or sha(file) != expected:
        return name
    return None


with ThreadPoolExecutor(max_workers=4) as pool:
    failures = [item for item in pool.map(check, baseline["files"].items()) if item is not None]
save(
    root / protocol["checks_output"] / "baseline_integrity.json",
    {
        "status": "PASS" if not failures else "FAIL",
        "completed_at": now(),
        "baseline_commit": protocol["baseline_commit"],
        "files": len(baseline["files"]),
        "baseline_manifest_sha256": protocol["baseline_manifest_sha256"],
        "failures": failures,
    },
    immutable=True,
)
assert not failures
print("ALL BASELINE FROZEN FILES EXACT", len(baseline["files"]), flush=True)
