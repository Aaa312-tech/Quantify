"""Publish exact earlier review bytes and resolve their private path references."""

import re
from pathlib import Path

from quantfolio.storage import now, read, save, sha


def collect(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in hash_keys and isinstance(item, str) and re.fullmatch("[a-f0-9]{64}", item):
                hash_refs.add(item)
            collect(key)
            collect(item)
    elif isinstance(value, list):
        for item in value:
            collect(item)
    elif isinstance(value, str) and value.startswith(".git/"):
        path = root / value
        if path.is_file():
            explicit_paths.add(path)


root = Path.cwd()
base = Path(__file__).resolve().parent
frontier = root / ".git/distribution_frontier_2026_11_22"
hash_keys = {
    "script_sha256",
    "initial_script_sha256",
    "writer_sha256",
    "progress_writer_sha256",
    "entered_observations_sha256",
}
hash_refs, explicit_paths = set(), set()
public = {}
for path in sorted(base.rglob("*")):
    if path.is_file() and path.suffix in {".json", ".py", ".png"}:
        public.setdefault(sha(path), path)
private = {}
for path in [*sorted((root / ".git").glob("*.py")), *sorted((root / ".git").glob("*.json"))]:
    private.setdefault(sha(path), path)
selected = set(frontier.glob("*.json"))
visited = set()
for path in base.rglob("*.json"):
    collect(read(path))
while True:
    for path in selected - visited:
        if path.suffix == ".json":
            collect(read(path))
        visited.add(path)
    selected.update(explicit_paths)
    selected.update(
        private[value] for value in hash_refs if value not in public and value in private
    )
    if selected == visited:
        break
missing = hash_refs - set(public) - set(private)
if missing:
    raise ValueError(f"Referenced executed sources not located: {sorted(missing)}")
aliases, added = [], 0
for original in sorted(selected):
    original = original.resolve()
    if not original.is_relative_to(root / ".git"):
        raise ValueError("Private evidence reference escaped .git")
    expected = sha(original)
    if expected in public:
        target = public[expected]
        copied = False
    else:
        relative = (
            original.relative_to(frontier)
            if original.is_relative_to(frontier)
            else Path("sources") / original.name
        )
        target = base / "prior_reviews" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha(target) != expected:
                raise ValueError(f"Import destination differs: {target}")
        else:
            with target.open("xb") as stream:
                stream.write(original.read_bytes())
            added += 1
        copied = True
        public[expected] = target
    if sha(target) != expected:
        raise ValueError("Imported evidence differs")
    aliases.append(
        {
            "original_path": original.relative_to(root).as_posix(),
            "path": target.relative_to(root).as_posix(),
            "sha256": expected,
            "copied_to_public_evidence": copied,
        }
    )
save(
    base / "prior_reviews" / "publication_import.json",
    {
        "status": "EXACT_PRIOR_EVIDENCE_PUBLISHED_LOCALLY",
        "recorded_at": now(),
        "script_sha256": sha(Path(__file__)),
        "aliases": aliases,
        "new_files": added,
        "executed_hash_references_resolved": len(hash_refs),
        "all_alias_bytes_verified": True,
        "boundary": "Exact historical bytes only. Alias entries resolve earlier private references without editing the original receipts or claiming new visual inspection. Initial reader/arithmetic failures, diagnostic statuses and their executed sources remain unchanged. These imported diagnostics do not independently accept fields or alter runtime inputs.",
        "runtime_inputs_changed": False,
    },
    immutable=True,
)
print(
    "IMPORTED", added, "FILES", "ALIASES", len(aliases), "EXECUTED_HASH_REFERENCES", len(hash_refs)
)
