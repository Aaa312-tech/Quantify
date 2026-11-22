"""Diagnostic replay from pre-change guard evidence, before compatibility registration."""

import logging
from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.evaluation import evaluate_accounts, experiment_folds, load_predictions
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha

root = Path(__file__).resolve().parents[3]
checks = Path(__file__).resolve().parent
output = root / ".git/core_readability_accounts"
receipt = checks / "history.json"
if output.exists() or receipt.exists():
    raise FileExistsError("Use new diagnostic output and receipt paths")
logging.basicConfig(level=logging.INFO, format="%(message)s")
scope = read(checks / "scope.json")
paths = Paths(root)
source = root / scope["active_evaluation"]
reference = read(source / "evaluation_summary.json")
run = root / scope["active_training"]
runtime = {p.name: sha(p) for p in sorted((root / "src/quantfolio").glob("*.py"))}
result = {
    "kind": "HISTORICAL_LEDGER_PARITY",
    "purpose": "Diagnostic only until registered; no guard bypass, fitting or new selection.",
    "baseline_commit": scope["baseline_commit"],
    "scope_sha256": sha(checks / "scope.json"),
    "audit_sha256": sha(Path(__file__)),
    "runtime_sha256": runtime,
    "subjects": {
        key: {"sha256": value["sha256"], "source_sha256": value["sources"]}
        for key, value in scope["subjects"].items()
    },
    "records": [],
}
try:
    if not scope["baseline_guards_passed"]:
        raise ValueError("No original guarded evidence")
    for relative, expected in scope["inputs_sha256"].items():
        if sha(root / relative) != expected:
            raise ValueError(f"Input changed: {relative}")
    for relative, subject in scope["subjects"].items():
        if sha(root / relative) != subject["sha256"]:
            raise ValueError(f"Subject changed: {relative}")
    bundle = load_bundle(paths)
    try:
        experiment_folds(paths, bundle, run)
    except ValueError as error:
        if "Execution source changed" not in str(error):
            raise
        result["unregistered_guard_rejected"] = True
    else:
        raise ValueError("Equivalence must be checked before registration")
    maps, schedule, choices, start, end = load_predictions(
        bundle, run, scope["folds"], scope["training_status"]["identity"]
    )
    evaluated = evaluate_accounts(
        bundle,
        maps,
        schedule,
        start,
        end,
        [10, 20, 30],
        list(reference["results"]["10bps"]),
        output,
        turnover=paths.config.get("turnover_policy", "legacy"),
    )
    for key, value in evaluated.items():
        if value != reference[key]:
            raise ValueError(f"Evaluation field differs: {key}")
    accounts = 0
    for relative, expected in reference["artifacts"].items():
        old, new = source / relative, output / relative
        old_sha, new_sha = sha(old), sha(new)
        if old_sha != expected or new_sha != expected:
            raise ValueError(f"Compressed ledger bytes differ: {relative}")
        ledger = load_gzip(new)
        accounts += len(ledger) if isinstance(ledger, list) else 1
        result["records"].append({"path": relative, "before": old_sha, "after": new_sha})
    if accounts != reference["accounts"]:
        raise ValueError("Account count differs")
    if runtime != {p.name: sha(p) for p in sorted((root / "src/quantfolio").glob("*.py"))}:
        raise ValueError("Source changed during replay")
    result.update(
        status="PASS",
        accounts=accounts,
        ledger_files=len(result["records"]),
        reconciliation_checks=evaluated["reconciliation_checks"],
        execution_checks=evaluated["execution_checks"],
        all_result_fields_equal=True,
    )
except Exception as error:
    result.update(status="FAIL", error=f"{type(error).__name__}: {error}")
    raise
finally:
    result["completed_at"] = now()
    save(receipt, result, immutable=True)
print(f"PASS: {accounts} accounts and {len(result['records'])} compressed ledgers")
