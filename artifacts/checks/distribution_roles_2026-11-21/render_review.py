"""Render whole pages for layout coverage, independent differences and amendments."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path.cwd()
BASE = ROOT / "artifacts/checks/distribution_roles_2026-11-21"
POPPLER = Path(
    "C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1111-574-0_26.1111.574.0_x64__3k8sg7r9htsxt/dependencies/native/poppler/Library/bin/pdftoppm.exe"
)


def read(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    initial = read("independent_sources.json")
    closure = read("independent_closure.json")
    amendments = read("amendment_closure.json")
    queue = {}

    def add(source, page, reason):
        queue.setdefault((source, page), []).append(reason)

    for row in initial["roles"]:
        if row["passed"]:
            continue
        for clause in row["body_checks"]:
            if not clause["passed"]:
                add(row["source"]["source"], clause["page"], "independent_reading_order_difference")
    failed = {(r["symbol"], r["ex_date"]) for r in initial["fields"] if not r["passed"]}
    layouts = set()
    for row in closure["fields"]:
        for layout in row["layouts"]:
            if layout["family"] not in layouts:
                layouts.add(layout["family"])
                add(row["source"]["path"], layout["page"], "field_layout:" + layout["family"])
        if (row["symbol"], row["ex_date"]) in failed:
            for item in row["evidence"]["cash_per_share"]:
                add(row["source"]["path"], item["page"], "independent_gross_layout_difference")
    role_families = set()
    for row in closure["roles"]:
        if row["resolution"] == "RESOLVED_COMPANIONS" and row["role"] not in role_families:
            role_families.add(row["role"])
            add(row["source"]["source"], 1, "notice_role:" + row["role"])
            for item in row["body_checks"]:
                if item["passed"]:
                    add(row["source"]["source"], item["page"], "notice_role_body:" + row["role"])
                    break
    for row in amendments["records"]:
        for decision in row["relevance"]:
            if decision["decision"] == "AMENDED_GROSS_AGREES":
                add(row["source"], decision["page"], "amended_gross_amount")
                if "unchanged_amount_support" in decision:
                    add(row["source"], 2, "unchanged_per_share_amount")
        if any(word in row["title"] for word in ("修订", "补充", "撤销")):
            add(row["source"], 1, "amendment_scope")
        if row["id"] == "1221051868":
            for page in (9, 10):
                add(row["source"], page, "governance_scope")
    output = BASE / "pages"
    output.mkdir(exist_ok=False)
    records = []
    for number, ((source, page), reasons) in enumerate(sorted(queue.items()), 1):
        name = f"{number:02d}_{Path(source).stem}_page_{page}"
        subprocess.run(
            [
                str(POPPLER),
                "-f",
                str(page),
                "-l",
                str(page),
                "-singlefile",
                "-r",
                "110",
                "-png",
                str(ROOT / source),
                str(output / name),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        png = output / (name + ".png")
        records.append(
            {
                "source": source,
                "source_sha256": sha(ROOT / source),
                "page": page,
                "image": png.relative_to(ROOT).as_posix(),
                "image_sha256": sha(png),
                "reasons": sorted(set(reasons)),
            }
        )
    result = {
        "status": "RENDERED_PENDING_VISUAL_REVIEW",
        "pages": len(records),
        "renderer_sha256": sha(Path(__file__)),
        "field_layouts": sorted(layouts),
        "notice_roles": sorted(role_families),
        "records": records,
    }
    target = BASE / "rendered_pages.json"
    assert not target.exists()
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("WHOLE PAGES RENDERED", len(records), "FIELD LAYOUTS", len(layouts))


if __name__ == "__main__":
    main()
