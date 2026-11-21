"""Bind whole-page observations and scoped amendment decisions to primary bytes."""

import re
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = Path(__file__).parent
fields = read(base / "independent_closure.json")
initial = read(base / "independent_fields.json")
production = {
    (row["symbol"], row["ex_date"]): row for row in read(base / "source_review.json")["records"]
}
render = read(base / "rendered_pages.json")
related_render = read(base / "related_pages.json")
inventory = read(base / "related_notices.json")
observations = read(base / "visual_observations.json")
assert fields["status"] == "PASS" and initial["status"] == "FAIL"
assert observations["status"] == "ALL_SELECTED_PAGES_VISUALLY_REVIEWED"
assert render["script_sha256"] == sha(base / "render_review.py")
assert related_render["script_sha256"] == sha(base / "render_related_notices.py")
assert render["source_review_sha256"] == sha(base / "source_review.json")
assert render["initial_independent_review_sha256"] == sha(base / "independent_fields.json")
assert related_render["related_inventory_sha256"] == sha(base / "related_notices.json")
selected = {row["image"]: row for row in render["records"] + related_render["records"]}
seen = {row["image"]: row for row in observations["records"]}
assert len(selected) == len(seen) == len(observations["records"]) == 38
assert selected.keys() == seen.keys()
pages = {}
for image, expected in selected.items():
    observed = seen[image]
    assert all(observed[key] == value for key, value in expected.items()), image
    assert observed["observation"] and observed["reviewed_at"]
    assert sha(root / image) == expected["image_sha256"]
    assert sha(root / expected["source"]) == expected["source_sha256"]
    pages[expected["source"], expected["page"]] = observed

families = {}
cross_page = []
for row in fields["records"]:
    for field, values in row["evidence"].items():
        for item in values:
            title = re.sub(
                r"20\d{2}(?:年)?(?:年度(?:中期|末期)?|半年度|中期)?",
                "PERIOD",
                item.get("title", ""),
            )
            key = field, item.get("method", ""), title
            item_pages = item.get("pages", [item["page"]])
            viewed = all((row["source"]["path"], page) in pages for page in item_pages)
            sample = {
                "symbol": row["symbol"],
                "ex_date": row["ex_date"],
                "source": row["source"]["path"],
                "pages": item_pages,
                "viewed": viewed,
            }
            families.setdefault(key, []).append(sample)
            if len(item_pages) > 1:
                assert viewed, sample
                cross_page.append(sample)
assert all(any(sample["viewed"] for sample in samples) for samples in families.values())
mismatches = []
for row in initial["records"]:
    if not row["passed"]:
        key = row["symbol"], row["ex_date"]
        needed = {1, *production[key]["evidence_pages"]}
        assert all((row["source"]["path"], page) in pages for page in needed)
        mismatches.append({"symbol": key[0], "ex_date": key[1], "pages": sorted(needed)})
assert len(mismatches) == 6 and len(families) == 21
assert {(row["symbol"], row["ex_date"]) for row in cross_page} == {("sh600031", "2024-06-21")}
visual = {
    "status": "PASS",
    "checked_at": now(),
    "checker_sha256": sha(Path(__file__)),
    "observations_sha256": sha(base / "visual_observations.json"),
    "renderer_manifest_sha256": sha(base / "rendered_pages.json"),
    "related_renderer_manifest_sha256": sha(base / "related_pages.json"),
    "independent_closure_sha256": sha(base / "independent_closure.json"),
    "whole_pages_reviewed": len(seen),
    "initial_render_layouts": len(render["families"]),
    "field_layouts": [
        {"field": key[0], "method": key[1], "title": key[2], "examples": samples}
        for key, samples in sorted(families.items())
    ],
    "initial_mismatches_reviewed": mismatches,
    "cross_page_financial_sentences": cross_page,
    "records": observations["records"],
    "scope": "Complete selected pages were visually inspected, including all six initial mismatches, the adjacent-page sentence, every final-reader layout and every archived revision-language hit. This does not claim visual inspection of every page in the archive.",
}
save(base / "visual_review.json", visual, immutable=True)

