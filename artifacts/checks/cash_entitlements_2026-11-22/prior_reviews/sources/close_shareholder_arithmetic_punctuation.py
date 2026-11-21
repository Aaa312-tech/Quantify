"""Close the observed full-stop layout difference while retaining the failed review."""

import re
import unicodedata
from pathlib import Path

import fitz

from quantfolio.storage import now, read, save, sha


root = Path.cwd()
base = root / ".git/distribution_frontier_2026_11_22"
original_file = base / "shareholder_independent_arithmetic.json"
original = read(original_file)
assert original["status"] == "DIAGNOSTIC_CHECKS_FAILED"
assert original["failures"] == [
    {"event": "sh600989/2025-05-13", "check": "institutional_gross_repeated"}
]
assert original["script_sha256"] == sha(root / ".git/check_shareholder_distribution_arithmetic.py")
visual_file = base / "shareholder_visual_observations.json"
assert original["visual_observations_sha256"] == sha(visual_file)
punctuation = {"sh600989/2024-07-24": "。", "sh600989/2025-05-13": ""}
records = []
for row in original["records"]:
    source, case = row["source"], row["case"]
    assert sha(root / source["path"]) == source["sha256"]
    with fitz.open(root / source["path"]) as document:
        raw_pages = [page.get_text(sort=True) for page in document]
    assert raw_pages == row["independent_pages"]
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw_pages[3]))
    start = text.index("(5)对于其他机构投资者和法人股东")
    end = text.index("五、有关咨询办法", start)
    paragraph = text[start:end]
    expected = ("实际派发现金红利为:每股实际派发现金红利人民币"
                + case["minority_gross"] + "元" + punctuation[case["event"]] + "(税前)。")
    checks = dict(row["checks"])
    checks["institutional_gross_repeated"] = expected in paragraph
    assert all(checks.values())
    records.append({**row, "checks": checks, "institutional_paragraph": paragraph,
                    "observed_full_stop_before_tax_qualifier": punctuation[case["event"]]})
save(base / "shareholder_arithmetic_closure.json", {
    "status": "DIAGNOSTIC_CHECKS_PASSED", "checked_at": now(),
    "script_sha256": sha(Path(__file__)), "prior_failure_sha256": sha(original_file),
    "visual_observations_sha256": sha(visual_file), "records": records,
    "checks": sum(len(row["checks"]) for row in records), "facts_accepted": 0,
    "scope": "All 24 diagnostic checks pass. The failed exact phrase expected a full stop before the tax qualifier in both documents; the observed 2025 page has none. Source bytes, all extracted page text, recipient scope, numeric checks and prior failed evidence remain unchanged. Related-notice closure and formal source registration are still required.",
}, immutable=True)
print("SHAREHOLDER DIAGNOSTIC CLOSURE PASS: 24 CHECKS; ORIGINAL FAILURE PRESERVED", flush=True)
