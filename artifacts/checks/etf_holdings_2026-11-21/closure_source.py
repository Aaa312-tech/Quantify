"""Close the report-field review without activating portfolio observations."""

from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from quantfolio.storage import digest, load_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_holdings_2026-11-21"
DATA = ROOT / "data/processed/etf_holdings_cells_2026-11-21"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    destination = CHECKS / "cell_closure.json"
    require(not destination.exists(), "Preserve the earlier closure")
    protocol = read(ROOT / "configs/protocols/etf_holdings.json")
    extraction = read(CHECKS / "extraction_reviewed.json")
    review = read(CHECKS / "cell_review.json")
    identity = read(CHECKS / "cell_source_identity.json")
    observations = load_gzip(DATA / "observations.json.gz")
    documents = extraction["documents"]
    reports = [r for r in documents if not r["publication_notice"]]
    notices = [r for r in documents if r["publication_notice"]]
    health = read(CHECKS / "pdf_health_review.json")
    health_by_source = {r["source"]: r for r in health["documents"]}
    require(len(reports) == len(observations) == 528 and len(notices) == 24, "Report count")
    require(len({r["url"] for r in documents}) == 552, "Unique document URLs")
    require(review["observations_sha256"] == sha(DATA / "observations.json.gz"), "Cells hash")
    require(
        identity["source_sha256"]
        == review["source_sha256"]
        == sha(CHECKS / "cell_review_source.py"),
        "Executed cell reviewer changed",
    )
    for filename, key in [
        ("cell_review_protocol.json", "protocol_sha256"),
        ("geometry_review_protocol.json", "geometry_protocol_sha256"),
        ("manual_boundaries_reviewed.json", "manual_review_sha256"),
    ]:
        require(identity[key] == sha(CHECKS / filename), "Cell protocol binding: " + filename)
    require(
        identity["prior_source_sha256"] == sha(CHECKS / "review_source.py"),
        "Shared first-review helper changed",
    )

    by_source = {r["source"]: r for r in observations}
    require(len(by_source) == len(observations), "Duplicate report observations")
    timing, counts, stocks, boundaries = [], Counter(), [], []
    for doc in reports:
        source = doc["source"]
        item = by_source[source]
        require(doc["status"] == "IDENTITY_AND_SECTION_REVIEWED", "Identity incomplete")
        require(doc["identity"] == item["identity"], "Identity drift")
        require(sha(ROOT / source) == doc["source_sha256"] == item["source_sha256"], "PDF hash")
        require(sha(ROOT / doc["extracted_pages"]) == doc["extraction_sha256"], "Page hash")
        source_health = health_by_source[source]
        require(source_health["source_sha256"] == doc["source_sha256"], "Health source hash")
        require(source_health["page_count_agrees"], "Independent page counts differ")
        require(source_health["status"] != "READER_FAILED", "Rejected PDF was accepted")
        require(
            not item["errors"] and item["status"] == "SOURCE_CELLS_REVIEWED_EXPOSURE_NOT_ACTIVATED",
            "Financial cell review incomplete",
        )
        inventory = item["independent_stock_inventory"]
        require(
            inventory["fitz"] == inventory["pypdf"]
            or (
                inventory["fitz"]["rows"] == inventory["pypdf"]["rows"]
                and inventory["fitz"]["ordered_inventory_sha256"]
                == inventory["pypdf"]["ordered_inventory_sha256"]
            ),
            "Independent ordered inventory differs",
        )
        for group in item["groups"]:
            counts[group["kind"]] += len(group["rows"])
            if group["kind"] == "stocks":
                stocks.extend(group["rows"])
            for row in [*group["headers"], *group["rows"]]:
                if len(row["fragments"]) > 1:
                    require(len(row["fragments"]) == 2, "Unreviewed multipage continuation")
                    boundaries.append((source, group["kind"], row))
        timing.append(
            {
                "symbol": doc["symbol"],
                "source": source,
                "report_type": doc["report_type"],
                "period_end": doc["identity"]["period_end"],
                "delivery_date": doc["identity"]["delivery_date"],
                "catalogue_date": doc["catalogue_date"],
                "catalogue_delay_days": (
                    date.fromisoformat(doc["catalogue_date"])
                    - date.fromisoformat(doc["identity"]["period_end"])
                ).days,
                "catalogue_delivery_difference_days": (
                    date.fromisoformat(doc["catalogue_date"])
                    - date.fromisoformat(doc["identity"]["delivery_date"])
                ).days,
                "exact_publication_time_certified": False,
            }
        )
    require(len(stocks) == 33433 and len(boundaries) == 132, "Cell or boundary inventory changed")

    expected_periods = {
        "quarterly": {"2022-12-31"}
        | {
            f"{year}-{suffix}"
            for year in range(2023, 2027)
            for suffix in ("03-31", "06-30", "09-30", "12-31")
            if f"{year}-{suffix}" <= "2026-09-30"
        },
        "annual": {f"{year}-12-31" for year in range(2022, 2026)},
        "interim": {f"{year}-06-30" for year in range(2023, 2027)},
    }
    funds = []
    for symbol in protocol["symbols"]:
        selected = [r for r in reports if r["symbol"] == symbol]
        require(len(selected) == 24, "Fund report count: " + symbol)
        for kind, periods in expected_periods.items():
            actual = [r["identity"]["period_end"] for r in selected if r["report_type"] == kind]
            require(len(actual) == len(periods) and set(actual) == periods, "Period coverage")
        funds.append(
            {
                "symbol": symbol,
                "report_types": dict(Counter(r["report_type"] for r in selected)),
                "catalogue_date_min": min(r["catalogue_date"] for r in selected),
                "catalogue_date_max": max(r["catalogue_date"] for r in selected),
                "pre_model_anchor": min(r["catalogue_date"] for r in selected)
                < protocol["catalogue_period"]["first_model_session"],
            }
        )
    require(len(funds) == 22 and all(r["pre_model_anchor"] for r in funds), "Fund coverage")

    visual_counts = {}
    for prefix, protocol_name, render_name, expected_count in [
        ("manual_boundaries", "manual_boundary_protocol.json", "manual_boundaries", 10),
        ("continuations", "continuation_visual_protocol.json", "continuations", 131),
    ]:
        checked = read(CHECKS / (prefix + "_reviewed.json"))
        visual_protocol = read(CHECKS / protocol_name)
        render_file = CHECKS / "visuals" / render_name / "render.json"
        render = read(render_file)
        require(checked["status"] == "VISUALLY_REVIEWED", "Visual review incomplete")
        require(checked["protocol_sha256"] == sha(CHECKS / protocol_name), "Visual scope changed")
        require(checked["render_sha256"] == sha(render_file), "Visual render changed")
        require(checked["reviewed_case_ids"] == list(range(1, expected_count + 1)), "Visual IDs")
        require(len(visual_protocol["cases"]) == expected_count, "Visual case count")
        for image in render["images"]:
            require(sha(ROOT / image["path"]) == image["sha256"], "Visual image changed")
        visual_counts[prefix] = expected_count
    general = read(CHECKS / "continuation_visual_protocol.json")
    require(
        general["observations_sha256"] == sha(DATA / "observations.json.gz"), "Visual data hash"
    )
    expected_boundaries = Counter(
        (source, kind, digest(row["fragments"]))
        for source, kind, row in boundaries
        if not row.get("manual_boundary_id")
    )
    rendered_boundaries = Counter(
        (c["source"], c["kind"], digest([c["left"], c["right"]]))
        for c in general["cases"]
        if c["kind"] != "reported_nav"
    )
    require(expected_boundaries == rendered_boundaries, "Missing or substituted visual boundaries")
    split_nav = [
        r
        for r in observations
        if r["reported_nav"].get("method") == "adjacent-page label continuation"
    ]
    require(len(split_nav) == 9, "NAV continuation count")
    expected_nav = Counter((r["source"], digest(r["reported_nav"]["fragments"])) for r in split_nav)
    rendered_nav = Counter(
        (c["source"], digest([c["left"], c["right"]]))
        for c in general["cases"]
        if c["kind"] == "reported_nav"
    )
    require(expected_nav == rendered_nav, "Missing NAV visual boundary")
    residuals = [
        Decimal(r["equity_sector_residual"]["amount_cny"])
        for r in observations
        if r["equity_sector_residual"]
    ]
    save(
        DATA / "coverage.json",
        {
            "created_at": now(),
            "funds": funds,
            "report_timing": timing,
            "observations_sha256": sha(DATA / "observations.json.gz"),
            "availability_policy_activated": False,
        },
        immutable=True,
    )
    save(
        destination,
        {
            "status": "SOURCE_FIELDS_REVIEWED_EXPOSURE_NOT_ACTIVATED",
            "completed_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(ROOT / "configs/protocols/etf_holdings.json"),
            "cell_review_sha256": sha(CHECKS / "cell_review.json"),
            "extraction_review_sha256": sha(CHECKS / "extraction_reviewed.json"),
            "observations_sha256": sha(DATA / "observations.json.gz"),
            "coverage_sha256": sha(DATA / "coverage.json"),
            "funds": 22,
            "fund_reports": 528,
            "publication_notices": 24,
            "report_types": dict(Counter(r["report_type"] for r in reports)),
            "original_report_pages": sum(r["pages"] for r in reports),
            "dual_reader_selected_pages": sum(r["dual_reader_pages"] for r in reports),
            "reviewed_rows": dict(counts),
            "portfolio_continuations": len(boundaries),
            "nav_continuations": len(split_nav),
            "visual_cases": visual_counts,
            "five_column_header_repairs": sum(len(r["grid_repairs"]) for r in observations),
            "pdf_health": dict(Counter(r["status"] for r in health["documents"])),
            "no_stock_reports_with_explicit_statements": sum(
                not any(g["kind"] == "stocks" for g in r["groups"]) for r in observations
            ),
            "equity_sector_residuals": {
                "reports": len(residuals),
                "nonzero": sum(v != 0 for v in residuals),
                "positive": sum(v > 0 for v in residuals),
                "negative": sum(v < 0 for v in residuals),
                "cash_or_sector_allocation_inferred": False,
            },
            "catalogue_delivery_dates_agree": sum(
                r["catalogue_delivery_difference_days"] == 0 for r in timing
            ),
            "catalogue_delay_days": {
                "min": min(r["catalogue_delay_days"] for r in timing),
                "max": max(r["catalogue_delay_days"] for r in timing),
            },
            "model_inputs_changed": False,
            "weights_renormalized": False,
            "exact_publication_time_certified": False,
            "daily_portfolios_certified": False,
            "derivative_or_total_economic_exposure_certified": False,
            "full_source_pages_visually_reviewed": False,
            "remaining": [
                "publication/as-of policy",
                "CAPCO taxonomy mapping",
                "non-equity and residual exposure treatment",
                "separately registered integration controls",
            ],
        },
        immutable=True,
    )
    print(read(destination), flush=True)


if __name__ == "__main__":
    main()
