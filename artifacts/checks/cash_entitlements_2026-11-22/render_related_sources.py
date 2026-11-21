"""Render selected inventoried notices, leaving visual review unclaimed."""

import shutil
import subprocess
import sys
from pathlib import Path

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
checks = Path(__file__).resolve().parent
inventory_path = checks / "related_source_inventory.json"
inventory = read(inventory_path)
requested = set(sys.argv[1:])
assert requested
records = {row["id"]: row for row in inventory["records"] if row["id"] in requested}
assert set(records) == requested
program = Path(shutil.which("pdftoppm"))
for source_id, row in records.items():
    pdf = root / row["source"]["path"]
    assert sha(pdf) == row["source"]["sha256"]
    output = checks / "related_pages" / source_id
    output.mkdir(parents=True)
    pages = []
    for page in range(1, len(row["pages"]) + 1):
        prefix = output / f"page_{page:02d}"
        result = subprocess.run([
            str(program), "-f", str(page), "-l", str(page), "-singlefile",
            "-scale-to", "1800", "-png", str(pdf), str(prefix),
        ], capture_output=True, check=True)
        image = prefix.with_suffix(".png")
        pages.append({
            "page": page, "image": image.relative_to(root).as_posix(),
            "sha256": sha(image), "stderr": result.stderr.decode("utf-8", errors="replace"),
        })
    save(checks / "renderings" / f"{source_id}.json", {
        "status": "RENDERED_NOT_REVIEWED", "rendered_at": now(),
        "source": row["source"], "source_id": source_id,
        "inventory_sha256": sha(inventory_path), "script_sha256": sha(Path(__file__)),
        "renderer": str(program), "renderer_sha256": sha(program), "pages": pages,
    }, immutable=True)
    print("RENDERED", source_id, len(pages), "whole pages")
