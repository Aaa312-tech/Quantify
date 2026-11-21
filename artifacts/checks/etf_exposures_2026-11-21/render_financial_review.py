"""Render original financial-note pages for the registered layout review."""

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from quantfolio.storage import now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
VISUALS = CHECKS / "visuals/financial"


def main():
    protocol = read(CHECKS / "financial_followup_protocol.json")
    save(
        CHECKS / "financial_render_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(CHECKS / "financial_followup_protocol.json"),
        },
        immutable=True,
    )
    records = []
    VISUALS.mkdir(parents=True, exist_ok=True)
    for case in protocol["scope"]:
        source = ROOT / case["source"]
        if sha(source) != case["source_sha256"]:
            raise ValueError("Original PDF changed")
        images = []
        pages = []
        for page in case["pages"]:
            target = VISUALS / f"{case['id']}_{page}"
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-r",
                    "95",
                    "-png",
                    str(source),
                    str(target),
                ],
                check=True,
                capture_output=True,
            )
            file = target.with_suffix(".png")
            images.append(Image.open(file).convert("RGB"))
            pages.append(
                {"page": page, "image": file.relative_to(ROOT).as_posix(), "sha256": sha(file)}
            )
        panel = Image.new(
            "RGB", (sum(i.width for i in images), max(i.height for i in images) + 28), "white"
        )
        draw, x = ImageDraw.Draw(panel), 0
        for page, image in zip(case["pages"], images):
            panel.paste(image, (x, 28))
            draw.text((x + 8, 7), f"{case['id']} / PDF page {page}", fill="black")
            x += image.width
        file = VISUALS / (case["id"] + "_panel.png")
        panel.save(file)
        records.append(
            {
                "id": case["id"],
                "source": case["source"],
                "source_sha256": case["source_sha256"],
                "pages": pages,
                "panel": file.relative_to(ROOT).as_posix(),
                "panel_sha256": sha(file),
            }
        )
    save(
        CHECKS / "financial_visual_catalogue.json",
        {"source_sha256": sha(Path(__file__)), "records": records, "visually_reviewed": False},
        immutable=True,
    )
    print("Rendered", len(records), "original-source cases", flush=True)


if __name__ == "__main__":
    main()
