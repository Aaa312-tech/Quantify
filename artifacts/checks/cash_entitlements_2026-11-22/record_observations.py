"""Bind manually entered page observations to the registered source inventory."""

import json
import sys
from pathlib import Path

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
protocol_file = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_file)
checks = root / protocol["checks_output"]
scope_file = root / protocol["scope"]["path"]
assert sha(scope_file) == protocol["scope"]["sha256"]
scope = {(r["symbol"], r["ex_date"]): r for r in read(scope_file)["records"]}
texts_file = checks / "source_text.json"
texts = {(r["symbol"], r["ex_date"]): r for r in read(texts_file)["records"]}
spec_file = Path(sys.argv[1]).resolve()
observations = json.loads(spec_file.read_text(encoding="utf-8"))["observations"]
output = checks / "observations"
output.mkdir(exist_ok=True)
for observation in observations:
    key = observation["symbol"], observation["ex_date"]
    registered, text = scope[key], texts[key]
    assert observation["source_id"] == registered["candidate"]["id"] == text["id"]
    assert observation["full_text_pages_read"] == text["pages"]
    assert sha(root / text["source"]["path"]) == text["source"]["sha256"]
    assert sha(root / text["receipt"]["path"]) == text["receipt"]["sha256"]
    pages = observation.pop("page_observations")
    assert pages and len({p["page"] for p in pages}) == len(pages)
    images = []
    for page in pages:
        assert 1 <= page["page"] <= text["pages"]
        original = root / page.pop("rendered_image")
        image = checks / "pages" / text["id"] / f"page_{page['page']:02d}.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        value = sha(original)
        if image.exists():
            assert sha(image) == value
        else:
            with image.open("xb") as stream:
                stream.write(original.read_bytes())
        images.append({**page, "image": image.relative_to(root).as_posix(),
                       "image_sha256": value, "whole_page_visually_inspected": True})
    save(output / f"{key[0]}_{key[1]}.json", {
        "status": "PRIMARY_REVIEW_PENDING_INDEPENDENT_CLOSURE", "reviewed_at": now(),
        "protocol_sha256": sha(protocol_file), "scope_sha256": sha(scope_file),
        "source_text_sha256": sha(texts_file), "script_sha256": sha(Path(__file__)),
        "entered_observations_sha256": sha(spec_file), "source": text["source"],
        "receipt": text["receipt"], **observation, "page_observations": images,
        "source_basis_action_sha256": registered["source_basis_action_sha256"],
        "current_action_sha256": registered["current_action_sha256"],
        "facts_accepted": 0, "runtime_inputs_changed": False,
        "boundary": "Complete primary text and the listed whole-page images were actually inspected. Independent text/arithmetic and related-notice closure remain required before source acceptance. Observed fields are not an instruction to modify active data.",
    }, immutable=True)
    print("RECORDED PRIMARY OBSERVATION", *key, "images", len(images), flush=True)
