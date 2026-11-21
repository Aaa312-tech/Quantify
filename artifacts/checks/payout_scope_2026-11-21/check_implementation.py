"""Bind the source-only change and prove that financial acceptance gates are exact."""

import ast
from copy import deepcopy
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
checks = Path(__file__).parent
protocol = read(root / "configs/protocols/payout_scope.json")
before = ast.parse((checks / "before_distributions.py").read_text(encoding="utf-8"))
after = ast.parse((root / "src/quantfolio/distributions.py").read_text(encoding="utf-8"))


def same(left, right):
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


def functions(module):
    return {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}


old, new = functions(before), functions(after)
added = {"_page_text", "_boundary_page", "_cross_page_cash"}
changed = {"_current_scheme", "_current_payout", "extract_terms", "review_distributions"}
assert set(new) - set(old) == added and not set(old) - set(new)
assert {name for name in old if not same(old[name], new[name])} == changed

# Restore only the two normalization statements and remove the registered bridge call.
extraction = deepcopy(new["extract_terms"])
old_loop = next(node for node in old["extract_terms"].body if isinstance(node, ast.For))
new_loop = next(node for node in extraction.body if isinstance(node, ast.For))
assert same(new_loop.body[0], ast.parse("text = _page_text(raw)").body[0])
new_loop.body[:1] = deepcopy(old_loop.body[:2])
assert same(extraction.body[-3], ast.parse("cross_page = _cross_page_cash(pages)").body[0])
assert same(
    extraction.body[-2],
    ast.parse(
        'if cross_page:\n    evidence.setdefault("cash_per_share", []).extend(cross_page)'
    ).body[0],
)
del extraction.body[-3:-1]
assert same(old["extract_terms"], extraction)

# The review change only includes both pages in the provenance inventory.
review = deepcopy(new["review_distributions"])
old_inventory = next(
    node
    for node in ast.walk(old["review_distributions"])
    if isinstance(node, ast.keyword)
    and node.arg == "evidence_pages"
    and isinstance(node.value, ast.Call)
)
new_inventory = next(
    node
    for node in ast.walk(review)
    if isinstance(node, ast.keyword)
    and node.arg == "evidence_pages"
    and isinstance(node.value, ast.Call)
)
new_inventory.value = deepcopy(old_inventory.value)
assert same(old["review_distributions"], review)

before_other = [node for node in before.body if not isinstance(node, ast.FunctionDef)]
after_other = [
    node
    for node in after.body
    if not isinstance(node, ast.FunctionDef)
    and not (
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SCHEME" for target in node.targets)
    )
]
assert len(before_other) == len(after_other)
assert all(same(left, right) for left, right in zip(before_other, after_other))
source = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
old_source = protocol["active_state"]["source_sha256"]
assert [name for name in source if source[name] != old_source[name]] == [
    "__init__.py",
    "distributions.py",
]
for key in ("configuration", "bundle", "plan"):
    assert sha(root / protocol["active_state"][key]) == protocol["active_state"][key + "_sha256"]
save(
    checks / "implementation.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "source_sha256": source,
        "protocol_sha256": sha(root / "configs/protocols/payout_scope.json"),
        "page_order_protocol_sha256": sha(root / "configs/protocols/payout_page_order.json"),
        "changed_functions": sorted(changed),
        "added_functions": sorted(added),
        "unchanged_financial_acceptance": True,
        "unchanged_active_inputs_and_plan": True,
        "scope": "Only current payout scope, exact existing normalization reuse, adjacent-page cash extraction and evidence-page inventory change; every other financial, source, numerical and planning module remains byte-identical.",
    },
    immutable=True,
)
print("IMPLEMENTATION SCOPE PASSED", flush=True)
