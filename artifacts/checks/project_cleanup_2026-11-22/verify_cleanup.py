"""Check the CLI and test-layout refactor against the recorded pre-cleanup commit."""

import argparse
import ast
import copy
import io
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quantfolio import cli
from quantfolio.storage import digest, now, read, save, sha

ROOT = Path(__file__).resolve().parents[3]
CHECK = Path(__file__).resolve().parent
SCOPE = read(CHECK / "scope.json")
MIGRATION = read(CHECK / "test_migration.json")
BASELINE = SCOPE["baseline_commit"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def original(path):
    return subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], cwd=ROOT).decode("utf-8")


def declaration(tree, name):
    return next(
        node
        for node in tree.body
        if getattr(node, "name", None) == name
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
        )
    )


class Normalize(ast.NodeTransformer):
    """Ignore import placement and apply only the recorded helper identifier changes."""

    def __init__(self, renames):
        self.renames = renames

    def visit_Name(self, node):
        node.id = self.renames.get(node.id, node.id)
        return node

    def visit_Import(self, node):
        return None

    def visit_ImportFrom(self, node):
        return None


def tests_equal():
    factories = ast.parse((ROOT / MIGRATION["factory_path"]).read_text("utf-8"))
    helpers = 0
    for module, names in MIGRATION["shared_factories"].items():
        before = ast.parse(original(f"tests/{module}.py"))
        for old, new in names.items():
            expected = copy.deepcopy(declaration(before, old))
            if isinstance(expected, ast.FunctionDef):
                expected.name = new
            else:
                expected.targets[0].id = new
            require(
                ast.dump(expected) == ast.dump(declaration(factories, new)),
                f"Factory changed: {old} -> {new}",
            )
            helpers += 1
    for item in MIGRATION["test_paths"]:
        before = ast.parse(original(item["old"]))
        before.body = [
            node
            for node in before.body
            if node not in [declaration(before, name) for name in item["moved_helpers"]]
        ]
        adjustment = item.get("project_root_adjustment")
        if adjustment:
            function = declaration(before, adjustment["function"])
            root = function.body[0].value
            require(
                ast.dump(root)
                == ast.dump(ast.parse("Path(__file__).resolve().parents[1]", mode="eval").body),
                "Unexpected original root lookup",
            )
            require(
                (ROOT / item["old"]).resolve().parents[adjustment["old_parent_index"]]
                == (ROOT / item["new"]).resolve().parents[adjustment["new_parent_index"]],
                "Test root no longer resolves to the same project",
            )
            root.slice.value = adjustment["new_parent_index"]
        after = ast.parse((ROOT / item["new"]).read_text("utf-8"))
        require(
            ast.dump(Normalize(item["helper_renames"]).visit(before))
            == ast.dump(Normalize({}).visit(after)),
            f"Test statements changed: {item['new']}",
        )
    for path in (ROOT / "tests").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [name.name for name in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            require(
                not any(name == "conftest" or name.startswith("test_") for name in modules),
                f"Import still depends on test collection: {path}",
            )
    output = subprocess.check_output(
        [sys.executable, "-B", "-m", "pytest", "--collect-only", "-q"], cwd=ROOT, text=True
    )
    actual = sorted(
        line for line in output.splitlines() if line.startswith("tests/") and "::" in line
    )
    paths = {item["old"]: item["new"] for item in MIGRATION["test_paths"]}
    expected = sorted(
        paths[node.split("::", 1)[0]] + "::" + node.split("::", 1)[1]
        for node in SCOPE["collected_tests"]
    )
    require(actual == expected, "Collected test cases changed")
    files = subprocess.check_output(
        [sys.executable, "-B", "-m", "ruff", "check", "--no-cache", "--show-files", "src", "tests"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    expected_files = {
        p.resolve() for folder in ("src/quantfolio", "tests") for p in (ROOT / folder).rglob("*.py")
    }
    require({Path(p).resolve() for p in files} == expected_files, "Ruff excludes live Python files")
    return {
        "modules": len(paths),
        "factories_and_constants": helpers,
        "collected": len(actual),
        "ruff_files": len(files),
    }


def cli_equal():
    source = original("src/quantfolio/cli.py")
    old = {"__name__": "quantfolio._cleanup_baseline", "__package__": "quantfolio"}
    exec(compile(source, "baseline_cli.py", "exec"), old)
    captured = []

    class Captured(Exception):
        pass

    def capture(parser, *args, **kwargs):
        captured.append(parser)
        raise Captured

    with patch.object(argparse.ArgumentParser, "parse_args", capture):
        try:
            old["main"]([])
        except Captured:
            pass
    before, after = captured[0], cli._parser()

    def schema(parser):
        actions = []
        for action in parser._actions:
            values = {
                key: getattr(action, key)
                for key in (
                    "option_strings",
                    "dest",
                    "nargs",
                    "const",
                    "default",
                    "required",
                    "help",
                    "metavar",
                )
            }
            values["class"] = type(action).__name__
            values["type"] = getattr(action.type, "__qualname__", None)
            values["choices"] = (
                {key: schema(value) for key, value in action.choices.items()}
                if isinstance(action, argparse._SubParsersAction)
                else action.choices
            )
            actions.append(values)
        return {
            "help": parser.format_help(),
            "actions": actions,
            "exclusive": [
                (group.required, [a.dest for a in group._group_actions])
                for group in parser._mutually_exclusive_groups
            ],
        }

    require(schema(before) == schema(after), "CLI help, defaults or argument schema changed")
    old_tree = ast.parse(source)
    new_tree = ast.parse((ROOT / "src/quantfolio/cli.py").read_text("utf-8"))
    require(
        ast.dump(declaration(old_tree, "run")) == ast.dump(declaration(new_tree, "run")),
        "Command routing changed",
    )

    def tail(tree):
        body = declaration(tree, "main").body
        start = next(
            i
            for i, node in enumerate(body)
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "args"
        )
        return ast.dump(ast.Module(body=body[start:], type_ignores=[]))

    require(tail(old_tree) == tail(new_tree), "CLI validation or exception handling changed")
    commands = next(a.choices for a in after._actions if isinstance(a, argparse._SubParsersAction))
    cases = [
        [],
        ["--help"],
        ["--version"],
        ["unknown"],
        ["verify", "--refit"],
        ["train", "--output", "sample", "--first", "0"],
        ["train", "--output", "sample", "--first", "3", "--last", "2"],
        ["report", "--run", "sample", "--reference"],
        ["plan", "--account", "sample", "--output", "sample", "--cost", "11"],
    ]
    for name, parser in commands.items():
        minimal = [name]
        for action in parser._actions:
            if action.required:
                minimal += [
                    action.option_strings[0],
                    str(action.choices[0] if action.choices else "sample"),
                ]
        cases.extend(
            [
                minimal,
                [name, "--help"],
                [name, "--unknown"],
                ["--debug", "--project", ".", "--config", "configs/project.toml", *minimal],
            ]
        )

    def invoke(namespace, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        replacements = {
            "run": lambda args, paths: vars(args),
            "Paths": lambda *args: None,
            "project_root": lambda value: value,
        }
        with patch.dict(namespace, replacements), redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                namespace["main"](argv)
                status = 0
            except SystemExit as error:
                status = error.code
        return status, stdout.getvalue(), stderr.getvalue()

    for case in cases:
        require(invoke(old, case) == invoke(cli.__dict__, case), f"CLI behavior changed: {case}")
    return {
        "commands_and_aliases": len(commands),
        "argument_cases": len(cases),
        "cases_sha256": digest(cases),
        "routing_and_validation_ast_equal": True,
        "research_commands_executed": False,
    }


def integrity_equal():
    import json

    manifest_path = "configs/integrity_manifest.json"
    baseline = json.loads(original(manifest_path))
    current = read(ROOT / manifest_path)
    require(
        all(current["files"].get(path) == value for path, value in baseline["files"].items()),
        "An original manifest entry changed",
    )
    expected = dict(baseline["files"])
    protected = {
        path: value
        for path, value in SCOPE["additional_protected_files"].items()
        if path != manifest_path
    }
    expected.update(protected)
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual = dict(zip(expected, pool.map(lambda path: sha(ROOT / path), expected), strict=True))
    require(actual == expected, "A protected historical file changed")
    models = read(CHECK / "models/verification.json")
    require(
        models["status"] == "PASS"
        and models["models_reloaded"] == 48
        and models["fresh_refit"] is None
        and all(item["max_error"] == 0 for item in models["models"]),
        "Frozen model verification failed",
    )
    return {
        "original_frozen_files": len(baseline["files"]),
        "additional_protected_files": len(protected),
        "manifest_exception": "Append-only evidence registration; every original entry is retained exactly.",
        "model_reload_receipt_sha256": sha(CHECK / "models/verification.json"),
        "models_reloaded": 48,
        "max_prediction_error": 0.0,
        "refit": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    require(not output.exists(), "Use a new evidence path")
    result = {
        "baseline_commit": BASELINE,
        "goal_status": "paused",
        "scope_sha256": sha(CHECK / "scope.json"),
        "migration_sha256": sha(CHECK / "test_migration.json"),
        "audit_sha256": sha(Path(__file__)),
    }
    try:
        result["tests"] = tests_equal()
        result["cli"] = cli_equal()
        result["integrity"] = integrity_equal()
        result["status"] = "PASS"
    except Exception as error:
        result.update(status="FAIL", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        result["completed_at"] = now()
        save(output, result, immutable=True)
    print(
        f"PASS: {result['tests']['collected']} cases, {result['cli']['argument_cases']} CLI checks, historical bytes preserved"
    )
