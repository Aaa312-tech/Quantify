"""Explain the rejected all-label parity assumption before any new-data fitting."""

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.dataset import load_snapshot
from quantfolio.market import legal_label
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
p = read(root / "configs/protocols/distribution_dates.json")
checks = root / p["checks_output"]
bundle = load_snapshot(Paths(root), root / p["source_bundle"], p["source_data_cutoff"])
facts = read(root / p["snapshot_output"] / "fact_bindings.json")
by_key = {(row["symbol"], row["ex_date"]): row for row in facts}
old, new, cash_only = defaultdict(list), defaultdict(list), defaultdict(list)
for action in bundle["actions"]:
    old[action["symbol"]].append(action)
    corrected = deepcopy(action)
    fields = by_key.get((action["symbol"], action["ex_date"]), {}).get("verified_fields", {})
    corrected.update(fields)
    if "cash_pay_date" in fields:
        following = [
            str(d.date()) for d in bundle["calendar"] if str(d.date()) > fields["cash_pay_date"]
        ]
        corrected["cash_available_date"] = following[0] if following else None
    new[action["symbol"]].append(corrected)
    only_cash = deepcopy(corrected)
    only_cash["share_list_date"] = action.get("share_list_date")
    cash_only[action["symbol"]].append(only_cash)
symbols = {row["symbol"] for row in facts if "share_list_date" in row["verified_fields"]}
calendar, table = bundle["calendar"], bundle["table"]
records, tested = [], 0
for symbol in sorted(symbols):
    cohort = table[table.symbol.eq(symbol) & table.eligible]
    for row in cohort.itertuples():
        for horizon in (1, 3, 5):
            columns = [f"{field}{horizon}" for field in ("y", "end", "status", "delay")]
            expected = [getattr(row, column) for column in columns]
            outputs = []
            for actions in (old[symbol], cash_only[symbol], new[symbol]):
                value = legal_label(
                    bundle["meta"][symbol],
                    bundle["raws"][symbol],
                    calendar,
                    int(row.date_index),
                    horizon,
                    actions,
                    opening_status=bundle.get("opening_status"),
                )
                outputs.append(
                    [np.asarray(v, dtype=table[c].dtype).item() for v, c in zip(value, columns)]
                )
            for observed in outputs[:2]:
                assert all(
                    (pd.isna(a) and pd.isna(b)) or a == b for a, b in zip(expected, observed)
                )
            tested += 1
            if not all((pd.isna(a) and pd.isna(b)) or a == b for a, b in zip(expected, outputs[2])):
                records.append(
                    {
                        "row": int(row.Index),
                        "symbol": symbol,
                        "date_index": int(row.date_index),
                        "signal_date": str(calendar[row.date_index].date()),
                        "horizon": horizon,
                        "before": [None if pd.isna(v) else v for v in expected],
                        "after": [None if pd.isna(v) else v for v in outputs[2]],
                        "listing_facts": [
                            f["verified_fields"] | {"ex_date": f["ex_date"]}
                            for f in facts
                            if f["symbol"] == symbol and "share_list_date" in f["verified_fields"]
                        ],
                    }
                )
    print("LABEL DIAGNOSTIC", symbol, flush=True)
counts = Counter(str(row["horizon"]) for row in records)
assert records and all(row["before"][2] == 4 and row["after"][2] == 0 for row in records)
save(
    checks / "label_diagnostic.json",
    {
        "status": "DIAGNOSED_NOT_ACTIVATED",
        "checked_at": now(),
        "snapshot_code_sha256": sha(root / "src/quantfolio/snapshots.py"),
        "market_code_sha256": sha(root / "src/quantfolio/market.py"),
        "diagnostic_sha256": sha(Path(__file__)),
        "data_sha256": sha(root / p["source_bundle"]),
        "failed_log_sha256": sha(checks / "snapshot.log"),
        "listing_symbols": len(symbols),
        "eligible_labels_recomputed": tested,
        "changed_labels": len(records),
        "changed_by_horizon": dict(counts),
        "cash_only_labels_exact_in_reviewed_cohorts": True,
        "records": records,
        "scope": "The unchanged legal-label implementation censors unknown listing dates. Verified listing dates restore complete legal exits; cash receivables already retain gross wealth regardless of payment. This diagnoses a registration assumption, not a changed return formula. Full independent label reconstruction is still required before acceptance.",
        "training_started": False,
        "active_inputs_unchanged": True,
    },
    immutable=True,
)
print("LABEL FAILURE DIAGNOSED", tested, len(records), dict(counts), flush=True)
