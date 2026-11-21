"""Run the frozen whole-inventory source review without changing active research inputs."""

import logging
from pathlib import Path

from quantfolio.distributions import review_distributions
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
paths = Paths(root)
protocol_file = root / "configs/protocols/distribution_terms.json"
p = read(protocol_file)
checks = root / p["checks_output"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
before = p["active_state"]["source_sha256"]
assert source.keys() == before.keys()
assert {name for name in source if source[name] != before[name]} == {"__init__.py", "distributions.py"}
assert "427 passed, 1 skipped" in (checks / "local_checks.log").read_text(encoding="utf-8")
assert "7 failed" in (checks / "before_tests.log").read_text(encoding="utf-8")
assert sha(paths.configuration_path) == p["active_state"]["configuration_sha256"]
assert sha(paths.bundle) == p["active_state"]["bundle_sha256"]
assert sha(root / p["active_state"]["plan"]) == p["active_state"]["plan_sha256"]
archive = checks / "extractor.py"
assert not archive.exists()
archive.write_bytes((root / "src/quantfolio/distributions.py").read_bytes())
save(checks / "implementation.json", {
    "status": "SOURCE_EXTRACTOR_TESTED", "registered_at": now(),
    "registration_commit": "e02fde5bb2c2f76a9ed084712f555aa3200eb03c",
    "protocol_sha256": sha(protocol_file), "source_sha256": source,
    "before_source_sha256": before, "extractor_sha256": sha(archive),
    "tests_sha256": sha(checks / "local_checks.log"),
    "regression_tests_sha256": sha(root / "tests/test_distribution_terms.py"),
    "scope": "Only source-term extraction and package version changed; training, allocation, execution, risk, account, data, plan and model paths remain byte-identical.",
}, immutable=True)
manifest = read(paths.manifest)
for file in checks.rglob("*"):
    if not file.is_file() or file.suffix == ".pyc" or any(part in {"__pycache__", ".pytest_cache"} for part in file.parts):
        continue
    relative, value = file.relative_to(root).as_posix(), sha(file)
    assert relative not in manifest["files"] or manifest["files"][relative] == value
    manifest["files"][relative] = value
save(paths.manifest, manifest)
result = review_distributions(paths, protocol_file.relative_to(root).as_posix())
assert result["events"] == 1839 and result["facts_applied"] is False
assert source == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert sha(paths.configuration_path) == p["active_state"]["configuration_sha256"]
assert sha(paths.bundle) == p["active_state"]["bundle_sha256"]
assert sha(root / p["active_state"]["plan"]) == p["active_state"]["plan_sha256"]
print("WHOLE-INVENTORY SOURCE EXTRACTION COMPLETE", result, flush=True)
