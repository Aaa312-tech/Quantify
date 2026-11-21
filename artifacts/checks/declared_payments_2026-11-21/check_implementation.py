"""Bind the receipt-only runtime change and the completed local checks."""

import ast
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
p = read(root / "configs/protocols/declared_payments.json")
checks = root / p["checks_output"]
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
changed = [name for name in sources if sources[name] != p["baseline_source_sha256"][name]]
assert changed == ["__init__.py", "snapshots.py"]
before_file, after_file = checks / "before_snapshots.py", root / "src/quantfolio/snapshots.py"
assert sha(before_file) == p["baseline_source_sha256"]["snapshots.py"]
before, after = [ast.parse(f.read_text(encoding="utf-8")) for f in (before_file, after_file)]
old = {n.name: n for n in before.body if isinstance(n, ast.FunctionDef)}
new = {n.name: n for n in after.body if isinstance(n, ast.FunctionDef)}
assert new.keys() == old.keys()
modified = [name for name in old if ast.dump(old[name]) != ast.dump(new[name])]
assert modified == ["accepted_date_records", "_accepted_date_supplements"]
for name in modified:
    previous = old[name]
    current = new[name]
    removed = 0
    for node in ast.walk(current):
        if not isinstance(node, ast.If) or len(node.orelse) != 1:
            continue
        branch = node.orelse[0]
        if isinstance(branch, ast.If) and any(
            isinstance(value, ast.Constant) and value.value == "gross_cash_wording"
            for value in ast.walk(branch.test)
        ):
            node.orelse = branch.orelse
            removed += 1
    assert removed == 1
    assert ast.dump(previous) == ast.dump(current), name
for tree in (before, after):
    tree.body = [n for n in tree.body if not isinstance(n, ast.FunctionDef)]
assert ast.dump(before) == ast.dump(after)
log_file = checks / "local_validation.log"
log = log_file.read_text(encoding="utf-8-sig")
assert "607 passed, 1 skipped" in log and "All checks passed!" in log
assert sha(root / "configs/project.toml") == p["active_configuration_sha256"]
save(
    checks / "implementation.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "source_sha256": sources,
        "changed_runtime_modules": changed,
        "added_functions": [],
        "changed_functions": modified,
        "all_numerical_and_execution_modules_byte_identical": True,
        "new_tests_sha256": sha(root / "tests/test_declared_payment_receipts.py"),
        "local_tests": {
            "passed": 607,
            "skipped": 1,
            "reason": "Host directory symlink permission",
            "log_sha256": sha(log_file),
        },
        "scope": "Only source receipt acceptance and its binding change. Feature, label, model, target, allocation, execution, risk and planning algorithms remain byte-identical. All old-input accounts are separately replayed through ordinary guards under this final source.",
    },
    immutable=True,
)
print("RUNTIME SCOPE AND LOCAL CHECKS PASSED", changed, flush=True)
