"""Finish the source-by-source structural audit, including rejected original copies."""

import importlib.util
from collections import Counter
from pathlib import Path

from quantfolio.storage import now, read, save, sha

root = Path.cwd()
out = root / "artifacts/checks/etf_holdings_2026-11-21"
source = out / "pdf_health_source.py"
spec = importlib.util.spec_from_file_location("etf_pdf_health_original", source)
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
collections = [
    root / f"data/raw/{name}/collection.json"
    for name in (
        "etf_reports_2026-11-21",
        "etf_reports_retry_2026-11-21",
        "etf_reports_truncation_2026-11-21",
    )
]
rows = [r for path in collections for r in read(path)["documents"] if r["status"] == "RECEIVED"]
if len(rows) != 553 or len({r["download"]["path"] for r in rows}) != 553:
    raise ValueError("Unexpected source-copy inventory")
if (out / "pdf_health_review_protocol.json").exists():
    raise FileExistsError("Keep the previous review")
save(
    out / "pdf_health_review_protocol.json",
    {
        "registered_at": now(),
        "source_sha256": sha(Path(__file__)),
        "inspector_sha256": sha(source),
        "initial_failure_log_sha256": sha(root / ".git/etf_holdings_pdf_health.log"),
        "collections": {p.relative_to(root).as_posix(): sha(p) for p in collections},
        "change": "Record a per-source reader failure and continue the independent audit. Include both the truncated original and the complete same-URL refetch.",
    },
    immutable=True,
)
results = []
for index, row in enumerate(rows, 1):
    try:
        result = original.inspect(row)
    except Exception as error:
        result = {
            "symbol": row["symbol"],
            "source": row["download"]["path"],
            "source_sha256": row["download"]["sha256"],
            "status": "READER_FAILED",
            "error": f"{type(error).__name__}: {error}",
            "page_count_agrees": False,
        }
    results.append(result)
    if index % 100 == 0:
        print(index, "/", len(rows), dict(Counter(r["status"] for r in results)), flush=True)
save(
    out / "pdf_health_review.json",
    {
        "completed_at": now(),
        "source_sha256": sha(Path(__file__)),
        "inspector_sha256": sha(source),
        "documents": results,
        "counts": dict(Counter(r["status"] for r in results)),
        "original_files_modified": False,
        "content_acceptance_implied": False,
    },
    immutable=True,
)
print("FINISHED", dict(Counter(r["status"] for r in results)), flush=True)
