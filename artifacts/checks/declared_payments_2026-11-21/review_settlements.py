"""Trace each supplemented event to account entitlements and actual cash release."""

from collections import Counter
from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import load_gzip, now, read, save, sha

root = Path.cwd()
p = read(root / "configs/protocols/declared_payments.json")
paths = Paths(root, p["configuration"])
checks = root / p["checks_output"]
bundle = load_bundle(paths)
changes = read(root / p["snapshot_output"] / "action_changes.json")
affected = {(row["symbol"], row["ex_date"]) for row in changes}
actions = {(row["symbol"], row["ex_date"]): row for row in bundle["actions"]}
assert len(affected) == p["expected_changes"]["events"] == 25
records, counters = [], Counter()


def check(account, location):
    counters[location["arm"] + "_accounts"] += 1
    ledger = account["ledger"]
    dates = [row["date"] for row in ledger["curve"]]
    for event in ledger["events"]:
        identity = event["symbol"], event["date"]
        if event["type"] != "CORPORATE_ACTION" or identity not in affected:
            continue
        action = actions[identity]
        assert event["pay_date"] == action["cash_pay_date"]
        assert event["cash_available_date"] == action["cash_available_date"]
        assert event["share_list_date"] == action.get("share_list_date")
        assert not event["unknown_cash_pay_date"]
        assert not event["unknown_cash_available_date"]
        paid = [
            row
            for row in ledger["events"]
            if row["type"] == "CASH_DIVIDEND_PAID" and row["event_id"] == action["id"]
        ]
        eligible_openings = [day for day in dates if day >= action["cash_available_date"]]
        if eligible_openings and event["net_receivable"] > 0:
            assert len(paid) == 1
            assert paid[0]["date"] == eligible_openings[0] > action["cash_pay_date"]
            assert paid[0]["amount"] == event["net_receivable"]
            assert paid[0]["pay_date"] == action["cash_pay_date"]
            assert paid[0]["cash_available_date"] == action["cash_available_date"]
        else:
            assert not paid
        counters[location["arm"] + "_affected_event_occurrences"] += 1
        counters[location["arm"] + "_paid_event_occurrences"] += len(paid)
        records.append({**location, "event": event, "payments": paid})


for arm, directory, summary_file in (
    ("retrained", root / p["evaluation_output"], "evaluation_summary.json"),
    ("fixed", root / p["fixed_forecast_output"], "summary.json"),
):
    summary = read(directory / summary_file)
    for relative, expected in summary["artifacts"].items():
        file = directory / relative
        assert sha(file) == expected
        value = load_gzip(file)
        for index, account in enumerate(value if isinstance(value, list) else [value]):
            check(account, {"arm": arm, "path": file.relative_to(root).as_posix(), "index": index})
    assert counters[arm + "_accounts"] == 1425
run = root / p["training_output"]
for file in sorted(run.rglob("*.ledgers.json.gz")):
    if "/inner_" not in file.relative_to(run).as_posix():
        continue
    metadata = read(file.with_name(file.name.replace(".ledgers.json.gz", ".json")))
    assert metadata["ledger_sha256"] == sha(file)
    value = load_gzip(file)
    for index, account in enumerate([*value["episodes"], value["continuous"]]):
        check(account, {"arm": "inner", "path": file.relative_to(root).as_posix(), "index": index})
assert counters["inner_accounts"] == read(checks / "training_review.json")["inner_accounts"]
save(
    checks / "settlement_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "bundle_sha256": sha(paths.bundle),
        "source_sha256": {f.name: sha(f) for f in (root / "src/quantfolio").glob("*.py")},
        "counts": dict(counters),
        "records": records,
        "scope": "Every affected inner, retrained and fixed-forecast entitlement is checked against sourced dates and its separately recorded cash payment. Receivable release is not itself profit; repeated account occurrences are not independent events.",
    },
    immutable=True,
)
print("SETTLEMENTS REVIEWED", dict(counters), flush=True)
