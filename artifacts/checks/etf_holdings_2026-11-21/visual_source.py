"""Render paired original PDF rows, preserving the source graphics for visual review."""

import argparse
import math
import subprocess
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    root = Path.cwd()
    source, out = root / args.input, root / args.output
    if out.exists():
        raise FileExistsError("Keep previous visual proofs")
    out.mkdir(parents=True)
    cases = read(source)["cases"]
    pdf = fitz.open()
    for index in range(math.ceil(len(cases) / 4)):
        page = pdf.new_page(width=1020, height=760)
        for slot, case in enumerate(cases[index * 4 : index * 4 + 4]):
            original_path = root / case["source"]
            if sha(original_path) != case["source_sha256"]:
                raise ValueError("Original PDF changed")
            top = 12 + slot * 185
            caption = f"Case {case['id']}: {original_path.parent.name} / {original_path.stem} / pages {case['left']['page']} -> {case['right']['page']}"
            page.insert_text((12, top + 10), caption, fontsize=10)
            with fitz.open(original_path) as original:
                for column, key in enumerate(("left", "right")):
                    fragment = case[key]
                    source_page = original[fragment["page"] - 1]
                    rect = fitz.Rect(
                        55,
                        max(0, fragment["bbox"][1] - 22),
                        source_page.rect.width - 35,
                        min(source_page.rect.height, fragment["bbox"][3] + 25),
                    )
                    target = fitz.Rect(12 + column * 510, top + 24, 503 + column * 510, top + 175)
                    page.show_pdf_page(target, original, fragment["page"] - 1, clip=rect)
    path = out / "source_rows.pdf"
    pdf.save(path)
    subprocess.run(
        ["pdftoppm", "-r", "110", "-png", str(path), str(out / "page")],
        check=True,
        capture_output=True,
    )
    images = sorted(out.glob("page-*.png"))
    save(
        out / "render.json",
        {
            "rendered_at": now(),
            "source_sha256": sha(Path(__file__)),
            "input": source.relative_to(root).as_posix(),
            "input_sha256": sha(source),
            "cases": len(cases),
            "images": [{"path": p.relative_to(root).as_posix(), "sha256": sha(p)} for p in images],
            "pdf_sha256": sha(path),
            "visual_review_completed": False,
        },
        immutable=True,
    )
    print("rendered", len(cases), "cases on", len(images), "pages", flush=True)


if __name__ == "__main__":
    main()
