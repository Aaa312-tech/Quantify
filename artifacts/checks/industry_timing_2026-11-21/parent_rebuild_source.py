import logging
from pathlib import Path

from quantfolio.dataset import check_integrity, load_bundle
from quantfolio.preprocessing import build_dataset, compare_history
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
paths = Paths(root)
protocol_path = root / "configs/protocols/industry_timing.json"
protocol = read(protocol_path)
assert sha(paths.bundle) == protocol["source_bundle_sha256"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
integrity = check_integrity(paths)
bundle = load_bundle(paths)
rebuilt = build_dataset(bundle["meta"], bundle["raws"], bundle["actions"], opening_status=bundle["opening_status"])
result = compare_history(bundle, rebuilt)
assert bundle["actions"] == rebuilt["actions"]
assert bundle["opening_status"] == rebuilt["opening_status"]
assert source == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
save(root / protocol["checks_output"] / "parent_rebuild.json", {
    **result, "checked_at": now(), "registration_commit": "6b31ec98d341f505ff9c45a6e9688bb8bea010ca",
    "source_sha256": source, "data_sha256": sha(paths.bundle), "protocol_sha256": sha(protocol_path),
    "integrity": integrity,
}, immutable=True)
print("PARENT EXACT REBUILD PASSED", result, flush=True)
