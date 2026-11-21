"""Restrict the follow-up patch to explicit fund-control validation and copying."""

import ast
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
protocol_file = root / "configs/protocols/fund_mode_preservation.json"
protocol = read(protocol_file)
folder = root / protocol["checks_output"]
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
changed = {name for name, value in sources.items() if value != protocol["before_source_sha256"][name]}
assert changed == {"dataset.py", "preprocessing.py", "snapshots.py"}
assert sources.keys() == protocol["before_source_sha256"].keys()

expected_helper = '''
def fund_mode(meta):
    """Infer one declared control from fund histories; do not silently disable audits."""
    modes = set()
    has_history = False
    for instrument in meta.values():
        if "fund_history" not in instrument:
            continue
        has_history = True
        if instrument["kind"] != "etf":
            raise ValueError("Fund history requires an ETF")
        for entry in instrument["fund_history"]:
            profile = entry.get("profile")
            if profile is None:
                continue
            mode = profile.get("mode")
            if mode not in {"coverage", "identity", "reported"}:
                raise ValueError("Fund history has an unknown control")
            modes.add(mode)
    if not has_history:
        return None
    if len(modes) != 1:
        raise ValueError("Fund histories require one consistent declared control")
    return modes.pop()
'''

allowed = {
    "dataset.py": {
        "load_snapshot": ['''
if bundle.get("fund_mode") != fund_mode(bundle["meta"]):
    raise ValueError("Fund mode differs from the stored profile histories")
'''],
    },
    "preprocessing.py": {
        "build_dataset": [
            "from .dataset import fund_mode",
            "mode = fund_mode(meta)",
            'if mode is not None:\n    result["fund_mode"] = mode',
        ],
        "compare_history": ['''
if before.get("fund_mode") != after.get("fund_mode"):
    raise ValueError("Historical fund mode changed")
'''],
    },
    "snapshots.py": {
        "repair_snapshot": ['''
if "fund_mode" in original:
    repaired["fund_mode"] = original["fund_mode"]
'''],
    },
}
records = []
for name in sorted(changed):
    before_file = folder / ("before_" + name)
    after_file = root / "src/quantfolio" / name
    assert sha(before_file) == protocol["before_source_sha256"][name]
    before = ast.parse(before_file.read_text(encoding="utf-8"))
    after = ast.parse(after_file.read_text(encoding="utf-8"))
    if name == "dataset.py":
        functions = [node for node in after.body if isinstance(node, ast.FunctionDef) and node.name == "fund_mode"]
        assert len(functions) == 1
        assert ast.dump(functions[0]) == ast.dump(ast.parse(expected_helper).body[0])
        after.body.remove(functions[0])
    for function_name, additions in allowed[name].items():
        functions = [node for node in after.body if isinstance(node, ast.FunctionDef) and node.name == function_name]
        assert len(functions) == 1
        for text in additions:
            expected = ast.dump(ast.parse(text).body[0])
            matching = [node for node in functions[0].body if ast.dump(node) == expected]
            assert len(matching) == 1, (name, function_name, text)
            functions[0].body.remove(matching[0])
    assert ast.dump(before) == ast.dump(after), name
    records.append({"module": name, "before_sha256": sha(before_file), "after_sha256": sha(after_file), "remaining_ast_exact": True})

save(folder / "ast_review.json", {
    "status": "PASS", "checked_at": now(), "protocol_sha256": sha(protocol_file),
    "source_sha256": sources, "before_source_sha256": protocol["before_source_sha256"],
    "changed_modules": records,
    "all_other_modules_byte_identical": True,
    "scope": "Only the exact helper and mode-check/copy statements listed in this audit differ. All previous function bodies, execution, models, features, risk and account arithmetic remain unchanged.",
}, immutable=True)
print("MODE AST REVIEW PASSED", sorted(changed), flush=True)
