"""Audit all fitted row identities, status exposure, model gates and inner quality."""

from collections import Counter
from pathlib import Path

import numpy as np

from quantfolio.dataset import eligible_rows, label_mask, load_bundle, load_snapshot
from quantfolio.models import training_targets
from quantfolio.settings import Paths
from quantfolio.storage import array_sha, digest, load_gzip, now, read, save, sha

root = Path.cwd()
p = read(root / "configs/protocols/declared_payments.json")
paths = Paths(root, p["configuration"])
bundle = load_bundle(paths)
table, calendar = bundle["table"], bundle["calendar"]
old_table = load_snapshot(paths, root / p["source_bundle"], p["source_data_cutoff"])["table"]
assert read(root / p["checks_output"] / "input_scope.json")["status"] == "PASS"
restored_five_day = set()

run, checks = root / p["training_output"], root / p["checks_output"]
expectations_file = checks / "target_expectations.json"
expectations = read(expectations_file)
assert expectations["status"] == "FROZEN_BEFORE_TRAINING"
assert expectations["model_code_sha256"] == sha(root / "src/quantfolio/models.py")
expected_targets = {(row["window"], row["stage"]): row for row in expectations["records"]}
assert len(expected_targets) == 24
status = read(run / "training_status.json")
assert status["status"] == "COMPLETE"
sources = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
assert read(run / "protocol.json")["specification"]["source_sha256"] == sources
status_protocol = read(root / "configs/protocols/historical_status_repair.json")
facts = read(root / status_protocol["review"])["facts"]
flagged = np.zeros(len(table), dtype=bool)
for fact in facts:
    indices = np.flatnonzero((calendar >= fact["start"]) & (calendar < fact["end"]))
    flagged |= (
        table.symbol.eq(fact["symbol"]).to_numpy() & table.date_index.isin(indices).to_numpy()
    )
assert not (flagged & table.eligible.to_numpy()).any()
records, decisions, gates = [], [], []
files = sorted((run / "training").glob("fold_*/result.json")) + [run / "final_fit/result.json"]
for file in files:
    result = read(file)
    fold, final = result["fold"], result["final_fit"]
    assert (
        result["identity"] == status["identity"] and not result["outer_results_used_for_selection"]
    )
    for stage, end, before, prediction_start, prediction_end, label in (
        (
            "validation_fit",
            fold["train_end"],
            fold["validation_start"],
            fold["validation_start"],
            fold["validation_end"],
            "validation",
        ),
        (
            "outer_fit",
            fold["test_start"] - 1 if final else fold["validation_end"],
            fold["test_start"] + int(final),
            fold["test_start"],
            fold["test_end"],
            "final" if final else "outer",
        ),
    ):
        rows = np.flatnonzero(label_mask(table, 5, fold["train_start"], end, before))
        fitted, target, weights, _ = training_targets(table, rows)
        old_rows = np.flatnonzero(label_mask(old_table, 5, fold["train_start"], end, before))
        old_fitted, old_target, old_weights, _ = training_targets(old_table, old_rows)
        added_reference = sorted(set(rows) - set(old_rows))
        assert set(old_rows) <= set(rows) and set(added_reference) <= restored_five_day
        np.testing.assert_array_equal(old_rows, rows)
        np.testing.assert_array_equal(old_fitted, fitted)
        np.testing.assert_array_equal(old_target, target)
        np.testing.assert_array_equal(old_weights, weights)
        common, left, right = np.intersect1d(
            old_fitted, fitted, assume_unique=True, return_indices=True
        )
        target_changed = old_target[left] != target[right]
        weight_changed = old_weights[left] != weights[right]
        target_change = {
            "old_reference_rows": len(old_rows),
            "restored_reference_rows": added_reference,
            "old_fit_rows": len(old_fitted),
            "added_fit_rows": sorted(set(fitted) - set(old_fitted)),
            "common_fit_rows": len(common),
            "common_targets_changed": int(target_changed.sum()),
            "common_weights_changed": int(weight_changed.sum()),
            "target_max_abs_change": float(
                np.max(np.abs(old_target[left] - target[right]), initial=0)
            ),
            "weight_max_abs_change": float(
                np.max(np.abs(old_weights[left] - weights[right]), initial=0)
            ),
            "old_target_sha256": array_sha(old_target),
            "old_weights_sha256": array_sha(old_weights),
            "scope": "Payment-only facts must leave reference cohorts, fitted rows, all targets and all weights exact.",
        }
        expected_target = expected_targets[fold["fold"], stage]
        for key, value in (
            ("reference_rows_sha256", array_sha(rows)),
            ("fit_rows_sha256", array_sha(fitted)),
            ("target_sha256", array_sha(target)),
            ("weights_sha256", array_sha(weights)),
        ):
            assert expected_target[key] == value, (fold["fold"], stage, key)
        assert (
            expected_target["restored_reference_rows"] == target_change["restored_reference_rows"]
        )
        assert expected_target["common_targets_changed"] == target_change["common_targets_changed"]
        predictions = eligible_rows(table, prediction_start, prediction_end)
        assert (
            not flagged[rows].any() and not flagged[fitted].any() and not flagged[predictions].any()
        )
        assert (
            not table.iloc[rows].industry.isna().any()
            and not table.iloc[predictions].industry.isna().any()
        )
        assert int(table.iloc[rows].end5.max()) < before
        # Independent eligible cohort counting; no production target helper decides support.
        groups = table.iloc[rows].groupby(["date_index", "industry"], sort=False)
        counts = groups.symbol.transform("size").to_numpy()
        expected_rows = rows[
            (counts >= 3) & (table.iloc[rows].momentum_percentile.to_numpy() >= 0.70)
        ]
        np.testing.assert_array_equal(fitted, expected_rows)
        identity = {
            "experiment": status["identity"],
            "fold": fold,
            "stage": label,
            "train_rows_sha256": array_sha(rows),
            "fit_rows_sha256": array_sha(fitted),
            "prediction_rows_sha256": array_sha(predictions),
            "model_code_sha256": sha(root / "src/quantfolio/models.py"),
        }
        for family in ("ridge", "tree"):
            metadata = read(file.parent / stage / family / "fit.json")
            assert metadata["identity"] == digest({**identity, "family": family})
            assert metadata["train_samples"] == len(fitted)
        records.append(
            {
                "window": fold["fold"],
                "stage": stage,
                "model_identities_checked": 2,
                "reference_rows": len(rows),
                "fit_rows": len(fitted),
                "prediction_rows": len(predictions),
                "reference_rows_sha256": array_sha(rows),
                "fit_rows_sha256": array_sha(fitted),
                "target_sha256": array_sha(target),
                "weights_sha256": array_sha(weights),
                "parent_target_comparison": target_change,
                "warning_reference_rows": 0,
                "warning_fit_rows": 0,
                "unknown_industry_rows": 0,
            }
        )
    decisions.append(
        {
            "window": fold["fold"],
            "model": result["selected_candidate"],
            "rules": result["selected_rules"],
        }
    )
    for name, candidate in result["inner_candidates"].items():
        if name == "momentum":
            continue
        gate = candidate["adoption"]
        gates.append(
            {
                "window": fold["fold"],
                "candidate": name,
                "qualifies": gate["qualifies"],
                "failed_requirements": [
                    key for key, value in gate["requirements"].items() if not value
                ],
                "requirements": gate["requirements"],
                "mean_advantage": gate["mean_advantage"],
                "lower90": gate["lower90"],
                "score": gate["score"],
                "factor_check": candidate["factor_check"],
            }
        )
