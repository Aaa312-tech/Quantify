"""Render the archived adjustments explicitly linked to the diagnostic schemes."""

from pathlib import Path
import shutil
import subprocess

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
inventory_file = base / "shareholder_related_notice_inventory.json"
inventory = read(inventory_file)
ids = {"1220519887"}
output = base / "shareholder_related_pages"
output.mkdir()
program = shutil.which("pdftoppm")
records = []
for row in inventory["records"]:
    if row["id"] not in ids:
        continue
    pdf = root / row["source"]["path"]
    assert sha(pdf) == row["source"]["sha256"]
    for page in range(1, len(row["pages"]) + 1):
        prefix = output / f"{row['id']}_page_{page:02d}"
        result = subprocess.run([program, "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "1800", "-png", str(pdf), str(prefix)], capture_output=True, check=True)
        png = prefix.with_suffix(".png")
        records.append({"id": row["id"], "source": row["source"], "page": page,
                        "image": png.relative_to(root).as_posix(), "image_sha256": sha(png),
                        "stderr": result.stderr.decode("utf-8", errors="replace")})
assert {row["id"] for row in records} == ids
save(base / "shareholder_related_rendering.json", {
    "status": "RENDERED_NOT_REVIEWED", "created_at": now(), "script_sha256": sha(Path(__file__)),
    "inventory_sha256": sha(inventory_file), "records": records, "facts_accepted": 0,
}, immutable=True)
print("RENDERED", len(records), "related full pages", [(item, sum(row['id'] == item for row in records)) for item in sorted(ids)], flush=True)
