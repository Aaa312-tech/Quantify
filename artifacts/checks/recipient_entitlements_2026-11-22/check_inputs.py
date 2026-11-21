"""Build expected actions independently of the production entitlement adapter."""

from collections import Counter
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

from quantfolio.dataset import load_bundle
from quantfolio.settings import Paths
from quantfolio.storage import digest, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/recipient_entitlements.json"
p = read(pfile)
base = root / p["checks_output"]
bundle = load_bundle(paths)
require(sha(paths.bundle) == p["source_bundle_sha256"], "Parent bundle changed")
review = read(root / p["review"])
accepted = read(root / p["independent_review"]["path"])
decisions = read(root / p["field_decisions"]["path"])
scope = read(root / decisions["scope"]["path"])
require(
    sha(root / decisions["scope"]["path"]) == decisions["scope"]["sha256"], "Source scope changed"
)
expected = deepcopy(bundle["actions"])
index = {(row["symbol"], row["ex_date"]): row for row in expected}
facts = {(row["symbol"], row["ex_date"]): row for row in review["facts"]}
calendar = [str(date.date()) for date in bundle["calendar"]]
counts, changed, preserved_cash = Counter(), [], 0
scoped = set()
for record in accepted["records"]:
    key = record["symbol"], record["ex_date"]
    require(key not in scoped, "Duplicate accepted action")
    scoped.add(key)
    action, fact = index[key], facts[key]
    require(digest(action) == record["original_action_sha256"], "Target action changed")
    require(record["verified_fields"] == fact["verified_fields"], "Source fields differ")
    require(
        not any(name in action for name in ("distribution_review", "distribution_date_reviews")),
        "Existing target review",
    )
    fields = {}
    for name, value in record["verified_fields"].items():
        old = action.get(name)
        if name in {"cash_per_share", "ex_reference_cash_per_share"}:
            amount = Fraction(value)
            require(amount >= 0, "Negative source cash")
            if old is not None and abs(amount - Fraction(str(old))) <= Fraction(1, 10**12):
                preserved_cash += int(name == "cash_per_share")
                continue
            value = float(amount)
        else:
            require(
                name in {"cash_pay_date", "share_list_date"} and value >= action["ex_date"],
                "Unexpected date field",
            )
        if old != value:
            fields[name] = {"before": old, "after": value}
            action[name] = value
            counts[name] += 1
    action["cash_available_date"] = next(
        (day for day in calendar if day > action["cash_pay_date"]), None
    )
    action["distribution_review"] = {
        "method": fact["method"],
        "fields": sorted(record["verified_fields"]),
        "source": deepcopy(fact["source"]),
        "pages": fact["evidence_pages"],
        "known_at": fact["known_at"],
    }
    if fields:
        changed.append({"symbol": key[0], "ex_date": key[1], "fields": fields})
require(
    {**counts, "events": len(changed)} == p["expected_changes"],
    "Unexpected independent field changes",
)
require(preserved_cash == 28, "Original binary64 preservation differs")
require(len(expected) == len(index) == 1839 and len(scoped) == 31, "Action inventory differs")
unchanged = sum(old == new for old, new in zip(bundle["actions"], expected))
require(unchanged == 1808, "Unscoped action or review changed")
save(base / "expected_actions.json", expected, immutable=True)
save(base / "expected_action_changes.json", changed, immutable=True)
save(
    base / "action_scope.json",
    {
        "status": "PASS_ACTION_SCOPE_ONLY",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "source_bundle_sha256": sha(paths.bundle),
        "source_review_sha256": sha(root / p["review"]),
        "actions_sha256": digest(expected),
        "action_inventory": len(expected),
        "changed_events": len(changed),
        "changed_fields": dict(counts),
        "unscoped_actions_and_reviews_exact": unchanged,
        "original_cash_binary64_preserved": preserved_cash,
        "independent_method": "Fraction-based monetary comparison and conversion; ISO calendar search; direct source evidence assembly. No production snapshot binding/application helper is called.",
        "numeric_features_and_labels_accepted": False,
        "activated": False,
    },
    immutable=True,
)
print("INDEPENDENT_ACTION_SCOPE_PASSED", dict(counts), digest(expected), flush=True)
