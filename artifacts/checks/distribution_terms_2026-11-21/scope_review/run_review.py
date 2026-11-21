"""Repeat the full registered source inventory after preserving amount-role failures."""

import logging
from pathlib import Path

from quantfolio.distributions import review_distributions
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
paths = Paths(root)
protocol_file = root / "configs/protocols/distribution_term_scope.json"
p = read(protocol_file)
checks = root / p["checks_output"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
before = p["active_state"]["source_sha256"]
assert source.keys() == before.keys()
assert {name for name in source if source[name] != before[name]} == {"__init__.py", "distributions.py"}
assert "4 failed, 2 passed" in (checks / "before_tests.log").read_text(encoding="utf-8")
archive = checks / "extractor.py"
assert not archive.exists()
archive.write_bytes((root / "src/quantfolio/distributions.py").read_bytes())
save(checks / "implementation.json", {
    "status": "SOURCE_SCOPE_CORRECTED", "registered_at": now(),
    "protocol_sha256": sha(protocol_file), "source_sha256": source,
    "before_source_sha256": before, "extractor_sha256": sha(archive),
    "regression_tests_sha256": sha(root / "tests/test_distribution_scope.py"),
    "facts_applied": False,
}, immutable=True)
manifest = read(paths.manifest)
for file in checks.rglob("*"):
    if not file.is_file() or file.suffix == ".pyc" or "__pycache__" in file.parts:
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
old = read(root / p["previous_review"]["path"])["facts"]
new = {(r["symbol"], r["ex_date"]): r for r in read(root / p["review"])["facts"]}
lost = []
for row in old:
    for field, value in row["verified_fields"].items():
        if new.get((row["symbol"], row["ex_date"]), {}).get("verified_fields", {}).get(field) != value:
            lost.append({"symbol": row["symbol"], "ex_date": row["ex_date"], "field": field})
save(checks / "preservation.json", {
    "status": "PASS" if not lost else "FAIL", "checked_at": now(),
    "previous_facts": len(old), "new_facts": len(new), "lost_fields": lost,
    "review_sha256": sha(root / p["review"]), "facts_applied": False,
}, immutable=True)
assert not lost, lost
print("WHOLE-INVENTORY SCOPE REVIEW COMPLETE", result, flush=True)