assert inventory["reader_sha256"] == sha(base / "inspect_related_notices.py")
assert inventory["independent_closure_sha256"] == sha(base / "independent_closure.json")
collection_file = root / "data/raw/disclosures_retry_2026-11-21/collection.json"
assert inventory["collection_sha256"] == sha(collection_file)
issuers = {row["symbol"] for row in fields["records"]}
expected_receipts = {
    item["receipt"]
    for item in read(collection_file)["documents"]
    if issuers.intersection(read(root / item["receipt"])["requested_symbols"])
}
assert set(inventory["issuers"]) == issuers and len(issuers) == 12
assert expected_receipts == {row["receipt"] for row in inventory["records"]}
assert len(expected_receipts) == inventory["documents"] == 55
assert inventory["title_revision_candidates"] == 0
classifications = {
    "1224719089": "CAPITAL_BASE_UPDATE_RATIO_UNCHANGED",
    "1220417229": "EXPLICIT_NO_CAPITAL_CHANGE",
    "1223934212": "EXPLICIT_NO_CAPITAL_CHANGE",
    "1224718262": "EXPLICIT_NO_CAPITAL_CHANGE",
    "1225328920": "EXPLICIT_NO_CAPITAL_CHANGE",
    "1220213405": "OPTION_EXERCISE_BASE_UPDATE_RATIO_UNCHANGED",
    "1223848087": "OPTION_EXERCISE_BASE_UPDATE_RATIO_UNCHANGED",
    "1225145429": "OPTION_EXERCISE_BASE_UPDATE_RATIO_UNCHANGED",
    "1225584867": "H_SHARE_GREENSHOE_CAPITAL_BASE_UPDATE",
}
decisions = []
for row in inventory["records"]:
    assert sha(root / row["receipt"]) == row["receipt_sha256"]
    assert sha(root / row["source"]) == row["source_sha256"]
    assert not row["title_revision_candidate"]
    for hit in row["text_hits"]:
        assert hit["word"] == "变更" and row["id"] in classifications
        observed = pages[row["source"], hit["page"]]
        assert hit in observed["hits"]
        decisions.append(
            {
                "document_id": row["id"],
                "source": row["source"],
                "source_sha256": row["source_sha256"],
                "hit": hit,
                "classification": classifications[row["id"]],
                "observation": observed["observation"],
                "image": observed["image"],
                "image_sha256": observed["image_sha256"],
                "payment_revision": False,
            }
        )
assert len(decisions) == inventory["text_hits"] == 10
assert {row["document_id"] for row in decisions} == classifications.keys()
assert {row["source"]["path"] for row in fields["records"]} <= {
    row["source"] for row in inventory["records"]
}
save(
    base / "amendment_review.json",
    {
        "status": "PASS",
        "checked_at": now(),
        "checker_sha256": sha(Path(__file__)),
        "inventory_sha256": sha(base / "related_notices.json"),
        "independent_closure_sha256": sha(base / "independent_closure.json"),
        "visual_review_sha256": sha(base / "visual_review.json"),
        "issuers": sorted(issuers),
        "issuer_documents": len(expected_receipts),
        "title_revision_candidates": 0,
        "body_hits_reviewed": len(decisions),
        "body_hit_documents": len(classifications),
        "unresolved_conflicts": 0,
        "decisions": decisions,
        "scope": "All received archived documents for these issuers are inventoried. The ten detected body hits concern capital-base disclosures or explicit absence of change, not amendments to accepted payment dates. This scoped review does not certify a complete historical filing/amendment archive, other financial terms or exact publication time.",
    },
    immutable=True,
)
print("SOURCE REVIEWS ACCEPTED", len(seen), "pages", len(families), "layouts", flush=True)
