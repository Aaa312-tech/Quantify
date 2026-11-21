"""Preserve captured command bytes while correcting the repository whitespace policy."""

import hashlib
import subprocess
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
checks = root / "artifacts/checks/window_range_2026-11-21"
p = read(root / "configs/protocols/window_range.json")
completion = read(checks / "completion.json")
initial_commit = "f5bfb1b4e18c8bda14c881b516caf6115329256a"
help_file = checks / "cli_help.txt"
lines = help_file.read_bytes().splitlines(keepends=True)
assert len(lines) == 9
assert all(line.endswith(b"\r\n") and not line[:-2].endswith((b" ", b"\t")) for line in lines)
initial_manifest = read(root / "configs/integrity_manifest.json")
assert initial_manifest["files"][help_file.relative_to(root).as_posix()] == sha(help_file)
failure = (checks / "whitespace_before.log").read_text(encoding="utf-8")
assert failure.count(": trailing whitespace.") == 9
assert all(
    "cli_help.txt:" in line for line in failure.splitlines() if ": trailing whitespace." in line
)
before = subprocess.check_output(["git", "show", initial_commit + ":.gitattributes"])
after = (root / ".gitattributes").read_bytes()
expected = before.replace(
    b"# Frozen receipts and executed scripts may retain native Windows line endings.\n",
    b"# Frozen receipts, executed scripts and captured text may retain native line endings.\n",
).replace(
    b"artifacts/checks/**/*.py whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n",
    b"artifacts/checks/**/*.py whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n"
    b"artifacts/checks/**/*.txt whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n",
)
assert after == expected
checked = subprocess.run(
    ["git", "diff", "--check", p["baseline_commit"], "HEAD"], capture_output=True, text=True
)
assert checked.returncode == 0, checked.stdout + checked.stderr
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
assert sources == completion["source_sha256"]
assert sha(root / completion["current_plan"]["path"]) == completion["current_plan"]["sha256"]
save(
    checks / "publication_followup.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "initial_commit": initial_commit,
        "initial_whitespace_exit": 2,
        "initial_error_count": 9,
        "failure_log_sha256": sha(checks / "whitespace_before.log"),
        "checker_sha256": sha(Path(__file__)),
        "help_output_sha256": sha(help_file),
        "captured_bytes_unchanged": True,
        "actual_trailing_spaces_or_tabs": False,
        "attributes_before_sha256": hashlib.sha256(before).hexdigest(),
        "attributes_after_sha256": hashlib.sha256(after).hexdigest(),
        "corrected_range_check_exit": checked.returncode,
        "source_sha256": sources,
        "completion_sha256": sha(checks / "completion.json"),
        "scope": "The initial pre-staging diff omitted untracked help output. The later staged check exposed native CRLF, after which the initial commit was retained. Extend the existing CR-at-EOL rule to captured text without normalizing any frozen bytes or permitting real trailing spaces. Runtime, data, tests, results and plan are unchanged; final staged-range and complete integrity checks follow.",
    },
    immutable=True,
)
print("CAPTURED TEXT WHITESPACE POLICY VERIFIED; ORIGINAL BYTES PRESERVED", flush=True)
