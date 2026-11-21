"""Read both source representations and render the previously unseen precision cases."""

import re
import shutil
import subprocess
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.distributions import extract_terms
from quantfolio.storage import now, read, save, sha


root = Path.cwd()
protocol_file = root / "configs/protocols/cash_entitlement_review.json"
protocol = read(protocol_file)
checks = root / protocol["checks_output"]
scope_file = root / protocol["scope"]["path"]
assert sha(scope_file) == protocol["scope"]["sha256"]
scope = read(scope_file)
cache = root / ".git/distribution_frontier_2026_11_22/precision_pages"
cache.mkdir()
program = shutil.which("pdftoppm")
assert program is not None
already_viewed = {"1219767154", "1220273317", "1220443864", "1224071707", "1220664181", "1223476469"}
records, images = [], []
for row in scope["records"]:
    source = row["source"]
    pdf = root / source["path"]
    assert sha(pdf) == source["sha256"]
    first = [page.extract_text() or "" for page in PdfReader(pdf).pages]
    with fitz.open(pdf) as document:
        second = [page.get_text(sort=True) for page in document]
    assert len(first) == len(second)
    identifier = row["candidate"]["id"]
    mentions = []
    for number, text in enumerate(first, 1):
        compact = re.sub(r"\s+", "", text)
        for match in re.finditer(r"每(?:10|十|1)?股.{0,70}(?:现金|红利|转增)|(?:登记日|除权除息日|上市日|发放日)|实际派发|虚拟分派|调整为", compact):
            mentions.append({"page": number, "text": compact[max(0, match.start()-65):min(len(compact), match.end()+180)]})
    records.append({
        "symbol": row["symbol"], "ex_date": row["ex_date"], "id": identifier,
        "source": source, "receipt": row["receipt"], "pages": len(first),
        "pypdf_pages": first, "mupdf_pages": second,
        "navigation_terms": extract_terms(first), "navigation_mentions": mentions,
        "status": "EXTRACTED_NOT_ACCEPTED",
    })
    if identifier not in already_viewed:
        for page in range(1, len(first) + 1):
            prefix = cache / f"{identifier}_page_{page:02d}"
            result = subprocess.run([program, "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "1800", "-png", str(pdf), str(prefix)], capture_output=True, check=True)
            png = prefix.with_suffix(".png")
            images.append({"id": identifier, "source": source, "page": page,
                "image": png.relative_to(root).as_posix(), "image_sha256": sha(png),
                "stderr": result.stderr.decode("utf-8", errors="replace")})
    print("PREPARED", row["symbol"], row["ex_date"], identifier, len(first), "pages", flush=True)
save(checks / "source_text.json", {
    "status": "EXTRACTED_NOT_ACCEPTED", "prepared_at": now(),
    "protocol_sha256": sha(protocol_file), "scope_sha256": sha(scope_file),
    "script_sha256": sha(Path(__file__)), "extractor_sha256": sha(root / "src/quantfolio/distributions.py"),
    "reader": {"pypdf": "6.9.2", "mupdf": fitz.VersionBind},
    "events": len(records), "pages": sum(row["pages"] for row in records),
    "records": records, "facts_accepted": 0,
    "scope": "Both complete text representations and broad navigation contexts. Extracted numeric candidates are not interpreted financial facts. Previously viewed six documents are bound to their existing page observations separately; newly rendered pages require actual inspection.",
}, immutable=True)
save(root / ".git/distribution_frontier_2026_11_22/precision_rendering.json", {
    "status": "RENDERED_NOT_REVIEWED", "created_at": now(), "script_sha256": sha(Path(__file__)),
    "scope_sha256": sha(scope_file), "records": images, "facts_accepted": 0,
}, immutable=True)
print("SOURCE TEXTS", len(records), "PAGES", sum(row["pages"] for row in records), "NEW RENDERINGS", len(images), flush=True)
