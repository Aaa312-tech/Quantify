"""Check preregistered source examples and unchanged financial acceptance functions."""

import ast
import hashlib
import subprocess
from decimal import Decimal
from pathlib import Path

from quantfolio.distributions import extract_terms, notice_role
from quantfolio.storage import load_gzip, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


base = Path("artifacts/checks/distribution_residuals_2026-11-22")
protocol = read("configs/protocols/distribution_layouts.json")
parent = read(protocol["parent_protocol"]["path"])
source = Path("src/quantfolio/distributions.py")
old_bytes = subprocess.run(
    ["git", "show", "34968f3f:src/quantfolio/distributions.py"], capture_output=True, check=True
).stdout
require(
    hashlib.sha256(old_bytes).hexdigest() == protocol["baseline_extractor_sha256"],
    "Baseline reader identity differs",
)
old_tree = ast.parse(old_bytes)
new_tree = ast.parse(source.read_text(encoding="utf-8"))
old_functions = {
    node.name: ast.dump(node, include_attributes=False)
    for node in old_tree.body
    if isinstance(node, ast.FunctionDef)
}
new_functions = {
    node.name: ast.dump(node, include_attributes=False)
    for node in new_tree.body
    if isinstance(node, ast.FunctionDef)
}
changed = {name for name, value in old_functions.items() if new_functions.get(name) != value}
require(changed == {"_boundary_page", "extract_terms", "notice_role"}, changed)
old_constants = [ast.dump(node) for node in old_tree.body if isinstance(node, ast.Assign)]
new_constants = [ast.dump(node) for node in new_tree.body if isinstance(node, ast.Assign)]
require(old_constants == new_constants, "Reader constants changed")
unchanged_modules = {}
for name, expected in parent["active_state"]["source_sha256"].items():
    if name != "distributions.py":
        unchanged_modules[name] = sha(Path("src/quantfolio") / name) == expected
require(all(unchanged_modules.values()), "Unregistered runtime module changed")
require(sha(protocol["test_file"]["path"]) == protocol["test_file"]["sha256"], "Test changed")
texts = load_gzip(base / "source_text.json.gz")["documents"]
checks = []
for case in protocol["observations"]:
    document = texts[case["source"]]
    require(sha(case["source"]) == case["source_sha256"], "Example PDF changed")
    for reader, extraction in document["readers"].items():
        terms = extract_terms(extraction["pages"])
        for field, expected in case["expected_unique_values_or_role"].items():
            if field == "role":
                symbols = document["source"]["scoped_symbols"]
                require(len(symbols) == 1, "Ambiguous example issuer")
                actual = notice_role(symbols[0], document["source"], extraction["pages"])["role"]
                passed = actual == expected
            else:
                actual = sorted({row["value"] for row in terms.get(field, [])})
                passed = (
                    {Decimal(value) for value in actual} == {Decimal(expected)}
                    if field == "cash_per_share"
                    else actual == [expected]
                )
            checks.append(
                {
                    "source": case["source"],
                    "reader": reader,
                    "field": field,
                    "expected": expected,
                    "actual": actual,
                    "passed": passed,
                }
            )
result = {
    "status": "PASSED" if all(row["passed"] for row in checks) else "FAILED",
    "completed_at": now(),
    "checker_sha256": sha(__file__),
    "protocol_sha256": sha("configs/protocols/distribution_layouts.json"),
    "extractor_sha256": sha(source),
    "changed_existing_functions": sorted(changed),
    "new_functions": sorted(set(new_functions) - set(old_functions)),
    "unchanged_constants": True,
    "unchanged_runtime_modules": unchanged_modules,
    "source_checks": checks,
    "facts_accepted": 0,
}
save(base / "layout_regressions.json", result, immutable=True)
print(result["status"], len(checks), "source field/role checks; facts accepted 0")
for check in checks:
    if not check["passed"]:
        print(check)
require(result["status"] == "PASSED", "Preregistered source expectations failed")
