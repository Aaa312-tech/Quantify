"""Review the registered inventory and preserve all before/after source field evidence."""

import logging
from pathlib import Path

from quantfolio.distributions import review_distributions
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

logging.basicConfig(level=logging.INFO, format="%(message)s")
root = Path.cwd()
paths = Paths(root)
pfile = root / "configs/protocols/payout_scope.json"
p = read(pfile)
checks = root / p["checks_output"]
implementation = read(checks / "implementation.json")
source = {file.name: sha(file) for file in sorted((root / "src/quantfolio").glob("*.py"))}
assert implementation["status"] == "PASS" and implementation["source_sha256"] == source
assert read(checks / "local_validation.json")["status"] == "PASS"
for key in ("configuration", "bundle", "plan"):
    assert sha(root / p["active_state"][key]) == p["active_state"][key + "_sha256"]
save(
    checks / "execution.json",
    {
        "started_at": now(),
        "source_sha256": source,
        "script_sha256": sha(Path(__file__)),
        "protocol_sha256": sha(pfile),
        "page_order_protocol_sha256": sha(root / "configs/protocols/payout_page_order.json"),
        "implementation_sha256": sha(checks / "implementation.json"),
        "local_validation_sha256": sha(checks / "local_validation.json"),
    },
    immutable=True,
)
review_distributions(paths, pfile.relative_to(root).as_posix())
review = read(root / p["review"])
old = read(root / p["previous_review"]["path"])
previous = {(row["symbol"], row["ex_date"]): row for row in old["records"]}
current = {(row["symbol"], row["ex_date"]): row for row in review["records"]}
assert set(previous) == set(current) and len(current) == 1839
differences, preserved, additions = [], [], []
for key, prior in previous.items():
    row = current[key]
    assert row["original_action_sha256"] == prior["original_action_sha256"]
    for field, value in prior["verified_fields"].items():
        actual = row["verified_fields"].get(field)
        if actual != value:
            differences.append(
                {
                    "symbol": key[0],
                    "ex_date": key[1],
                    "field": field,
                    "before": value,
                    "after": actual,
                    "status": row["status"],
                }
            )
        else:
            preserved.append([*key, field, value])
    if prior["verified_fields"]:
        for field in ("source", "known_at", "method"):
            if row.get(field) != prior.get(field):
                differences.append(
                    {
                        "symbol": key[0],
                        "ex_date": key[1],
                        "field": field,
                        "before": prior.get(field),
                        "after": row.get(field),
                    }
                )
    fields = {
        field: value
        for field, value in row["verified_fields"].items()
        if field not in prior["verified_fields"]
    }
    if fields:
        additions.append(
            {"symbol": key[0], "ex_date": key[1], "fields": fields, "source": row["source"]}
        )
assert source == {file.name: sha(file) for file in (root / "src/quantfolio").glob("*.py")}
save(
    checks / "preservation.json",
    {
        "status": "FAIL" if differences else "PASS",
        "checked_at": now(),
        "reader_sha256": sha(Path(__file__)),
        "source_sha256": source,
        "source_review_sha256": sha(root / p["review"]),
        "previous_review_sha256": sha(root / p["previous_review"]["path"]),
        "events": len(current),
        "counts": review["counts"],
        "preserved_fields": preserved,
        "differences": differences,
        "additions": additions,
        "facts_applied": False,
        "scope": "Production extraction and prior-field preservation only. Added facts still require independent financial, amendment and whole-page visual acceptance.",
    },
    immutable=True,
)
print(
    "SOURCE REVIEW",
    review["counts"],
    "ADDED",
    len(additions),
    "DIFFERENCES",
    len(differences),
    flush=True,
)
assert not differences and len(preserved) == 1414
