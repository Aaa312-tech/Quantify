"""Close the source-only industry review without altering research inputs."""

import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from quantfolio.dataset import check_integrity
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha

root = Path.cwd()
paths = Paths(root)
checks = root / "artifacts/checks/industry_cells_2026-11-21"
protocol_path = root / "configs/protocols/industry_cell_review.json"
protocol = read(protocol_path)
review = read(checks / "review.json")
started = read(checks / "started.json")
baseline = "cf5f0340b26ac21814f57672812b85e90eb9b65a"
manifest = read(paths.manifest)
old = json.loads(subprocess.check_output(["git", "show", baseline + ":configs/integrity_manifest.json"]))
assert len(old["files"]) == 23742
assert len(manifest["files"]) == 23769
assert all(manifest["files"].get(name) == value for name, value in old["files"].items())
integrity = check_integrity(paths)
assert integrity == {"status": "PASS", "files": 23769}
assert sha(protocol_path) == review["protocol_sha256"] == started["protocol_sha256"]
assert sha(root / "src/quantfolio/industry_sources.py") == review["source_sha256"] == started["source_sha256"]
assert sha(root / "configs/project.toml") == review["configuration_sha256"] == started["configuration_sha256"]
assert sha(root / protocol["source_bundle"]) == protocol["source_bundle_sha256"]

inventory = []
stock_rows = stock_pages = alias_rows = alias_pages = findings = 0
aliases = {}
for item in review["sources"]:
    assert item["status"] == "PARSED_AND_CROSS_CHECKED"
    assert sha(root / item["source"]) == item["source_sha256"]
    assert sha(root / item["output"]) == item["sha256"]
    data = load_gzip(root / item["output"])
    records, pages = data["records"], data["pages"]
    assert len(records) == data["rows"] == item["rows"]
    assert [p["page"] for p in pages] == list(range(1, len(pages) + 1))
    assert all(p["text_comparison"] == "PASS" for p in pages)
    assert Counter(r["page"] for r in records) == {p["page"]: p["rows"] for p in pages}
    key = "old_code" if item["aliases"] else "code"
    assert len({r[key] for r in records}) == len(records)
    assert all(len(r["raw_cells"]) == (5 if item["aliases"] else 8) for r in records)
    assert all(len(r["bbox"]) == 4 and r["row"] > 0 for r in records)
    if item["aliases"]:
        alias_rows += len(records)
        alias_pages += len(pages)
        assert len({r["new_code"] for r in records}) == len(records)
        assert [r["number"] for r in records] == list(range(1, len(records) + 1))
        for row in records:
            assert row["old_code"] not in aliases or aliases[row["old_code"]] == row["new_code"]
            aliases[row["old_code"]] = row["new_code"]
    else:
        stock_rows += len(records)
        stock_pages += len(pages)
        codes = [r["code"] for r in records]
        assert codes == sorted(codes)
        issues = [f for r in records for f in r["taxonomy_findings"]]
        assert all(f["problem"] == "name_mismatch" for f in issues)
        findings += len(issues)
    inventory.append({"source": item["source"], "sha256": item["source_sha256"],
                      "rows": len(records), "pages": len(pages), "aliases": item["aliases"]})
assert (stock_rows, stock_pages, alias_rows, alias_pages, findings) == (37625, 891, 254, 7, 320)
assert len(aliases) == len(set(aliases.values())) == 248

initial = read(root / protocol["initial_review"])
assert initial["status"] == "REVIEW_REQUIRED"
assert sha(root / protocol["initial_review"]) == protocol["initial_review_sha256"]
prior_matches = []
for item in initial["sources"]:
    if item["status"] != "PARSED_AND_CROSS_CHECKED":
        continue
    before = load_gzip(root / item["output"])
    current = next(s for s in review["sources"] if s["source"] == item["source"])
    after = load_gzip(root / current["output"])
    assert len(before["records"]) == len(after["records"])
    for left, right in zip(before["records"], after["records"]):
        assert all(right.get(k) == value for k, value in left.items())
    for left, right in zip(before["pages"], after["pages"]):
        assert all(right.get(k) == value for k, value in left.items())
    prior_matches.append({"source": item["source"], "rows": item["rows"], "status": "EXACT_PRIOR_CELLS"})
