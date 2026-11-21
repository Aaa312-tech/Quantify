"""Explain recorded account differences without changing prior acceptance receipts."""

from collections import Counter
from pathlib import Path

from quantfolio.feasibility import first_difference
from quantfolio.storage import load_gzip, now, read, save, sha

root = Path.cwd()
p = read(root / "configs/protocols/companion_payments.json")
checks = root / p["checks_output"]
attribution = read(checks / "attribution.json")
evaluation = read(root / p["evaluation_output"] / "evaluation_summary.json")
results = {}
for name, before, after in (
    ("data_execution", p["baseline_evaluation"], p["fixed_forecast_output"]),
    ("retraining_selection", p["fixed_forecast_output"], p["evaluation_output"]),
):
    components, examples, accounts, changed = Counter(), {}, 0, 0
    for relative in evaluation["artifacts"]:
        left, right = [load_gzip(root / directory / relative) for directory in (before, after)]
        left = left if isinstance(left, list) else [left]
        right = right if isinstance(right, list) else [right]
        assert len(left) == len(right)
        for index, (old, new) in enumerate(zip(left, right)):
            accounts += 1
            changed += old != new
            assert old.keys() == new.keys() and old["ledger"].keys() == new["ledger"].keys()
            parts = [(key, old[key], new[key]) for key in old if key != "ledger"]
            parts += [
                ("ledger/" + key, old["ledger"][key], new["ledger"][key]) for key in old["ledger"]
            ]
            for key, old_value, new_value in parts:
                if old_value != new_value:
                    components[key] += 1
                    examples.setdefault(
                        key,
                        {
                            "path": relative,
                            "account_index": index,
                            "first_difference": first_difference(old_value, new_value),
                        },
                    )
    registered = attribution["account_comparisons"][name]
    assert accounts == registered["accounts"] == 1425
    assert changed == accounts - registered["full_accounts_exact"]
    results[name] = {
        "accounts": accounts,
        "changed_accounts": changed,
        "changed_components": dict(components),
        "examples": examples,
    }
save(
    checks / "forecast_effects_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "attribution_sha256": sha(checks / "attribution.json"),
        "evaluation_sha256": sha(root / p["evaluation_output"] / "evaluation_summary.json"),
        "results": results,
        "scope": "Explanatory comparison of every stored account component. Prior numerical acceptance, returns and complete difference records are unchanged.",
    },
    immutable=True,
)
print({name: row["changed_components"] for name, row in results.items()}, flush=True)
