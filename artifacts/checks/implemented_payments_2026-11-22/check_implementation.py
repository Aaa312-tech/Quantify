"""Prove old source-phase branches and every numerical module are preserved."""

import ast
from copy import deepcopy
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
p = read(root / "configs/protocols/implemented_payments.json")
base = root / p["checks_output"]
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
changed = [name for name, value in sources.items() if value != p["baseline_source_sha256"][name]]
assert changed == ["__init__.py", "snapshots.py"]
assert sha(base / "before_snapshots.py") == p["baseline_source_sha256"]["snapshots.py"]
assert sha(base / "executed_snapshots.py") == sources["snapshots.py"]
before, after = [
    ast.parse(file.read_text(encoding="utf-8"))
    for file in (base / "before_snapshots.py", root / "src/quantfolio/snapshots.py")
]
old = {node.name: node for node in before.body if isinstance(node, ast.FunctionDef)}
new = {node.name: node for node in after.body if isinstance(node, ast.FunctionDef)}
assert old.keys() == new.keys()
modified = [name for name in old if ast.dump(old[name]) != ast.dump(new[name])]
assert modified == ["accepted_date_records", "_accepted_date_supplements"]
phase_test = ast.dump(
    ast.parse('registered.get("phase") == "implementation_payout_scope"', mode="eval").body
)
shared_test = ast.dump(
    ast.parse(
        'registered.get("phase") in {"gross_cash_wording", "implementation_payout_scope"}',
        mode="eval",
    ).body
)
old_test = ast.parse('registered.get("phase") == "gross_cash_wording"', mode="eval").body


class OldPhase(ast.NodeTransformer):
    """Specialize the shared receipt path to its already supported wording phase."""

    def __init__(self):
        self.shared = 0
        self.new_only = 0
        self.receipt = 0

    def visit_If(self, node):
        if ast.dump(node.test) == phase_test:
            self.new_only += 1
            return None
        if ast.dump(node.test) == shared_test:
            self.shared += 1
            node.test = deepcopy(old_test)
        return self.generic_visit(node)

    def visit_Call(self, node):
        expected = ast.parse("evidence.get(receipt)", mode="eval").body
        if ast.dump(node) == ast.dump(expected):
            self.receipt += 1
            node.args = [ast.Constant(value="field_acceptance.json")]
        return self.generic_visit(node)


specialization = {}
for name in modified:
    transform = OldPhase()
    restored = transform.visit(deepcopy(new[name]))
    assert transform.shared == transform.new_only == 1
    assert transform.receipt == (1 if name == "accepted_date_records" else 0)
    assert ast.dump(restored) == ast.dump(old[name]), name
    specialization[name] = vars(transform)
for tree in (before, after):
    tree.body = [node for node in tree.body if not isinstance(node, ast.FunctionDef)]
assert ast.dump(before) == ast.dump(after)
test = root / "tests/test_declared_payment_receipts.py"
assert ast.dump(ast.parse((base / "before_tests.py").read_text(encoding="utf-8"))) == ast.dump(
    ast.parse(test.read_text(encoding="utf-8"))
)
assert sha(test) == sha(base / "executed_tests.py")
local = read(base / "local_validation.json")
assert local["status"] == "PASS" and local["passed"] == 687 and local["skipped"] == 1
assert local["source_sha256"] == sources and local["test_sha256"] == sha(test)
assert local["log_sha256"] == sha(base / "local_validation.log")
assert sha(root / "configs/project.toml") == p["active_configuration_sha256"]
save(
    base / "implementation.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "source_sha256": sources,
        "changed_runtime_modules": changed,
        "added_functions": [],
        "changed_functions": modified,
        "old_phase_ast_specialization": specialization,
        "all_numerical_and_execution_modules_byte_identical": True,
        "new_tests_sha256": sha(test),
        "before_tests_ast_preserved": True,
        "local_tests": {
            "passed": local["passed"],
            "skipped": local["skipped"],
            "reason": "Host directory symlink permission",
            "log_sha256": local["log_sha256"],
        },
        "scope": "The two receipt functions share their original checks with the new source phase. Specializing the new receipt filename and removing only its page-order checks restores the complete original ASTs. Other runtime modules are byte-identical except package version. Ordinary guarded model/account/paper compatibility remains required.",
    },
    immutable=True,
)
print("RUNTIME SCOPE PASSED", changed, specialization, flush=True)