assert len(records) == 24 and len(gates) == 48 and len(decisions) == 12
quality, unresolved, warning_buys, rejected = [], [], [], Counter()
counts = Counter()
for file in sorted(run.rglob("*.ledgers.json.gz")):
    if "/inner_" not in file.relative_to(run).as_posix():
        continue
    metadata = read(file.with_name(file.name.replace(".ledgers.json.gz", ".json")))
    assert metadata["ledger_sha256"] == sha(file)
    stored = load_gzip(file)
    assert all(
        check["status"] == "PASS" for check in stored["reconciliation"] + stored["execution_audit"]
    )
    for index, account in enumerate([*stored["episodes"], stored["continuous"]]):
        counts["accounts"] += 1
        ledger = account["ledger"]
        location = {"path": file.relative_to(root).as_posix(), "account_index": index}
        if ledger["quality"]:
            quality.append({**location, "quality": ledger["quality"]})
            counts["quality_occurrences"] += len(ledger["quality"])
        counts["summary_unresolved_occurrences"] += account["summary"]["unresolved_events"]
        for event in ledger["events"]:
            if event["type"] != "CORPORATE_ACTION":
                continue
            flags = [
                key
                for key in (
                    "unknown_cash_pay_date",
                    "unknown_cash_available_date",
                    "unknown_share_list_date",
                    "bonus_tax_not_modelled",
                )
                if event.get(key)
            ]
            if event.get("fractional_shares_discarded", 0) > 0:
                flags.append("fractional_shares_discarded")
            if flags:
                unresolved.append({**location, "event": event, "flags": flags})
        for trade in ledger["trades"]:
            if trade["side"] == "BUY" and any(
                trade["symbol"] == fact["symbol"] and fact["start"] <= trade["date"] < fact["end"]
                for fact in facts
            ):
                warning_buys.append({**location, "trade": trade})
assert not warning_buys
assert len(unresolved) == counts["summary_unresolved_occurrences"]
save(
    checks / "training_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "operation": p["operation"],
        "identity": status["identity"],
        "source_sha256": sources,
        "bundle_sha256": sha(paths.bundle),
        "historical_status_source_sha256": status_protocol["review_sha256"],
        "models_checked": 48,
        "target_expectations_sha256": sha(expectations_file),
        "expected_training_stages_matched": 24,
        "warning_reference_occurrences": 0,
        "warning_fit_occurrences": 0,
        "unknown_industry_training_rows": 0,
        "warning_inner_buys": 0,
        "fit_records": records,
        "selections": decisions,
        "model_gates": gates,
        "qualified_candidate_windows": sum(row["qualifies"] for row in gates),
        "inner_accounts": counts["accounts"],
        "quality_occurrences": counts["quality_occurrences"],
        "quality_accounts": quality,
        "unresolved_occurrences": len(unresolved),
        "unresolved_events": unresolved,
        "scope": "Checks every fitted cohort identity and preserved adoption gate. Inner quality and unresolved events remain reported even for unused rules and models; repeated account exposures are not independent source events.",
    },
    immutable=True,
)
assert sources == {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
print(
    "TRAINING REVIEW PASSED",
    dict(counts),
    "qualified",
    sum(row["qualifies"] for row in gates),
    flush=True,
)