assert len(prior_matches) == 6

structure_path = root / "artifacts/checks/industry_preparation_2026-11-21/source_structure.json"
structure = read(structure_path)
coverage = read(checks / "coverage.json")
collection_path = root / protocol["collection"]
collection = read(collection_path)
releases = []
for item in sorted(structure["tables"], key=lambda x: x["catalogue_date"]):
    matched = next(s for s in review["sources"] if s["source"] == item["source"])
    covered = next(s for s in coverage["releases"] if s["catalogue_date"] == item["catalogue_date"])
    article = next(s for s in collection["articles"] if s["published_date"] == item["catalogue_date"])
    metadata = {str(k): str(v) for k, v in (PdfReader(root / item["source"]).metadata or {}).items()}
    assert metadata == item["metadata"]
    assert len(PdfReader(root / item["source"]).pages) == matched["pages"]
    assert item["first_observed_session_after_catalogue_date"] == covered["following_observed_session"]
    releases.append({"title": item["title"], "catalogue_date": item["catalogue_date"],
                     "source": item["source"], "source_sha256": item["source_sha256"],
                     "article": article["article"], "source_url": item["url"], "pdf_metadata": metadata,
                     "first_observed_session_after_catalogue_date": covered["following_observed_session"],
                     "intraday_available_at": None, "historical_bytes_certified": False})
shell_path = root / "data/raw/industry_standards_2026-11-21/capco_release_2025-09-30.html"
shell = shell_path.read_text(encoding="utf-8")
title = re.search(r"<title>(.*?)</title>", shell, re.S).group(1).strip()
assert title == "中国上市公司协会" and "2025-09-30" not in shell
standard = read(root / "artifacts/checks/industry_standards_2026-11-21/codebook_comparison.json")
assert standard["differences"] == []
assert standard["older_counts"] == standard["later_counts"] == {"section": 19, "subclass": 10, "division": 91}
save(checks / "availability_review.json", {
    "status": "CATALOGUE_DATE_DIAGNOSTIC_ONLY", "checked_at": now(),
    "source_structure_sha256": sha(structure_path), "collection_sha256": sha(collection_path),
    "coverage_sha256": sha(checks / "coverage.json"), "releases": releases,
    "failed_corroboration": {"source": shell_path.relative_to(root).as_posix(), "sha256": sha(shell_path),
        "bytes": shell_path.stat().st_size, "page_title": title, "status": "GENERIC_SITE_SHELL",
        "supports_release_date": False, "reason": "The received page contains no release body or date; HTTP success does not validate publication content."},
    "codebook_comparison": {"source": "artifacts/checks/industry_standards_2026-11-21/codebook_comparison.json",
        "stated_2024_publication_and_effective_date": standard["stated_publication_and_effective_date"],
        "compared_published_levels": 120, "differences": 0,
        "scope": "Section, manufacturing subclass and division codes, names and parents only; not group-level text or permission to backdate a later standard."},
    "convention": "Coverage uses the first observed session strictly after the catalogue date; it is a retrospective convention, not certified historical intraday availability.",
    "limitations": ["Statistical periods and June/December work-start dates are not public availability dates.",
                    "Migrated URL directories, PDF creation metadata and present retrieval times do not prove original release times or historical content versions.",
                    "No same-day availability, future-table backfill or carry-forward for a security omitted from a later release is assumed.",
                    "Current-universe conditioning, earlier releases, ETF grouping and integration remain separate work."],
    "assignments_applied": 0}, immutable=True)

tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", baseline, "src/quantfolio"], text=True).splitlines()
core = {}
for name in tracked:
    if not name.endswith(".py") or name in ("src/quantfolio/cli.py", "src/quantfolio/__init__.py"):
        continue
    expected = hashlib.sha256(subprocess.check_output(["git", "show", baseline + ":" + name])).hexdigest()
    assert sha(root / name) == expected, name
    core[name] = expected
plan_path = root / "artifacts/plans/local_gaps_2026-11-23/plan.json"
plan = read(plan_path)
assert sha(plan_path) == "16cb4982092002eae34bda2b1b5cf36ae70c11d62e4f6f6cfa1a6610375a7756"
assert plan["active_configuration"] == paths.config
for name, expected in plan["input_sha256"].items():
    assert sha(root / name) == expected, name
for module, expected in plan["source_sha256"].items():
    assert sha(root / f"src/quantfolio/{module}.py") == expected, module
test_log = root / ".git/industry_cell_full_tests.log"
assert "344 passed, 1 skipped" in test_log.read_text(encoding="utf-8")
(checks / "local_tests.log").write_bytes(test_log.read_bytes())
evidence_sources = {
    "coverage_audit.py": root / ".git/audit_industry_coverage.py",
    "codebook_audit.py": root / ".git/compare_industry_codebooks.py",
    "source_closure.py": Path(__file__),
}
source_hashes = {}
for name, file in evidence_sources.items():
    target = checks / name
    target.write_bytes(file.read_bytes())
    source_hashes[target.relative_to(root).as_posix()] = sha(target)
save(checks / "verification_sources.json", {
    "sources": source_hashes,
    "scope": "Frozen exact audit sources used in this review; immutable original outputs are not overwritten by rerunning them."}, immutable=True)
save(checks / "completion.json", {
    "status": "SOURCE_REVIEW_COMPLETE_NOT_ACTIVATED", "completed_at": now(),
    "baseline_commit": baseline, "protocol_sha256": sha(protocol_path),
    "source_review_sha256": sha(checks / "review.json"), "reader_sha256": review["source_sha256"],
    "prior_frozen_integrity": integrity, "pre_phase_frozen_files_preserved": len(old["files"]),
    "source_inventory": inventory, "stock_rows": stock_rows, "stock_pages": stock_pages,
    "alias_rows": alias_rows, "alias_pages": alias_pages, "unique_bse_pairs": len(aliases),
    "label_findings_retained": findings, "prior_successful_sources": prior_matches,
    "prior_failed_sources_retained": sum(s["status"] != "PARSED_AND_CROSS_CHECKED" for s in initial["sources"]),
    "unchanged_core_sources": core, "configuration_sha256": sha(root / "configs/project.toml"),
    "bundle_sha256": sha(root / protocol["source_bundle"]), "paper_plan_sha256": sha(plan_path),
    "paper_plan_source_bindings_checked": len(plan["source_sha256"]),
    "paper_plan_input_bindings_checked": len(plan["input_sha256"]),
    "tests": {"passed": 344, "skipped": 1, "skip_reason": "Host does not permit directory symlinks",
              "log_sha256": sha(checks / "local_tests.log")},
    "environment": {"python": platform.python_version(), "platform": platform.platform(),
                    "packages": {p: importlib.metadata.version(p) for p in ("pypdf", "PyMuPDF", "pytest", "ruff")}},
    "assignments_applied": 0, "models_retrained": 0, "forward_observations_added": 0,
    "scope": "Source extraction and evidence review only. Source/data/configuration identities preserve current predictions and accounts without a numerical change or new profitability claim."}, immutable=True)
print(json.dumps({"status": "PASS", "stock_rows": stock_rows, "stock_pages": stock_pages,
                  "alias_rows": alias_rows, "unique_bse_pairs": len(aliases),
                  "frozen_files_unchanged": integrity["files"], "core_sources_unchanged": len(core),
                  "plan_bindings_unchanged": len(plan["source_sha256"]) + len(plan["input_sha256"])}, ensure_ascii=False))
