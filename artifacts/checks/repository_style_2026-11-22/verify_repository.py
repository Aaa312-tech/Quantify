"""Check repository presentation changes against the published research baseline."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from string import Template
from urllib.parse import unquote, urlsplit

from quantfolio.cli import _parser
from quantfolio.storage import now, read, save, sha

OUTPUT = Path(__file__).resolve().parent
ROOT = OUTPUT.parents[2]


class Report(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.cells = []
        self.summary = []
        self.links = []
        self.active = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag in {"td", "strong"}:
            self.active = tag
        self.links.extend(value for key, value in attrs if key in {"href", "src"})

    def handle_endtag(self, tag):
        if tag == self.active:
            self.active = None

    def handle_data(self, text):
        if self.active and text.strip():
            (self.cells if self.active == "td" else self.summary).append(text.strip())


def git_bytes(commit, name):
    return subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)


def equal_files(expected):
    def check(item):
        name, digest = item
        path = ROOT / name
        if not path.resolve().is_relative_to(ROOT) or not path.is_file() or sha(path) != digest:
            return name
        return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        differences = [name for name in pool.map(check, expected.items()) if name]
    if differences:
        raise ValueError(f"Protected files changed: {differences[:10]}")
    return len(expected)


def documentation():
    documents = [
        ROOT / name
        for name in ("README.md", "CONTRIBUTING.md", "AGENTS.md", "CHANGELOG.md", "NOTICE.md")
    ]
    documents += sorted((ROOT / "docs").glob("*.md"))
    link_count = 0
    commands = []
    parser = _parser()
    for path in documents:
        text = path.read_text("utf-8")
        prose = re.sub(r"```.*?```", "", text, flags=re.S)
        for href in re.findall(r"\]\(([^)]+)\)", prose):
            parsed = urlsplit(href.strip("<>"))
            if parsed.scheme or parsed.netloc:
                continue
            target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            if not target.is_relative_to(ROOT) or not target.exists():
                raise ValueError(f"Missing local link in {path}: {href}")
            if parsed.fragment and target.suffix == ".md":
                headings = re.findall(r"^#+\s+(.+)$", target.read_text("utf-8"), flags=re.M)
                anchors = {
                    re.sub(r"[^\w\-\u4e00-\u9fff ]", "", heading.lower()).replace(" ", "-")
                    for heading in headings
                }
                if unquote(parsed.fragment) not in anchors:
                    raise ValueError(f"Missing anchor in {path}: {href}")
            link_count += 1
        for block in re.findall(r"```(?:powershell|sh)\n(.*?)```", text, flags=re.S):
            for line in block.splitlines():
                if " -m quantfolio " not in line:
                    continue
                arguments = shlex.split(line.split(" -m quantfolio ", 1)[1].replace("\\", "/"))
                parser.parse_args(arguments)
                commands.append(
                    {"document": path.relative_to(ROOT).as_posix(), "arguments": arguments}
                )
    return {
        "files": len(documents),
        "local_links": link_count,
        "parsed_commands": len(commands),
        "commands": commands,
    }


def report_parity(baseline):
    old = ROOT / "reports/latest"
    new = ROOT / "reports/experiments/recipient_entitlements_2026-11-22"
    before = Report((old / "index.html").read_text("utf-8"))
    text = (new / "index.html").read_text("utf-8")
    after = Report(text)
    if before.summary != after.cells[:4] or before.cells != after.cells[4:]:
        raise ValueError("Report summary or cost table changed")
    if sha(old / "equity_curve.png") != sha(new / "equity_curve.png"):
        raise ValueError("Equity curve bytes changed")
    name = "src/quantfolio/templates/report.html"
    before_fields = sorted(Template(git_bytes(baseline, name).decode("utf-8")).get_identifiers())
    after_fields = sorted(Template((ROOT / name).read_text("utf-8")).get_identifiers())
    if before_fields != after_fields:
        raise ValueError("Template input fields changed")
    for href in after.links:
        if not (new / href).resolve().is_file():
            raise ValueError(f"Report link missing: {href}")
    evaluation = read(
        ROOT / "artifacts/evaluations/recipient_entitlements_2026-11-22/evaluation_summary.json"
    )
    for field in ("trade_start", "trade_end", "data_cutoff"):
        if evaluation[field] not in text:
            raise ValueError(f"Report date missing: {field}")
    if "$title" in text or "$rows" in text:
        raise ValueError("Unresolved template placeholders")
    return {
        "summary_cells_equal": 4,
        "cost_cells_equal": len(before.cells),
        "equity_curve_byte_equal": True,
        "template_fields_equal": before_fields,
        "html_sha256": sha(new / "index.html"),
        "chart_sha256": sha(new / "equity_curve.png"),
    }


def main():
    scope = read(OUTPUT / "scope.json")
    baseline = scope["baseline_commit"]
    raw = git_bytes(baseline, "configs/integrity_manifest.json")
    if hashlib.sha256(raw).hexdigest() != scope["manifest_sha256"]:
        raise ValueError("Baseline manifest identity mismatch")
    original = json.loads(raw)["files"]
    current = read(ROOT / "configs/integrity_manifest.json")["files"]
    if any(current.get(name) != digest for name, digest in original.items()):
        raise ValueError("Original manifest entry changed")
    frozen_count = equal_files(original)
    protected_count = equal_files(scope["protected_files"])
    runtime_count = equal_files(scope["runtime_sha256"])
    history = ROOT / "artifacts/checks/paper_refactor_2026-11-22/history.json"
    historical_source = {
        f"src/quantfolio/{name}": digest for name, digest in read(history)["runtime_sha256"].items()
    }
    if historical_source != scope["runtime_sha256"]:
        raise ValueError("Historical parity evidence does not bind current source")
    for name in ("scripts/run.ps1", "scripts/sync.ps1"):
        if (ROOT / name).exists():
            raise ValueError(f"Obsolete wrapper remains: {name}")
    documentation_check = documentation()
    report_check = report_parity(baseline)
    guards = read(OUTPUT / "guards.json")
    wheel = read(OUTPUT / "wheel.json")
    if guards["status"] != "PASS" or wheel["status"] != "PASS":
        raise ValueError("Guard or wheel checks failed")
    log = (OUTPUT / "checks.log").read_text("utf-8-sig")
    if "799 passed, 1 skipped" not in log or "All checks passed!" not in log:
        raise ValueError("Local checks incomplete")
    result = {
        "status": "PASS",
        "completed_at": now(),
        "baseline_commit": baseline,
        "goal_status": "paused",
        "original_frozen_files_unchanged": frozen_count,
        "additional_protected_files_unchanged": protected_count,
        "python_runtime_files_unchanged": runtime_count,
        "documentation": documentation_check,
        "report": report_check,
        "tests": {"passed": 799, "host_symlink_skip": 1, "log_sha256": sha(OUTPUT / "checks.log")},
        "guards_sha256": sha(OUTPUT / "guards.json"),
        "wheel_sha256": sha(OUTPUT / "wheel.json"),
        "historical_evidence_reused": {
            "path": history.relative_to(ROOT).as_posix(),
            "sha256": sha(history),
            "reason": "All Python runtime files, original inputs, predictions and ledgers are byte-identical to the verified baseline.",
        },
        "new_model_fits": 0,
        "new_historical_replays": 0,
    }
    save(OUTPUT / "review.json", result, immutable=True)
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key
                in {
                    "status",
                    "original_frozen_files_unchanged",
                    "additional_protected_files_unchanged",
                    "python_runtime_files_unchanged",
                }
            }
        )
    )


if __name__ == "__main__":
    main()
