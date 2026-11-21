"""Compare the complete reader replay with prior accepted facts and residual scope."""

from collections import Counter
from pathlib import Path

from quantfolio.storage import digest, now, read, save, sha


def require(condition, message):
    if not condition:
        raise ValueError(message)


base = Path("artifacts/checks/distribution_residuals_2026-11-22")
protocol = read("configs/protocols/distribution_residuals.json")
scope = read(protocol["scope"]["path"])
old = read(protocol["previous_review"]["path"])
new = read(base / "proposed_source_review.json")
probe = read(base / "candidate_probe.json")
extractor = sha("src/quantfolio/distributions.py")
require(
    new["extractor_sha256"] == probe["extractor_sha256"] == extractor, "Reader identities differ"
)
require(
    old["original_actions_sha256"] == new["original_actions_sha256"], "Original actions changed"
)
require(old["source_inputs"] == new["source_inputs"], "Source inputs changed")
require(digest(old["facts"]) == scope["prior_supported_facts_sha256"], "Prior facts changed")
original = {(r["symbol"], r["ex_date"]): r for r in old["records"]}
replayed = {(r["symbol"], r["ex_date"]): r for r in new["records"]}
residual = {(r["symbol"], r["ex_date"]): r for r in scope["records"]}
require(
    len(original) == len(replayed) == 1839 and set(original) == set(replayed), "Event scope differs"
)
prior_keys = {(r["symbol"], r["ex_date"]) for r in old["facts"]}
require(set(original) == prior_keys | set(residual), "Scope does not partition all events")
require(not prior_keys.intersection(residual), "Supported and residual records overlap")
retained_fields, exact_records, prior_changes, outside_scope = 0, 0, [], []
for row in old["facts"]:
    key = row["symbol"], row["ex_date"]
    current = replayed[key]
    require(current["status"] == "VERIFIED_FIELDS", (key, "Prior event became unsupported"))
    for field, expected in row["verified_fields"].items():
        require(
            current["verified_fields"].get(field) == expected, (key, field, "Prior value changed")
        )
        retained_fields += 1
    for field in ("source", "structured_source", "known_at", "method", "original_action_sha256"):
        require(row.get(field) == current.get(field), (key, field, "Prior identity changed"))
    if row == current:
        exact_records += 1
    else:
        prior_changes.append(
            {
                "symbol": key[0],
                "ex_date": key[1],
                "changed_keys": sorted(
                    k for k in row.keys() | current.keys() if row.get(k) != current.get(k)
                ),
                "original_record_sha256": digest(row),
                "replayed_record_sha256": digest(current),
                "disposition": "RETAIN_ORIGINAL_ACCEPTED_RECORD",
            }
        )
    extra = {k: v for k, v in current["verified_fields"].items() if k not in row["verified_fields"]}
    if extra:
        outside_scope.append(
            {
                "symbol": key[0],
                "ex_date": key[1],
                "proposed_fields": extra,
                "disposition": "OUTSIDE_REGISTERED_RESIDUAL_SCOPE_NOT_ACCEPTED",
            }
        )
require(retained_fields == 1530 and len(prior_keys) == 1452, "Prior field coverage differs")
probe_records = {(r["symbol"], r["ex_date"]): r for r in probe["records"]}
require(set(probe_records) == set(residual), "Dual-reader probe scope differs")
dispositions, proposed_fields = [], Counter()
for key, prior in residual.items():
    current = replayed[key]
    primary = probe_records[key]["readers"]["pypdf"]
    require(current["status"] == primary["status"], (key, "Production/probe status differs"))
    require(
        current["verified_fields"] == primary["candidate_fields_not_accepted"],
        (key, "Production/probe fields differ"),
    )
    require(
        current["original_action_sha256"] == prior["original_action_sha256"],
        (key, "Original action differs"),
    )
    proposed_fields.update(current["verified_fields"].keys())
    dispositions.append(
        {
            "symbol": key[0],
            "ex_date": key[1],
            "prior_status": prior["status"],
            "reader_status": current["status"],
            "proposed_fields_not_accepted": current["verified_fields"],
            "disposition": "INDEPENDENT_FIELD_REVIEW_PENDING"
            if current["verified_fields"]
            else "UNRESOLVED",
        }
    )
active = protocol["active_state"]
state = {
    "configuration": sha(active["configuration"]) == active["configuration_sha256"],
    "bundle": sha(active["bundle"]["path"]) == active["bundle"]["sha256"],
    "paper_plan": sha(active["plan"]["path"]) == active["plan"]["sha256"],
}
require(all(state.values()), "Active research state changed")
save(
    base / "reader_scope.json",
    {
        "status": "READER_SCOPE_CHECKED_FINANCIAL_ACCEPTANCE_PENDING",
        "completed_at": now(),
        "checker_sha256": sha(__file__),
        "extractor_sha256": extractor,
        "proposed_review_sha256": sha(base / "proposed_source_review.json"),
        "candidate_probe_sha256": sha(base / "candidate_probe.json"),
        "events_replayed": len(replayed),
        "prior_supported_events": len(prior_keys),
        "prior_fields_preserved": retained_fields,
        "prior_source_and_time_identities_preserved": True,
        "prior_records_exact_in_raw_replay": exact_records,
        "raw_prior_record_differences": prior_changes,
        "out_of_scope_additional_fields": outside_scope,
        "residual_dispositions": dispositions,
        "proposed_residual_fields": dict(proposed_fields),
        "active_state_unchanged": state,
        "facts_accepted": 0,
        "facts_applied": False,
    },
    immutable=True,
)
print("READER_SCOPE_CHECKED", len(replayed), "events; prior fields preserved", retained_fields)
print("PROPOSED_NOT_ACCEPTED", dict(proposed_fields), "outside-scope events", len(outside_scope))
