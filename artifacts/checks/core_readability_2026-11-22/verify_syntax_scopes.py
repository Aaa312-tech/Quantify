"""Compare calculation syntax after explicit local renames and forecast-loop expansion."""

import ast
import copy
from pathlib import Path

from quantfolio.storage import now, read, save, sha

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
BASELINE = ROOT / ".git/core_readability_baseline/quantfolio"
RENAMES = read(OUTPUT / "renames.json")
FUNCTION_NAMES = {"validation_summary": "objective", "label_clip_bounds": "clip_for"}


class Names(ast.NodeTransformer):
    def __init__(self, names):
        self.names = names

    def visit_Name(self, node):
        node.id = self.names.get(node.id, node.id)
        return node

    def visit_arg(self, node):
        node.arg = self.names.get(node.arg, node.arg)
        return node


class Clean(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        node.name = FUNCTION_NAMES.get(node.name, node.name)
        return self.generic_visit(node)

    def visit_Name(self, node):
        node.id = FUNCTION_NAMES.get(node.id, node.id)
        return node

    def visit_ImportFrom(self, node):
        for item in node.names:
            item.name = FUNCTION_NAMES.get(item.name, item.name)
        node.names.sort(key=lambda item: (item.name, item.asname or ""))
        return node

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)


def expand_forecasts(function, helper):
    call = function.body[-1].value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        raise ValueError("Expected forecast grouping call")
    if call.func.id != "_group_forecasts" or len(call.args) != 5 or call.keywords:
        raise ValueError("Forecast helper signature changed")
    arguments = {arg.arg: value for arg, value in zip(helper.args.args, call.args)}
    local_names = (
        {
            "forecasts": "out",
            "date_index": "d",
            "symbol": "s",
            "rank": "r",
            "mean": "m",
            "margin": "e",
        }
        if function.name == "forecasts_by_date"
        else {"forecasts": "out", "date_index": "day"}
    )

    class Inline(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id in arguments:
                return copy.deepcopy(arguments[node.id])
            node.id = local_names.get(node.id, node.id)
            return node

    expanded = [Inline().visit(copy.deepcopy(statement)) for statement in helper.body]
    function.body = function.body[:-1] + expanded
    if function.name == "prediction_map":
        function = Names({"signals": "sub"}).visit(function)
        # The only reordered statement allocates an empty local dictionary.
        allocation = function.body.pop(1)
        if ast.dump(allocation, include_attributes=False) != ast.dump(
            ast.parse("out = {}").body[0], include_attributes=False
        ):
            raise ValueError("Unexpected forecast allocation")
        function.body.insert(0, allocation)
    return function


def normalized(path, *, current):
    module = Clean().visit(ast.parse(path.read_text("utf-8")))
    helper = next(
        (
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "_group_forecasts"
        ),
        None,
    )
    result = []
    for node in module.body:
        if not isinstance(node, ast.FunctionDef):
            result.append(node)
            continue
        if node.name in {"feature_frame", "_group_forecasts"}:
            continue
        aliases = {}
        if path.name == "features.py" and node.name in {"base_features", "momentum_features"}:
            aliases = {"f": "prices"} if node.name == "base_features" else {"t": "table"}
            if not current:
                node = Names(aliases).visit(node)
                expected = ast.parse(
                    "prices = prices" if node.name == "base_features" else "table = table"
                ).body[0]
                if ast.dump(node.body[0], include_attributes=False) != ast.dump(
                    expected, include_attributes=False
                ):
                    raise ValueError("Expected removable local alias")
                node.body.pop(0)
        if not current and path.name == "metrics.py" and node.name == "episode_stats":
            # Comprehension variables have independent scopes; these names already occurred
            # in the baseline before other comprehensions adopted the same readable names.
            node = Names({"episode": "e", "day": "d"}).visit(node)
        if current:
            mapping = RENAMES.get(path.name, {}).get(node.name, {})
            inverse = {new: old for old, new in mapping.items() if old not in aliases}
            if path.name == "models.py" and node.name == "factor_check":
                inverse.update(date_index="d", day="d", spreads="v", block="k", spread="x")
            if path.name == "selection.py" and node.name == "objective":
                inverse.update(episode="x", summary="x")
            node = Names(inverse).visit(node)
            if helper is not None and node.name in {"forecasts_by_date", "prediction_map"}:
                node = expand_forecasts(node, helper)
        result.append(node)
    module.body = result
    return ast.dump(module, include_attributes=False)


scope = read(OUTPUT / "scope.json")
result = {
    "baseline_commit": scope["baseline_commit"],
    "modules": [],
    "removed_test_only_wrapper": "features.feature_frame",
}
try:
    for name in scope["editable_files"]:
        if not name.startswith("src/") or not name.endswith(".py"):
            continue
        old = BASELINE / Path(name).name
        new = ROOT / name
        if sha(old) != scope["original_runtime_sha256"][name]:
            raise ValueError(f"Baseline source changed: {name}")
        if normalized(old, current=False) != normalized(new, current=True):
            raise ValueError(f"Calculation syntax differs: {name}")
        result["modules"].append({"path": name, "before": sha(old), "after": sha(new)})
    consumers = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "src").rglob("*.py")
        if "feature_frame" in path.read_text("utf-8")
    ]
    if consumers:
        raise ValueError(f"Removed wrapper still has consumers: {consumers}")
    result.update(status="PASS", formula_and_control_flow_equal=True)
except Exception as error:
    result.update(status="FAIL", error=f"{type(error).__name__}: {error}")
    raise
finally:
    result["completed_at"] = now()
    save(OUTPUT / "syntax_scopes.json", result, immutable=True)
print(f"PASS: {len(result['modules'])} calculation modules")
