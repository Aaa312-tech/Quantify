"""Content checks for immutable inputs and the Python modules that consume them."""

from __future__ import annotations

import ast
import tomllib
from hashlib import sha256
from pathlib import Path

from .storage import read, save, sha


def runtime_sources(directory: Path | None = None) -> dict[str, str]:
    """Read package source hashes in filename order, without caching."""
    directory = Path(directory or Path(__file__).parent)
    return {path.name: sha(path) for path in sorted(directory.glob("*.py"))}


def local_path(paths, name):
    path = (paths.root / name).resolve()
    if not path.is_relative_to(paths.root.resolve()):
        raise ValueError(f"Dependency escapes project: {name}")
    return path


def verify_files(paths, names):
    """Read hashes afresh; registration, existence and content must all agree."""
    manifest = read(paths.manifest)["files"]
    checked = {}
    for name in sorted(set(names)):
        path = local_path(paths, name)
        relative = path.relative_to(paths.root).as_posix()
        if not path.is_file() or relative not in manifest or sha(path) != manifest[relative]:
            raise ValueError(f"Unregistered or changed dependency: {relative}")
        checked[relative] = manifest[relative]
    return checked


def register_files(paths, files):
    """Register file hashes; reject changes to existing entries."""
    manifest = read(paths.manifest)
    for path in sorted(files):
        name = local_path(paths, path).relative_to(paths.root).as_posix()
        checksum = sha(path)
        if name in manifest["files"] and manifest["files"][name] != checksum:
            raise ValueError(f"Frozen output changed: {name}")
        manifest["files"][name] = checksum
    save(paths.manifest, manifest)


def register_output(paths, output):
    """Append immutable output hashes without replacing any existing entry."""
    output = local_path(paths, output)
    register_files(paths, [path for path in output.rglob("*") if path.is_file()])


def source_closure(roots, directory=None):
    """Follow package imports, including imports inside functions.

    Dynamic imports are rejected because their dependency closure is unknown.
    The Git regression loader is validated separately.
    """
    directory = Path(directory or Path(__file__).parent)
    pending = {"__init__", *roots}
    result = {}
    while pending:
        name = pending.pop()
        filename = name + ".py"
        if filename in result:
            continue
        path = directory / filename
        raw = path.read_bytes()
        tree = ast.parse(raw, filename=str(path))
        imports = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and node.id in {"__import__", "exec", "eval"}) or (
                isinstance(node, ast.Attribute)
                and node.attr in {"__import__", "import_module", "spec_from_file_location"}
            ):
                raise ValueError(
                    f"Dynamic import requires an explicit dependency contract: {filename}"
                )
            if isinstance(node, ast.ImportFrom):
                if (
                    node.module
                    and node.module.split(".")[0] == "importlib"
                    and node.module != "importlib.metadata"
                ):
                    raise ValueError(
                        f"Dynamic import requires an explicit dependency contract: {filename}"
                    )
                if node.level == 1:
                    if node.module:
                        imports.add(node.module.split(".")[0])
                    else:
                        imports.update(
                            alias.name for alias in node.names if alias.name != "__version__"
                        )
                elif node.level or node.module == "quantfolio":
                    raise ValueError(f"Unresolved package import in {filename}")
                elif node.module and node.module.startswith("quantfolio."):
                    imports.add(node.module.split(".")[1])
            elif isinstance(node, ast.Import):
                if any(
                    alias.name.split(".")[0] == "importlib" and alias.name != "importlib.metadata"
                    for alias in node.names
                ):
                    raise ValueError(
                        f"Dynamic import requires an explicit dependency contract: {filename}"
                    )
                imports.update(
                    alias.name.split(".")[1]
                    for alias in node.names
                    if alias.name.startswith("quantfolio.")
                )
            elif isinstance(node, ast.Call):
                function = node.func
                called = (
                    function.id if isinstance(function, ast.Name) else getattr(function, "attr", "")
                )
                if called in {
                    "__import__",
                    "import_module",
                    "spec_from_file_location",
                    "exec",
                    "eval",
                }:
                    raise ValueError(
                        f"Dynamic import requires an explicit dependency contract: {filename}"
                    )
        result[filename] = sha256(raw).hexdigest()
        pending.update(imports - {key[:-3] for key in result})
    return dict(sorted(result.items()))


def subject_roots(name):
    path = Path(name)
    if path.name == "protocol.json" and path.parts[:2] == ("artifacts", "experiments"):
        return ["training"]
    if path.name == "plan.json" and path.parts[:2] == ("artifacts", "plans"):
        return ["planning"]
    raise ValueError(f"No source dependency contract for {name}")


