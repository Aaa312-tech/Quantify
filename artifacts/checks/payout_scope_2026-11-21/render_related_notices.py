"""Render whole pages containing every archived revision-language occurrence."""

import shutil
import subprocess
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = Path(__file__).parent
inventory = read(base / "related_notices.json")
renderer = shutil.which("pdftoppm")
assert renderer
records = []
for row in inventory["records"]:
    for page in sorted({hit["page"] for hit in row["text_hits"]}):
        pdf = root / row["source"]
        assert sha(pdf) == row["source_sha256"]
        prefix = base / "pages" / f"{pdf.stem}_page_{page}"
        image = prefix.with_suffix(".png")
        if not image.exists():
            subprocess.run(
                [
                    renderer,
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-scale-to",
                    "1600",
                    "-png",
                    str(pdf),
                    str(prefix),
                ],
                check=True,
            )
        records.append(
            {
                "source": row["source"],
                "source_sha256": row["source_sha256"],
                "page": page,
                "image": image.relative_to(root).as_posix(),
                "image_sha256": sha(image),
                "reasons": ["archived_revision_language"],
                "hits": [hit for hit in row["text_hits"] if hit["page"] == page],
            }
        )
save(
    base / "related_pages.json",
    {
        "status": "RENDERED_NOT_YET_VISUALLY_ACCEPTED",
        "rendered_at": now(),
        "script_sha256": sha(Path(__file__)),
        "related_inventory_sha256": sha(base / "related_notices.json"),
        "records": records,
    },
    immutable=True,
)
print("RENDERED RELATED PAGES", len(records))
