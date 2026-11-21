"""Specialize the entitlement branch away and compare the complete old runtime AST."""

import ast
from copy import deepcopy
from pathlib import Path

from quantfolio.storage import now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


class ExistingOperations(ast.NodeTransformer):
    def visit_Assign(self, node):
        if any(
            isinstance(target, ast.Name) and target.id == "entitlement" for target in node.targets
        ):
            return None
        return self.generic_visit(node)

    def visit_Name(self, node):
        return ast.Constant(False) if node.id == "entitlement" else node

    def visit_Tuple(self, node):
        node.elts = [
            value
            for value in node.elts
            if not (
                isinstance(value, ast.Constant) and value.value == "apply_verified_entitlements"
            )
        ]
        return self.generic_visit(node)

    def visit_BoolOp(self, node):
        node = self.generic_visit(node)
        if isinstance(node.op, ast.Or):
            node.values = [
                value
                for value in node.values
                if not (isinstance(value, ast.Constant) and value.value is False)
            ]
            if len(node.values) == 1:
                return node.values[0]
        return node

    def visit_If(self, node):
        node = self.generic_visit(node)
        if isinstance(node.test, ast.Constant) and node.test.value is False:
            return node.orelse
        return node

    def visit_IfExp(self, node):
        node = self.generic_visit(node)
        return (
            node.orelse
            if isinstance(node.test, ast.Constant) and node.test.value is False
            else node
        )


root = Path.cwd()
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
base = root / p["checks_output"]
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
changed = [name for name in sources if sources[name] != p["baseline_source_sha256"][name]]
require(changed == ["__init__.py", "snapshots.py"], "Unregistered runtime change")
before, after = [
    ast.parse(file.read_text(encoding="utf-8"))
    for file in (base / "before_snapshots.py", root / "src/quantfolio/snapshots.py")
]
old = {node.name: node for node in before.body if isinstance(node, ast.FunctionDef)}
new = {node.name: node for node in after.body if isinstance(node, ast.FunctionDef)}
added = sorted(new.keys() - old.keys())
require(
    added == ["_accepted_entitlement_facts", "entitlement_facts"], "Unexpected new runtime function"
)
modified = [name for name in old if ast.dump(old[name]) != ast.dump(new[name])]
require(modified == ["repair_distribution_snapshot"], "Existing numeric/helper functions changed")
restored = ExistingOperations().visit(deepcopy(new[modified[0]]))
require(ast.dump(restored) == ast.dump(old[modified[0]]), "Old-operation specialization differs")
for tree in (before, after):
    tree.body = [node for node in tree.body if not isinstance(node, ast.FunctionDef)]
require(ast.dump(before) == ast.dump(after), "Module constants/imports changed")
local = read(base / "local_validation.json")
require(
    local["source_sha256"] == sources and local["status"] == "PASS",
    "Local tests bind another implementation",
)
require(
    sha(root / "configs/project.toml") == p["active_configuration_sha256"],
    "Active configuration changed",
)
save(
    base / "implementation.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "source_sha256": sources,
        "changed_runtime_modules": changed,
        "added_functions": added,
        "changed_functions": modified,
        "old_operation_complete_ast_exact_after_specialization": True,
        "all_feature_label_model_execution_risk_and_planning_modules_byte_identical": True,
        "local_tests": local,
        "activated": False,
    },
    immutable=True,
)
print("IMPLEMENTATION_SCOPE_PASSED", changed, added, flush=True)
