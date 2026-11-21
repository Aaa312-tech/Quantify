"""Render full representative pages and every independent-reader mismatch."""

import shutil
import subprocess
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = root / "artifacts/checks/distribution_wording_2026-11-21"
review = read(base / "field_acceptance.json")
failed = read(base / "independent_closure.json")
selected = {}
families = {}
for row in review["records"]:
    for layout in row["layouts"]:
        family = layout["family"]
        if family.startswith("amount:") or family in families:
            continue
        families[family] = {"symbol": row["symbol"], "ex_date": row["ex_date"]}
        pages = {item["page"] for values in row["evidence"].values() for item in values}
        for page in pages:
            selected.setdefault((row["source"]["path"], page), []).append(family)
for row in failed["records"]:
    if not row["passed"]:
        selected.setdefault((row["source"]["path"], 1), []).append(
            "independent_title_or_summary_failure"
        )
for row in review["records"]:
    if (row["symbol"], row["ex_date"]) == ("sz000951", "2025-11-14"):
        for page in range(1, 5):
            selected.setdefault((row["source"]["path"], page), []).append("remaining_inner_event")
    if row["symbol"] == "sh603687":
        for page in {e["page"] for values in row["evidence"].values() for e in values}:
            selected.setdefault((row["source"]["path"], page), []).append("cash_paisong_verb")
renderer = shutil.which("pdftoppm")
assert renderer
output = base / "pages"
output.mkdir()
rows = []
for (relative, page), reasons in sorted(selected.items()):
    pdf = root / relative
    prefix = output / (pdf.stem + f"_page_{page}")
    subprocess.run(
        [
            renderer,
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-scale-to",
            "1500",
            "-png",
            str(pdf),
            str(prefix),
        ],
        check=True,
    )
    image = prefix.with_suffix(".png")
    rows.append(
        {
            "source": relative,
            "source_sha256": sha(pdf),
            "page": page,
            "image": image.relative_to(root).as_posix(),
            "image_sha256": sha(image),
            "reasons": reasons,
        }
    )
save(
    base / "rendered_pages.json",
    {
        "status": "RENDERED_NOT_YET_VISUALLY_ACCEPTED",
        "rendered_at": now(),
        "renderer": renderer,
        "checker_sha256": sha(Path(__file__)),
        "source_review_sha256": sha(base / "source_review.json"),
        "field_acceptance_sha256": sha(base / "field_acceptance.json"),
        "field_layouts": families,
        "records": rows,
    },
    immutable=True,
)
print("Rendered", len(rows), "whole pages", len(families), "layouts")
