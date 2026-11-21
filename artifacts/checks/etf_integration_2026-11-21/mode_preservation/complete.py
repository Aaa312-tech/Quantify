"""Close the registered metadata repair without relabelling prior numerical evidence."""

import re
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
protocol_file = root / "configs/protocols/fund_mode_preservation.json"
protocol = read(protocol_file)
folder = root / protocol["checks_output"]
integration = read(root / protocol["parent_protocol"])
checks = root / integration["checks_output"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
ast = read(folder / "ast_review.json")
assert ast["status"] == "PASS" and ast["source_sha256"] == source
assert "4 failed" in (folder / "before_tests.log").read_text(encoding="utf-8")
assert "4 passed" in (folder / "after_tests.log").read_text(encoding="utf-8")
tests = (folder / "checks.log").read_text(encoding="utf-8")
counts = re.search(r"(\d+) passed, (\d+) skipped", tests)
assert counts and int(counts[1]) >= 404 and int(counts[2]) == 1
assert "All checks passed!" in tests
before = protocol["before_source_sha256"]
attribution = read(checks / "attribution.json")
assert attribution["status"] == "COMPLETE" and attribution["source_sha256"] == before
records = {}
for arm in ("baseline", "coverage", "identity", "reported"):
    path = folder / arm / "rebuild.json"
    record = read(path)
    assert record["status"] == "PASS" and record["source_sha256"] == source
    assert record["training_source_sha256"] == before
    assert record["comparison"]["status"] == "PASS"
    if arm != "baseline":
        validation = read(root / integration["variants"][arm]["checks_output"] / "validation.json")
        assert validation["source_sha256"] == before
        assert validation["status"] == "PASS"
        assert record["models"]["models_reloaded"] == 48
    records[arm] = {"path": path.relative_to(root).as_posix(), "sha256": sha(path)}
save(folder / "closure.json", {
    "status": "PASS", "completed_at": now(),
    "protocol_sha256": sha(protocol_file), "source_sha256": source,
    "training_source_sha256": before, "ast_review_sha256": sha(folder / "ast_review.json"),
    "attribution_sha256": sha(checks / "attribution.json"),
    "datasets_rebuilt_exact": 4, "models_reloaded": 144,
    "rebuilds": records, "tests_passed": int(counts[1]), "tests_skipped": int(counts[2]),
    "before_tests_sha256": sha(folder / "before_tests.log"),
    "after_tests_sha256": sha(folder / "after_tests.log"), "checks_sha256": sha(folder / "checks.log"),
    "scope": "Final source differs only in dataset mode checks/preservation. All numerical paths are byte-identical or AST-identical to the actually trained and independently audited implementation. Original receipts and frozen data/model bytes remain unchanged.",
    "retained_accounts": {"parent": 1425, "retrained": 4275, "fixed_forecasts": 4275, "paper": 225, "parent_paper": 75},
    "complete_economic_exposure_certified": False, "economic_exposure_upper_bound": None,
    "superiority_assessment": "NOT_ESTABLISHED", "forward_observations_added": 0,
}, immutable=True)
print("FUND MODE PRESERVATION CLOSED", {arm: row["path"] for arm, row in records.items()}, flush=True)
