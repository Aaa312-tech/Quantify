"""Render complete financial/date layout examples and every independent mismatch."""

import re
import shutil
import subprocess
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
base = Path(__file__).parent
independent = read(base / "independent_fields.json")
production = {(r["symbol"], r["ex_date"]): r for r in read(base / "source_review.json")["records"]}
selected, families = {}, {}


def add(source, page, reason):
    selected.setdefault((source, page), []).append(reason)


for row in independent["records"]:
    source = row["source"]["path"]
    if not row["passed"]:
        for page in {1, *production[(row["symbol"], row["ex_date"])]["evidence_pages"]}:
            add(source, page, "independent_title_or_currency_mismatch")
    for field, values in row["evidence"].items():
        for item in values:
            title = re.sub(
                r"20\d{2}(?:年)?(?:年度(?:中期|末期)?|半年度|中期)?",
                "PERIOD",
                item.get("title", ""),
            )
            family = (field, item.get("method", ""), title)
            if family not in families:
                families[family] = {"symbol": row["symbol"], "ex_date": row["ex_date"]}
                for page in item.get("pages", [item["page"]]):
                    add(source, page, "layout:" + "/".join(family))
            if len(item.get("pages", [])) > 1:
                for page in item["pages"]:
                    add(source, page, "every_cross_page_financial_sentence")
renderer = shutil.which("pdftoppm")
assert renderer
output = base / "pages"
output.mkdir()
records = []
for (relative, page), reasons in sorted(selected.items()):
    pdf = root / relative
    prefix = output / f"{pdf.stem}_page_{page}"
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
    image = prefix.with_suffix(".png")
    records.append(
        {
            "source": relative,
            "source_sha256": sha(pdf),
            "page": page,
            "image": image.relative_to(root).as_posix(),
            "image_sha256": sha(image),
            "reasons": sorted(set(reasons)),
        }
    )
save(
    base / "rendered_pages.json",
    {
        "status": "RENDERED_NOT_YET_VISUALLY_ACCEPTED",
        "rendered_at": now(),
        "renderer": renderer,
        "script_sha256": sha(Path(__file__)),
        "source_review_sha256": sha(base / "source_review.json"),
        "initial_independent_review_sha256": sha(base / "independent_fields.json"),
        "families": [
            {"field": key[0], "method": key[1], "title": key[2], **value}
            for key, value in families.items()
        ],
        "records": records,
    },
    immutable=True,
)
print("RENDERED WHOLE PAGES", len(records), "FIELD LAYOUTS", len(families), flush=True)
