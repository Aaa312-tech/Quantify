"""Rebuild frozen inputs and reload existing models after the metadata-only patch."""

import logging
import sys
from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.preprocessing import build_dataset, compare_history
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.verification import verify

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
protocol_file = root / "configs/protocols/fund_mode_preservation.json"
protocol = read(protocol_file)
integration = read(root / protocol["parent_protocol"])
folder = root / protocol["checks_output"]
arm = sys.argv[1]
assert arm in {"baseline", "coverage", "identity", "reported"}
ast_review = read(folder / "ast_review.json")
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert ast_review["status"] == "PASS" and source == ast_review["source_sha256"]
attribution_file = root / integration["checks_output"] / "attribution.json"
attribution = read(attribution_file)
assert attribution["status"] == "COMPLETE"
assert attribution["source_sha256"] == protocol["before_source_sha256"]
assert sha(root / protocol["parent_protocol"]) == protocol["parent_protocol_sha256"]
setting = integration["variants"].get(arm)
paths = Paths(root, setting["configuration"]) if setting else Paths(root)
active_configuration = sha(root / "configs/project.toml")
bundle = load_bundle(paths)
data_hash = sha(paths.bundle)
assert data_hash == (integration["source_bundle_sha256"] if arm == "baseline" else read(root / setting["checks_output"] / "validation.json")["data_sha256"])
before_mode = bundle.get("fund_mode")
assert before_mode == (None if arm == "baseline" else arm)
rebuilt = build_dataset(bundle["meta"], bundle["raws"], bundle["actions"], opening_status=bundle.get("opening_status"))
comparison = compare_history(bundle, rebuilt)
assert bundle["actions"] == rebuilt["actions"]
assert bundle.get("opening_status") == rebuilt.get("opening_status")
assert rebuilt.get("fund_mode") == before_mode
models = None
if setting:
    validation = read(root / setting["checks_output"] / "validation.json")
    assert validation["status"] == "PASS" and validation["source_sha256"] == protocol["before_source_sha256"]
    models = verify(paths, folder / arm / "models", run=root / setting["training_output"])
    assert models["status"] == "PASS" and models["models_reloaded"] == 48
    assert all(item["max_error"] == 0 for item in models["models"])
assert source == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert sha(paths.bundle) == data_hash
assert sha(root / "configs/project.toml") == active_configuration
save(folder / arm / "rebuild.json", {
    "status": "PASS", "completed_at": now(), "arm": arm,
    "protocol_sha256": sha(protocol_file), "source_sha256": source,
    "training_source_sha256": protocol["before_source_sha256"],
    "ast_review_sha256": sha(folder / "ast_review.json"),
    "attribution_sha256": sha(attribution_file), "data_sha256": data_hash,
    "fund_mode": before_mode, "comparison": comparison,
    "actions_exact": True, "opening_status_exact": True,
    "frozen_dataset_unchanged": True, "models": models,
    "scope": "Ordinary guarded model reloads and exact full input rebuilding; previously executed training/account receipts retain their original source identity.",
}, immutable=True)
print("MODE REBUILD PASSED", arm, comparison, flush=True)
