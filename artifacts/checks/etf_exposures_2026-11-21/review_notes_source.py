"""Resolve registered note-layout failures without changing the original extraction."""

import importlib.util
import re
from collections import Counter
from pathlib import Path

import fitz
from pypdf import PdfReader

from quantfolio.storage import dump_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
OUTPUT = ROOT / "data/processed/etf_exposures_2026-11-21/followup"
spec = importlib.util.spec_from_file_location(
    "original_note_extraction", CHECKS / "extract_notes_source.py"
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def locate(text):
    starts = list(
        re.finditer(r"(?m)^\s*([67]\.4\.\d+\.\d+)(?:\.\d+)?\s*衍生金融(?:资产|工具)", text)
    )
    roots = {match[1] for match in starts}
    if len(roots) != 1:
        raise ValueError(f"Expected one financial-note root, found {sorted(roots)}")
    first = starts[0]
    prefix, number = first[1].rsplit(".", 1)
    following = prefix + "." + str(int(number) + 1)
    end = re.search(r"(?m)^\s*" + re.escape(following) + r"\s+[\u4e00-\u9fff]", text[first.end() :])
    if end is None:
        raise ValueError("Following peer note missing")
    return {
        "section": first[1],
        "following_section": following,
        "start": first.start(),
        "end": first.end() + end.start(),
        "following_heading_end": first.end() + end.end(),
    }


def graphic_note(doc):
    visual = read(CHECKS / "graphic_note_review.json")
    if (
        visual["status"] != "VISUALLY_REVIEWED"
        or visual["source"] != doc["source"]
        or visual["source_sha256"] != doc["source_sha256"]
        or sha(ROOT / visual["image"]) != visual["image_sha256"]
        or sha(ROOT / doc["source"]) != doc["source_sha256"]
    ):
        raise ValueError("Graphic note evidence differs")
    with fitz.open(ROOT / doc["source"]) as pdf:
        reader = PdfReader(ROOT / doc["source"])
        if len(reader.pages) != len(pdf):
            raise ValueError("Page counts differ")
        pages = [
            {
                "page": i + 1,
                "fitz": pdf[i].get_text(),
                "pypdf": reader.pages[i].extract_text(),
                "tables": base.grids(pdf[i]),
                "roles": ["graphic_derivative_note" if i == 24 else "current_period_context"],
            }
            for i in (23, 24)
        ]
        for name in ("fitz", "pypdf"):
            current = base.compact(pages[0][name])
            text = base.compact(pages[1][name])
            if "2026年6月30日" not in current or "本期末" not in current:
                raise ValueError("Current-period table context missing")
            anchor = "注:股票投资的估值增值和股票投资的公允价值均包含可退替代款的估值增值。"
            if anchor not in text or not text.split(anchor, 1)[1].startswith("无。无。无。无。"):
                raise ValueError("Independent body tokens or their order differ")
        target = (
            OUTPUT / "financial_notes" / doc["symbol"] / (Path(doc["source"]).stem + ".json.gz")
        )
        dump_gzip(
            target,
            {
                "source": doc["source"],
                "source_sha256": doc["source_sha256"],
                "pages_in_document": len(pdf),
                "pages": pages,
                "graphic_heading_evidence": visual,
                "source_original_modified": False,
            },
        )
    original = base.load_gzip(ROOT / doc["extracted_pages"])
    portfolio = [p for p in original["pages"] if p["page"] >= doc["portfolio_first_page"]]
    result = {
        key: doc[key]
        for key in (
            "symbol",
            "title",
            "catalogue_date",
            "report_type",
            "identity",
            "source",
            "source_sha256",
            "pages",
            "extracted_pages",
            "extraction_sha256",
        )
    }
    result.update(
        status="NOTE_BOUNDARIES_REVIEWED_FINANCIAL_FIELDS_PENDING",
        portfolio_pages=[p["page"] for p in portfolio],
        financial_notes=target.relative_to(ROOT).as_posix(),
        financial_notes_sha256=sha(target),
        derivative_note_pages=[25],
        additional_dual_reader_pages=2,
        lending_context=base.lending_context(portfolio),
        graphic_note_review_sha256=sha(CHECKS / "graphic_note_review.json"),
        headings_transcribed_from_image_not_text=True,
    )
    return result


def main():
    protocol = read(CHECKS / "notes_followup_protocol.json")
    if (
        (CHECKS / "notes_reviewed.json").exists()
        or protocol["initial_review_sha256"] != sha(CHECKS / "notes_extraction.json")
        or protocol["initial_source_sha256"] != sha(CHECKS / "extract_notes_source.py")
    ):
        raise ValueError("Original review changed or follow-up already exists")
    save(
        CHECKS / "notes_followup_source_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(CHECKS / "notes_followup_protocol.json"),
            "original_source_sha256": sha(CHECKS / "extract_notes_source.py"),
        },
        immutable=True,
    )
    originals = read(ROOT / "artifacts/checks/etf_holdings_2026-11-21/extraction_reviewed.json")[
        "documents"
    ]
    by_source = {d["source"]: d for d in originals}
    reviewed = read(CHECKS / "notes_extraction.json")
    replacements = {}
    base.locate = locate
    base.OUTPUT = OUTPUT
    for candidate in protocol["scope"]:
        doc = by_source[candidate["source"]]
        row = graphic_note(doc) if doc["symbol"] == "sz159995" else base.extract(doc)
        replacements[doc["source"]] = row
        print(doc["symbol"], doc["catalogue_date"], row["status"], flush=True)
    results = [replacements.get(d["source"], d) for d in reviewed["documents"]]
    if any(d["status"] == "EXTRACTION_FAILED" for d in results):
        raise ValueError("A note failure remains")
    save(
        CHECKS / "notes_reviewed.json",
        {
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "initial_review_sha256": sha(CHECKS / "notes_extraction.json"),
            "counts": dict(Counter(d["status"] for d in results)),
            "documents": results,
            "replaced_failures": len(replacements),
            "unchanged_original_results": len(results) - len(replacements),
            "financial_fields_validated": False,
            "model_inputs_changed": False,
        },
        immutable=True,
    )
    print("FINISHED", dict(Counter(d["status"] for d in results)), flush=True)


if __name__ == "__main__":
    main()
