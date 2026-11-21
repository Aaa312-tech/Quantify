"""Prove the runtime change is confined to registered source-recognition strings."""

import ast
import copy
from pathlib import Path

from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol = read(root / "configs/protocols/distribution_wording.json")
base = root / protocol["checks_output"]
state = protocol["active_state"]
source = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
assert source.keys() == state["source_sha256"].keys()
assert {name for name in source if source[name] != state["source_sha256"][name]} == {
    "distributions.py",
    "__init__.py",
}
before = ast.parse((base / "before_distributions.py").read_text(encoding="utf-8"))
after = ast.parse((root / "src/quantfolio/distributions.py").read_text(encoding="utf-8"))
assert sha(base / "before_distributions.py") == state["source_sha256"]["distributions.py"]
assert sha(base / "extractor.py") == source["distributions.py"]


def constants(node):
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


class StripStrings(ast.NodeTransformer):
    def visit_Constant(self, node):
        return ast.Constant("STRING") if isinstance(node.value, str) else node


changed = {}
assert len(before.body) == len(after.body)
for left, right in zip(before.body, after.body):
    if ast.dump(left) == ast.dump(right):
        continue
    assert isinstance(left, ast.FunctionDef) and left.name == right.name
    assert left.name in {"_current_scheme", "extract_terms"}
    assert ast.dump(StripStrings().visit(copy.deepcopy(left))) == ast.dump(
        StripStrings().visit(copy.deepcopy(right))
    )
    a, b = constants(left), constants(right)
    assert len(a) == len(b)
    changed[left.name] = [{"before": x, "after": y} for x, y in zip(a, b) if x != y]
assert len(changed["_current_scheme"]) == 1 and len(changed["extract_terms"]) == 5
assert sha(paths.configuration_path) == state["configuration_sha256"]
assert sha(paths.bundle) == state["bundle_sha256"]
assert sha(root / state["plan"]) == state["plan_sha256"]
save(
    base / "implementation.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "source_sha256": source,
        "changed_literal_strings": changed,
        "unchanged_financial_and_date_gates": True,
        "active_inputs_unchanged": True,
    },
    immutable=True,
)
print("PASS: six source-pattern strings; all acceptance and numerical functions unchanged")
