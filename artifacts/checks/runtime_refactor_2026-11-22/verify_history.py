"""Compare the shared evaluator's full account matrix with the active frozen ledgers."""

from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.evaluation import evaluate_accounts, experiment_folds, load_predictions
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha

root = Path(__file__).resolve().parents[3]
checks = Path(__file__).resolve().parent
output = root / ".git/runtime_refactor_accounts"
receipt = checks / "history.json"
if output.exists() or receipt.exists():
    raise FileExistsError("Use new diagnostic output and receipt paths")
paths = Paths(root)
source = paths.artifact_path(paths.config["evaluation"], "evaluations")
reference = read(source / "evaluation_summary.json")
run = paths.artifact_path(reference["source_run"], "experiments")
bundle = load_bundle(paths)
status, folds = experiment_folds(paths, bundle, run)
maps, schedule, choices, start, end = load_predictions(bundle, run, folds, status["identity"])
result = {
    "purpose": "Maintenance parity; no new fitting, candidate selection or forward trades.",
    "source": source.relative_to(root).as_posix(),
    "reference_sha256": sha(source / "evaluation_summary.json"),
    "audit_sha256": sha(Path(__file__)),
    "source_sha256": {
        name: sha(root / "src/quantfolio" / f"{name}.py")
        for name in ("training", "evaluation", "study")
    },
    "records": [],
}
try:
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
            raise ValueError(f"Full compressed ledger bytes differ: {relative}")
        ledger = load_gzip(new)
        accounts += len(ledger) if isinstance(ledger, list) else 1
        result["records"].append({"path": relative, "before": old_sha, "after": new_sha})
    if accounts != reference["accounts"]:
        raise ValueError("Full account count differs")
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
print(f"PASS: {accounts} full accounts and {len(result['records'])} compressed ledger files")
