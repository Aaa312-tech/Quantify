"""Limit maintenance to optional bounds and registered inventory checks."""

import ast
import copy
import re
from pathlib import Path

from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol_file = root / "configs/protocols/window_range.json"
p = read(protocol_file)
checks = root / p["checks_output"]
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
changed = sorted(name for name in sources if sources[name] != p["baseline_source_sha256"][name])
assert changed == sorted(p["allowed_modules"])
for name, value in p["before_source"].items():
    assert sha(root / name) == value
assert sha(paths.bundle) == p["bundle_sha256"]
assert sha(paths.configuration_path) == p["active_configuration_sha256"]
assert sha(root / p["current_plan"]["path"]) == p["current_plan"]["sha256"]


def dump(node):
    return ast.dump(node, include_attributes=False)


def statement(code):
    return ast.parse(code).body[0]


def expression(code):
    return ast.parse(code, mode="eval").body


class WithoutDocs(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr):
            first = node.body[0].value
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                node.body.pop(0)
        return node


def replace_once(tree, old, new):
    class Replace(ast.NodeTransformer):
        count = 0

        def visit(self, node):
            if dump(node) == dump(old):
                self.count += 1
                return copy.deepcopy(new)
            return super().visit(node)

    visitor = Replace()
    tree = visitor.visit(tree)
    assert visitor.count == 1
    return tree


def module(name, before=False):
    file = (
        checks / ("before_" + name + ".py") if before else root / "src/quantfolio" / (name + ".py")
    )
    return WithoutDocs().visit(ast.parse(file.read_text(encoding="utf-8")))


before, after = module("training", True), module("training")
function = next(
    node
    for node in before.body
    if isinstance(node, ast.FunctionDef) and node.name == "run_training"
)
assert [arg.arg for arg in function.args.args][-3:] == ["first", "last", "include_latest"]
assert dump(function.args.defaults[1]) == dump(ast.Constant(value=11))
function.args.defaults[1] = ast.Constant(value=None)
assignment = statement('folds = walk_forward_folds(bundle["calendar"])')
before = replace_once(
    before, assignment, [assignment, statement("if last is None:\n    last = len(folds)")]
)
assert dump(before) == dump(after)

before, after = module("cli", True), module("cli")
before = replace_once(
    before,
    statement('training.add_argument("--last", type=int, default=11)'),
    statement(
        'training.add_argument("--last", type=int, help="Last window (default: all complete windows)")'
    ),
)
before = replace_once(
    before,
    expression('args.command == "train" and not 1 <= args.first <= args.last'),
    expression(
        'args.command == "train" and (args.first < 1 or (args.last is not None and args.last < args.first))'
    ),
)
assert dump(before) == dump(after)

before, after = module("feasibility", True), module("feasibility")
before = replace_once(
    before,
    statement(
        'if checked["models_reloaded"] != 48:\n    raise ValueError("The exit-budget study requires all 48 models in each arm")'
    ),
    ast.parse(
        'expected = len(_fit_inventory(output / folder)["models"])\nif checked["models_reloaded"] != expected:\n    raise ValueError(f"The exit-budget study requires all {expected} registered models")'
    ).body,
)
before = replace_once(
    before,
    statement(
        'requirements = {"models_unchanged": True, "legacy_accounts_unchanged": len(legacy) == 1425}'
    ),
    statement(
        'requirements = {"models_unchanged": True, "legacy_accounts_unchanged": len(legacy) == original["accounts"]}'
    ),
)
function = next(
    node for node in before.body if isinstance(node, ast.FunctionDef) and node.name == "fit_parity"
)
function.body[0:0] = ast.parse(
    'inventory = _fit_inventory(baseline)\nif _fit_inventory(current) != inventory:\n    raise ValueError("Fit comparison runs cover different registered artifacts")'
).body
loops = [node for node in function.body if isinstance(node, ast.For)]
assert len(loops) == 2
for loop, key in zip(loops, ("models", "forecasts")):
    assert dump(loop.target) == dump(ast.Name(id="original", ctx=ast.Store()))
    assert dump(loop.body[0]) == dump(statement("relative = original.relative_to(baseline)"))
    loop.target = ast.Name(id="name", ctx=ast.Store())
    loop.iter = expression(f'inventory["{key}"]')
    loop.body[0:1] = ast.parse("relative = Path(name)\noriginal = baseline / relative").body
passed = next(
    node
    for node in function.body
    if isinstance(node, ast.Assign)
    and dump(node.targets[0]) == dump(ast.Name(id="passed", ctx=ast.Store()))
)
assert dump(passed.value.values[0]) == dump(expression("len(models) == 48"))
assert dump(passed.value.values[1]) == dump(expression("len(forecasts) == 12"))
passed.value.values = passed.value.values[2:]
helper = next(
    node
    for node in after.body
    if isinstance(node, ast.FunctionDef) and node.name == "_fit_inventory"
)
before.body.insert(before.body.index(function), copy.deepcopy(helper))
assert dump(before) == dump(after)
assert dump(module("verification", True)) == dump(module("verification"))
version = (root / "src/quantfolio/__init__.py").read_text(encoding="utf-8")
assert '__version__ = "0.25.1"' in version

log = (checks / "local_validation.log").read_text(encoding="utf-8")
tests = re.search(r"(\d+) passed, (\d+) skipped", log)
assert tests and tuple(map(int, tests.groups())) == (545, 1)
assert "All checks passed!" in log and "74 files already formatted" in log
save(
    checks / "implementation.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "protocol_sha256": sha(protocol_file),
        "checker_sha256": sha(Path(__file__)),
        "source_sha256": sources,
        "changed_modules": changed,
        "ast_scope": "Exact whole-module AST equality after only the registered optional-range, CLI and inventory/count substitutions; verification changes only a docstring. All estimator, calibration, selection, execution, risk, ledger and other training functions are unchanged.",
        "new_inventory_helper_sha256": __import__("hashlib")
        .sha256(dump(helper).encode())
        .hexdigest(),
        "active_configuration_unchanged": True,
        "active_data_unchanged": True,
        "current_plan_unchanged": True,
        "local_tests": {
            "passed": 545,
            "skipped": 1,
            "skip_reason": "Host does not permit directory symlinks",
            "log_sha256": sha(checks / "local_validation.log"),
        },
        "refits": 0,
        "numerical_formula_changes": 0,
    },
    immutable=True,
)
print("REGISTERED SOURCE SCOPE PASSED", changed, flush=True)
