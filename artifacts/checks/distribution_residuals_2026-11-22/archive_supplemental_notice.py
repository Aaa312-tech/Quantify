"""Archive the located Jinjiang proposal without replacing the received archive."""

from importlib.metadata import version
from pathlib import Path

import pymupdf
from pypdf import PdfReader

from quantfolio.storage import _atomic_write, digest, dump_gzip, load_gzip, now, read, save, sha

BASE = Path("artifacts/checks/distribution_residuals_2026-11-22")
PROTOCOL = Path("configs/protocols/supplemental_amendment_sources.json")
CATALOGUE = Path("data/raw/disclosures_2026-11-21/indexes/sh600754/distributions/page_001.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def find_rows(value, document_id):
    if isinstance(value, dict):
        if value.get("announcementId") == document_id:
            yield value
        for child in value.values():
            yield from find_rows(child, document_id)
    elif isinstance(value, list):
        for child in value:
            yield from find_rows(child, document_id)


def main():
    protocol = read(PROTOCOL)
    for item in protocol["inputs"]:
        require(sha(item["path"]) == item["sha256"], f"Input changed: {item['path']}")
    supplement = protocol["supplement"]
    source = Path(".git/jinjiang_original_notice_1221086914.pdf")
    target = Path(supplement["target"])
    require(sha(source) == supplement["sha256"], "Acquired original differs")
    prior_receipt = Path(supplement["prior_receipt"])
    response = read(prior_receipt)
    require(response["sha256"] == sha(source), "Acquisition receipt differs")
    require(response["bytes"] == source.stat().st_size, "Acquired size differs")
    require(response["url"] == supplement["url"], "Acquired URL differs")
    rows = {digest(row): row for row in find_rows(read(CATALOGUE), supplement["id"])}
    require(len(rows) == 1, "Original catalogue row is not unique")
    row = next(iter(rows.values()))
    require("https://static.cninfo.com.cn/" + row["adjunctUrl"] == supplement["url"], "URL")
    _atomic_write(target, source.read_bytes(), immutable=True)
    acquisition_path = target.with_suffix(".acquisition.json")
    _atomic_write(acquisition_path, prior_receipt.read_bytes(), immutable=True)
    receipt_path = target.with_suffix(".json")
    save(
        receipt_path,
        {
            "id": supplement["id"],
            "url": supplement["url"],
            "path": target.as_posix(),
            "sha256": sha(target),
            "bytes": target.stat().st_size,
            "archived_at": now(),
            "acquired_at": None,
            "acquisition_time_note": "Initial receipt did not capture time; not inferred.",
            "already_acquired_before_protocol": True,
            "catalogue": {"path": CATALOGUE.as_posix(), "sha256": sha(CATALOGUE), "row": row},
            "acquisition_receipt": {
                "path": acquisition_path.as_posix(),
                "sha256": sha(prior_receipt),
            },
            "protocol": {"path": PROTOCOL.as_posix(), "sha256": sha(PROTOCOL)},
            "status": "ARCHIVED_SUPPLEMENT_NOT_ACTIVE_INPUT",
        },
        immutable=True,
    )
    archive = load_gzip(BASE / "source_text.json.gz")["documents"]
    archive.update(load_gzip(BASE / "related_source_text.json.gz")["documents"])
    sources = {
        item["source"]["id"]: item["source"]
        for item in archive.values()
        if item["source"]["id"] in protocol["comparison_ids"]
    }
    require(set(sources) == {"1221114875", "1221258694"}, "Comparison scope differs")
    sources[supplement["id"]] = {
        "id": supplement["id"],
        "path": target.as_posix(),
        "sha256": sha(target),
        "receipt": receipt_path.as_posix(),
        "receipt_sha256": sha(receipt_path),
        "title": row["announcementTitle"],
        "catalog_date": supplement["catalog_date"],
        "url": supplement["url"],
        "published_code": "600754",
        "kind": "original_proposal_supplement",
    }
    documents = {}
    for document_id in protocol["comparison_ids"]:
        metadata = sources[document_id]
        path = metadata["path"]
        require(sha(path) == metadata["sha256"], "Source bytes differ")
        require(sha(metadata["receipt"]) == metadata["receipt_sha256"], "Receipt bytes differ")
        pypdf_pages = [page.extract_text() for page in PdfReader(path).pages]
        with pymupdf.open(path) as pdf:
            mupdf_pages = [page.get_text(sort=False) for page in pdf]
        expected_pages = 5 if document_id == "1221258694" else 2
        require(len(pypdf_pages) == len(mupdf_pages) == expected_pages, "Page count differs")
        require(all(pypdf_pages) and all(mupdf_pages), "Empty extraction")
        documents[document_id] = {
            "source": metadata,
            "readers": {"pypdf": pypdf_pages, "mupdf_unsorted": mupdf_pages},
        }
    text_path = BASE / "supplemental_source_text.json.gz"
    require(not text_path.exists(), "Refusing to replace extracted evidence")
    dump_gzip(text_path, {"documents": documents})
    result = {
        "status": "ARCHIVED_AND_EXTRACTED_NOT_ACCEPTED",
        "completed_at": now(),
        "script_sha256": sha(__file__),
        "protocol_sha256": sha(PROTOCOL),
        "reader_versions": {"pypdf": version("pypdf"), "pymupdf": version("PyMuPDF")},
        "comparison_documents": 3,
        "comparison_pages": 9,
        "page_reader_extractions": 18,
        "original_archive_documents": 1135,
        "original_archive_pages": 3780,
        "supplement_documents": 1,
        "supplement_pages": 2,
        "outputs": {
            p.as_posix(): sha(p) for p in [target, acquisition_path, receipt_path, text_path]
        },
        "accepted_facts_added": 0,
        "active_inputs_changed": False,
    }
    save(BASE / "supplemental_acquisition.json", result, immutable=True)
    print(result["status"], "3 documents / 9 pages / 18 reader pages")


if __name__ == "__main__":
    main()
