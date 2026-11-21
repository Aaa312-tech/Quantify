"""Bind the completed baseline replay to the final snapshot-only validation change."""

import ast
from copy import deepcopy
from pathlib import Path

from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha
from quantfolio.verification import verify

root = Path.cwd()
p = read(root / "configs/protocols/distribution_availability.json")
checks = root / p["checks_output"]
parity_file = checks / "compatibility/validation.json"
parity = read(parity_file)
assert parity["status"] == "PASS"
assert parity["models_reloaded"] == 48
assert parity["full_accounts_exact"] == 1425 and parity["paper_accounts_exact"] == 75
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
changed = [name for name, value in source.items() if parity["source_sha256"][name] != value]
assert changed == ["snapshots.py"]
old_file = checks / "rejected_label_snapshots.py"
assert sha(old_file) == parity["source_sha256"]["snapshots.py"]
before = ast.parse(old_file.read_text(encoding="utf-8"))
after = ast.parse((root / "src/quantfolio/snapshots.py").read_text(encoding="utf-8"))
old_functions = {n.name: n for n in before.body if isinstance(n, ast.FunctionDef)}
new_functions = {n.name: n for n in after.body if isinstance(n, ast.FunctionDef)}
assert new_functions.keys() - old_functions.keys() == {"restored_label_table"}
assert not old_functions.keys() - new_functions.keys()
modified = [
    name for name in old_functions if ast.dump(old_functions[name]) != ast.dump(new_functions[name])
]
assert modified == ["repair_distribution_snapshot"]
old_function = deepcopy(old_functions[modified[0]])
new_function = new_functions[modified[0]]
assert len(old_function.body) == len(new_function.body)
statements = [
    i
    for i, (a, b) in enumerate(zip(old_function.body, new_function.body))
    if ast.dump(a) != ast.dump(b)
]
assert len(statements) == 1
position = statements[0]
block = old_function.body[position]
assert (
    isinstance(block, ast.If) and isinstance(block.test, ast.Name) and block.test.id == "supplement"
)
assert len(block.body) == 1 and not block.orelse
assert isinstance(block.body[0], ast.Expr)
call = block.body[0].value
assert isinstance(call, ast.Call) and ast.unparse(call.func) == "pd.testing.assert_frame_equal"
assert [ast.unparse(arg) for arg in call.args] == ["original['table']", "repaired['table']"]
old_function.body[position] = deepcopy(new_function.body[position])
assert ast.dump(old_function) == ast.dump(new_function)
before.body = [n for n in before.body if not isinstance(n, ast.FunctionDef)]
after.body = [n for n in after.body if not isinstance(n, ast.FunctionDef)]
assert ast.dump(before) == ast.dump(after)
assert sha(root / "configs/project.toml") == p["active_configuration_sha256"]
models = verify(
    Paths(root), checks / "compatibility/final_source_models", run=root / p["baseline_run"]
)
assert models["models_reloaded"] == 48 and all(row["max_error"] == 0 for row in models["models"])
assert source == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
save(
    checks / "compatibility/source_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "source_sha256": source,
        "original_compatibility_sha256": sha(parity_file),
        "original_executed_source_sha256": parity["source_sha256"],
        "changed_modules": changed,
        "added_functions": ["restored_label_table"],
        "changed_functions": modified,
        "replaced_statements": statements,
        "all_other_runtime_modules_byte_identical": True,
        "original_models_reloaded": 48,
        "full_accounts_exact": 1425,
        "paper_accounts_exact": 75,
        "final_source_models_reloaded": 48,
        "prediction_max_error": 0.0,
        "final_model_receipt_sha256": sha(
            checks / "compatibility/final_source_models/verification.json"
        ),
        "scope": "The only AST change after the completed ordinary baseline replay is the supplemental snapshot table acceptance block and its validation helper. Every numerical/training/execution module is byte-identical. All 48 parent models were additionally reloaded through final ordinary guards. The original 1425-account and 75-paper receipts retain their actual source hashes.",
    },
    immutable=True,
)
print("FINAL SOURCE SCOPE AND ORDINARY RELOAD PASSED", flush=True)