def scope_counts(report):
    try:
        return {
            "models": len(report["models"]),
            "training_windows": len(report["training"]["windows"]),
            "fit_stages": len(report["training"]["fit_inputs"]),
            "historical_accounts": report["history"]["accounts"],
            "ledger_files": report["history"]["ledger_files"],
            "paper_accounts": report["paper"]["accounts"],
            "paper_sessions": report["paper"]["sessions"],
            "securities": report["features"]["securities"],
            "features": report["features"]["frozen_features"]["features"],
        }
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete regression result inventory") from error


def write_dependency_certificate(paths, output):
    """Add a dependency certificate to an accepted full regression."""
    legacy = output / "source_compatibility.json"
    certificate = read(legacy)
    result = output / "result.json"
    report = read(result)
    if report["status"] != "PASS" or report["scope"] != "full":
        raise ValueError("Dependency compatibility requires a complete full regression")
    closures = {}
    for name in certificate["subjects"]:
        roots = subject_roots(name)
        sources = source_closure(roots)
        if any(certificate["runtime_sha256"].get(key) != value for key, value in sources.items()):
            raise ValueError("Sources changed after the full regression")
        closures[name] = {"roots": roots, "source_sha256": sources}
    save(
        output / "dependency_compatibility.json",
        {
            "schema_version": 2,
            "kind": "DEPENDENCY_COMPATIBILITY",
            "legacy_certificate": {
                "path": legacy.relative_to(paths.root).as_posix(),
                "sha256": sha(legacy),
            },
            "regression": {
                "path": result.relative_to(paths.root).as_posix(),
                "sha256": sha(result),
            },
            "closures": closures,
        },
        immutable=True,
    )


def dependency_compatible(paths, subject, expected, directory=None):
    """Allow an unrelated module to change only outside a proven dependency closure."""
    manifest = read(paths.manifest)["files"]
    name = subject.relative_to(paths.root).as_posix()
    try:
        roots = subject_roots(name)
        current = source_closure(roots, directory)
    except (OSError, ValueError, SyntaxError):
        return False

    def evidence(reference):
        path = local_path(paths, reference["path"])
        if not path.is_relative_to(paths.root / "artifacts/checks"):
            raise ValueError("Compatibility evidence is outside checks")
        if verify_files(paths, [reference["path"]])[reference["path"]] != reference["sha256"]:
            raise ValueError("Compatibility evidence hash differs")
        return read(path)

    for relative, checksum in manifest.items():
        if not Path(relative).match("artifacts/checks/*/dependency_compatibility.json"):
            continue
        try:
            record = evidence({"path": relative, "sha256": checksum})
            if record["schema_version"] != 2 or record["kind"] != "DEPENDENCY_COMPATIBILITY":
                continue
            closure = record["closures"].get(name)
            if closure != {"roots": roots, "source_sha256": current}:
                continue
            legacy = evidence(record["legacy_certificate"])
            report = evidence(record["regression"])
            profile = paths.root / "configs/regression.toml"
            config = tomllib.loads(profile.read_text(encoding="utf-8"))
            scope = evidence({"path": config["scope"], "sha256": config["scope_sha256"]})
            sources = legacy["runtime_sha256"]
            if (
                legacy["schema_version"] != 1
                or legacy["kind"] != "EXECUTION_REFACTOR"
                or legacy["subjects"].get(name)
                != {"sha256": sha(subject), "source_sha256": expected}
                or any(sources.get(key) != value for key, value in current.items())
                or report["source_sha256"] != sources
                or report["status"] != "PASS"
                or report["scope"] != "full"
                or report["baseline_commit"] != legacy["baseline_commit"]
                or report["baseline_commit"] != config["baseline_commit"]
                or report["profile_sha256"] != sha(profile)
                or scope["baseline_commit"] != report["baseline_commit"]
                or scope_counts(report) != scope["expected"]
                or report["new_model_fits"] != 0
                or not report["models"]
                or any(model["max_error"] != 0 for model in report["models"])
            ):
                continue
            required = {"EXECUTION_DIFFERENTIAL", "HISTORICAL_LEDGER_PARITY"}
            if set(legacy["evidence"]) != required:
                continue
            for kind, reference in legacy["evidence"].items():
                item = evidence(reference)
                if (
                    item["status"] != "PASS"
                    or item["scope"] != "full"
                    or item["kind"] != kind
                    or item["runtime_sha256"] != sources
                    or item["subjects"] != legacy["subjects"]
                    or item["profile_sha256"] != report["profile_sha256"]
                    or (
                        kind == "EXECUTION_DIFFERENTIAL"
                        and any(
                            item[key] != report[key] for key in ("features", "training", "models")
                        )
                    )
                    or (
                        kind == "HISTORICAL_LEDGER_PARITY"
                        and any(item[key] != value for key, value in report["history"].items())
                    )
                ):
                    break
            else:
                return True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return False
