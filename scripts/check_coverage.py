"""Compare line and branch coverage with the frozen engineering baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from pathlib import Path


def module_totals(report: dict) -> dict[str, dict[str, int]]:
    result = {}
    if not report["meta"]["branch_coverage"]:
        raise ValueError("Branch coverage was not measured")
    for name, entry in report["files"].items():
        normalized = name.replace("\\", "/")
        if "/quantfolio/" not in normalized:
            raise ValueError(f"Unexpected coverage source: {name}")
        module = "src/quantfolio/" + normalized.rsplit("/quantfolio/", 1)[1]
        if module in result:
            raise ValueError(f"Duplicate coverage source: {module}")
        summary = entry["summary"]
        result[module] = {
            key: summary[key]
            for key in ("covered_lines", "num_statements", "covered_branches", "num_branches")
        }
    return result


def compare_coverage(current: dict, baseline: dict, groups: dict, minimums=None) -> list[str]:
    """A moved module is compared with its named successors as one coverage scope."""
    errors = []
    members = []
    for module, expected in baseline.items():
        names = groups.get(module, [module])
        if not names or any(name not in current for name in names):
            errors.append(f"Missing coverage: {module}")
            continue
        members.extend(names)
        actual = {key: sum(current[name][key] for name in names) for key in expected}
        for covered, total in (
            ("covered_lines", "num_statements"),
            ("covered_branches", "num_branches"),
        ):
            for counts in (expected, actual):
                if not 0 <= counts[covered] <= counts[total]:
                    raise ValueError(f"Invalid coverage counts: {module}/{covered}")
            before = (expected[covered], expected[total]) if expected[total] else (1, 1)
            after = (actual[covered], actual[total]) if actual[total] else (1, 1)
            if after[0] * before[1] < before[0] * after[1]:
                errors.append(f"Coverage decreased: {module}/{covered}: {before} -> {after}")
    if len(members) != len(set(members)):
        errors.append("Coverage groups overlap")
    if set(groups) - set(baseline):
        errors.append("Coverage group has no registered baseline")
    for module, thresholds in (minimums or {}).items():
        if module in members or module not in current:
            errors.append(f"Invalid new-module coverage scope: {module}")
            continue
        members.append(module)
        actual = current[module]
        for metric, covered, total in (
            ("lines", "covered_lines", "num_statements"),
            ("branches", "covered_branches", "num_branches"),
        ):
            if not 0 <= thresholds[metric] <= 100:
                raise ValueError("Invalid coverage minimum")
            if actual[total] and actual[covered] * 100 < thresholds[metric] * actual[total]:
                errors.append(f"New-module coverage below minimum: {module}/{metric}")
    if set(current) - set(members):
        errors.append(f"Coverage has unregistered modules: {sorted(set(current) - set(members))}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with (root / "configs/coverage.toml").open("rb") as stream:
        config = tomllib.load(stream)
    baseline_path = (root / config["baseline"]).resolve()
    if not baseline_path.is_relative_to(root):
        raise ValueError("Coverage baseline escapes the project")
    raw = baseline_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != config["baseline_sha256"]:
        raise ValueError("Coverage baseline hash differs")
    baseline = json.loads(raw)
    current = module_totals(json.loads(args.report.read_text(encoding="utf-8")))
    errors = compare_coverage(
        current, baseline["modules"], config.get("groups", {}), config.get("minimums", {})
    )
    print(
        json.dumps(
            {"status": "FAIL" if errors else "PASS", "modules": len(current), "errors": errors}
        )
    )
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
