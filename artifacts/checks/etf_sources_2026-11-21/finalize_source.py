"""Freeze the ETF source review while proving the numerical baseline unchanged."""

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

from quantfolio.dataset import check_integrity
from quantfolio.planning import source_hashes
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol = read(root / "configs/protocols/etf_sources.json")
out = root / protocol["review_output"]
manifest = read(paths.manifest)
if sha(paths.manifest) != protocol["baseline_manifest_sha256"]:
    raise ValueError("Frozen registry changed since source registration")
baseline_files = dict(manifest["files"])
baseline = protocol["baseline_commit"]
if sha(paths.bundle) != protocol["baseline_bundle_sha256"] or sha(paths.configuration_path) != protocol["baseline_configuration_sha256"]:
    raise ValueError("Active model input or configuration changed")
modules = {}
for file in sorted((root / "src/quantfolio").glob("*.py")):
    relative = file.relative_to(root).as_posix()
    before = subprocess.check_output(["git", "show", baseline + ":" + relative])
    if file.read_bytes() != before:
        raise ValueError("Runtime source differs: " + relative)
    modules[file.name] = sha(file)
if subprocess.check_output(["git", "diff", baseline, "--name-only", "--", "tests", "configs/project.toml"]).strip():
    raise ValueError("Tests or active settings changed")
plan_file = root / "artifacts/plans/dated_industries_2026-11-23/plan.json"
plan = read(plan_file)
if plan["source_sha256"] != source_hashes() or sha(plan_file) != baseline_files[plan_file.relative_to(root).as_posix()]:
    raise ValueError("Frozen plan or dependency binding changed")
fields = read(out / "field_closure.json")
structural = read(out / "structural_review.json")
if not (fields["accepted_summary_pdfs"] == fields["listing_dates_cross_checked"] == 199
        and fields["funds"] == fields["pre_model_anchors"] == 22
        and structural["existing_split_actions_checked"] == 8 and structural["document_count"] == 17
        and not fields["model_inputs_changed"] and structural["new_actions_applied"] == 0):
    raise ValueError("ETF source-review closure missing")
for summary, source in [(out / "review.json", out / "review_source.py"),
                        (out / "field_closure.json", out / "field_review_source.py"),
                        (out / "structural_review.json", out / "structural_review_source.py")]:
    if read(summary)["source_sha256"] != sha(source):
        raise ValueError("Review source changed after execution")
read_check = check_integrity(paths)
folders = sorted((root / "data/raw").glob("etf_*"))
receipt_count = 0
failed_receipts = []
for folder in folders:
    for file in folder.rglob("*.receipt.json"):
        receipt = read(file)
        if receipt["status"] == "RECEIVED":
            target = (root / receipt["path"]).resolve()
            if not target.is_relative_to(root) or sha(target) != receipt["sha256"]:
                raise ValueError("Source receipt hash mismatch")
            receipt_count += 1
        else:
            failed_receipts.append(file.relative_to(root).as_posix())
logs = out / "logs"
logs.mkdir()
for name, source in {
    "checks.log": "etf_checks.log", "collection.log": "etf_collection.log",
    "pagination_followup.log": "etf_followup.log", "summary_review.log": "etf_review_followup.log",
    "field_review.log": "etf_field_review.log", "structural_review.log": "etf_structural_review.log",
    "manager_collection.log": "etf_manager.log",
}.items():
    (logs / name).write_bytes((root / ".git" / source).read_bytes())
checks_log = (logs / "checks.log").read_text(encoding="utf-8-sig")
if "375 passed, 1 skipped" not in checks_log or "All checks passed!" not in checks_log:
    raise ValueError("Local Ruff/pytest result missing")
visuals = sorted((out / "visuals").glob("*.png"))
save(out / "visual_review.json", {
    "reviewed_at": now(), "scope": "Four representative summaries, the rejected prospectus cover, every date-layout conflict, both benchmark page boundaries, all five leaves of the manager reading copy, the early bank anchor and index-amendment pages 1/2.",
    "images": [{"path": f.relative_to(root).as_posix(), "sha256": sha(f)} for f in visuals],
    "findings": "The original manager page tree remains defective; its reading copy preserves the warning page and all leaf content. Date labels and benchmark-row boundaries support the explicit adjudications.",
    "complete_visual_review_of_all_source_pages": False,
}, immutable=True)
new_roots = [*folders, root / protocol["processed_output"], out]
new_files = [f for folder in new_roots for f in folder.rglob("*") if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"]
new_files.append(root / "configs/protocols/etf_sources.json")
for file in new_files:
    key = file.relative_to(root).as_posix()
    if key in manifest["files"]:
        raise ValueError("Source review attempts to replace frozen bytes: " + key)
    manifest["files"][key] = sha(file)
completion_file = out / "completion.json"
save(completion_file, {
    "status": "VALIDATED_LOCALLY_SOURCE_ONLY", "completed_at": now(), "baseline_commit": baseline,
    "protocol_sha256": sha(root / "configs/protocols/etf_sources.json"), "finalizer_sha256": sha(Path(__file__)),
    "baseline_integrity": read_check, "old_frozen_files_unchanged": len(baseline_files),
    "new_frozen_files_including_completion": len(new_files) + 1,
    "expected_final_registry_files": len(manifest["files"]) + 1,
    "source_modules_unchanged": modules, "configuration_sha256": sha(paths.configuration_path),
    "bundle_sha256": sha(paths.bundle), "paper_plan_sha256": sha(plan_file), "paper_dependency_binding_exact": True,
    "summary_closure_sha256": sha(out / "field_closure.json"), "structural_closure_sha256": sha(out / "structural_review.json"),
    "received_source_receipts_checked": receipt_count, "failed_source_receipts_preserved": failed_receipts,
    "summary_pdfs_accepted": 199, "summary_pages": 1077, "funds": 22, "structural_notices": 17,
    "tests_passed": 375, "tests_skipped": 1, "refits": 0, "new_backtests": 0, "new_forward_observations": 0,
    "numerical_inputs_or_results_changed": False, "superiority_assessment": "NOT_ESTABLISHED",
    "remaining": ["exact historical publication time", "dated ETF constituent/holding weights and sector taxonomy mapping", "full amendment-content coverage", "historical universe selection bias", "other status/data gaps", "future paper observations", "competition fees and matching details"],
}, immutable=True)
manifest["files"][completion_file.relative_to(root).as_posix()] = sha(completion_file)
save(paths.manifest, manifest)
result = check_integrity(paths)
if result["files"] != read(completion_file)["expected_final_registry_files"]:
    raise ValueError("Final frozen registry count differs")
for relative in ("README.md", "docs/etf_sources.md", "docs/remediation.md", "docs/data_contract.md", "docs/reproducibility.md"):
    file = root / relative
    for target in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
        if target.startswith(("https://", "http://", "#")):
            continue
        target = unquote(target.split("#")[0])
        if target and not (file.parent / target).exists():
            raise ValueError("Local documentation link missing: " + relative + " -> " + target)
save(root / ".git/etf_source_local_validation.json", {"status": "PASS", "completed_at": now(),
    "integrity": result, "completion_sha256": sha(completion_file), "manifest_sha256": sha(paths.manifest),
    "docs_links_checked": True}, immutable=True)
print({"status": "PASS", "old_files": len(baseline_files), "new_files": len(new_files) + 1,
       "frozen_total": result["files"], "source_receipts": receipt_count, "raw_pdfs": len([f for folder in folders for f in folder.rglob("*.pdf")])}, flush=True)
