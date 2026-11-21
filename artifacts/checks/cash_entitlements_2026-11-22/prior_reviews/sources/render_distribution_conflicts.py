"""Render relevant full pages for the unresolved amount diagnostic."""

import shutil
import subprocess
from pathlib import Path

from PIL import Image

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
initial_file = base / "initial_inventory.json"
initial = read(initial_file)
output = base / "preliminary_pages"
output.mkdir()
program = Path(shutil.which("pdftoppm"))
version = subprocess.run([str(program), "-v"], capture_output=True, text=True, check=True)
records = []
for row in initial["records"]:
    if row["status"] != "PRIMARY_GROSS_AMOUNT_MISMATCH":
        continue
    key = row["symbol"] + "/" + row["ex_date"]
    source = row["source_review_record"]["source"]
    pages = initial["text_observations"][key]["pages_to_review"]
    pdf = root / source["path"]
    assert sha(pdf) == source["sha256"]
    for page in pages:
        stem = f"{pdf.stem}_page_{page:02d}"
        prefix = output / stem
        command = [str(program), "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "1800", "-png", str(pdf), str(prefix)]
        result = subprocess.run(command, capture_output=True, check=True)
        png = prefix.with_suffix(".png")
        with Image.open(png) as image:
            size = list(image.size)
        records.append({
            "event": key, "source": source, "page": page,
            "image": png.relative_to(root).as_posix(), "image_sha256": sha(png),
            "image_size": size, "stderr": result.stderr.decode("utf-8", errors="replace"),
        })
save(base / "preliminary_rendering.json", {
    "status": "RENDERED_NOT_REVIEWED", "created_at": now(),
    "initial_inventory_sha256": sha(initial_file), "script_sha256": sha(Path(__file__)),
    "renderer": str(program), "renderer_sha256": sha(program),
    "renderer_version": (version.stdout + version.stderr).strip(),
    "records": records, "facts_accepted": 0,
}, immutable=True)
print("RENDERED", len(records), "full pages; visual inspection still required", flush=True)
