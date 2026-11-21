"""Render the six primary tail pages omitted from the earlier diagnostic images."""

import shutil
import subprocess
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
checks = root / "artifacts/checks/cash_entitlements_2026-11-22"
text_file = checks / "source_text.json"
sources = {item["id"]: item for item in read(text_file)["records"]}
renderer = Path(shutil.which("pdftoppm"))
output = root / ".git/distribution_frontier_2026_11_22/tail_pages"
output.mkdir()
records = []
for source_id, pages in {
    "1219767154": [5],
    "1220273317": [5],
    "1220443864": [4, 5],
    "1224071707": [4, 5],
}.items():
    source = sources[source_id]["source"]
    assert sha(root / source["path"]) == source["sha256"]
    for page in pages:
        prefix = output / f"{source_id}_page_{page:02d}"
        result = subprocess.run(
            [str(renderer), "-f", str(page), "-l", str(page), "-singlefile",
             "-scale-to", "1800", "-png", str(root / source["path"]), str(prefix)],
            capture_output=True, check=True,
        )
        image = prefix.with_suffix(".png")
        records.append({
            "source_id": source_id, "source": source, "page": page,
            "image": image.relative_to(root).as_posix(), "image_sha256": sha(image),
            "renderer_stderr": result.stderr.decode("utf-8", errors="replace"),
            "whole_page_visually_inspected": False,
        })
save(checks / "primary_tail_rendering.json", {
    "status": "RENDERED_NOT_REVIEWED", "created_at": now(),
    "source_text_sha256": sha(text_file), "script_sha256": sha(Path(__file__)),
    "renderer_sha256": sha(renderer), "records": records, "facts_accepted": 0,
}, immutable=True)
print("Rendered", len(records), "primary tail pages; visual review still required")
