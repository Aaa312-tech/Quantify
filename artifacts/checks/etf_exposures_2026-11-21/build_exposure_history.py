"""Build source-bound ETF identities and reported equity partitions as of each session."""

from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import joblib

from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

ROOT = Path.cwd()
CHECKS = ROOT / "artifacts/checks/etf_exposures_2026-11-21"
OUTPUT = ROOT / "data/processed/etf_exposures_2026-11-21"
PROTOCOL_PATH = ROOT / "configs/protocols/etf_exposures.json"


def release_day(record):
    return max(
        record.get(key, "") for key in ("catalogue_date", "source_directory_date", "notice_date")
    )


def next_session(calendar, value):
    index = bisect_right(calendar, value)
    return calendar[index] if index < len(calendar) else None


def ratio(amount, nav):
    value = Fraction(amount) / Fraction(nav)
    return {
        "amount_cny": str(amount),
        "nav_fraction": {"numerator": value.numerator, "denominator": value.denominator},
    }


def select_report(reports, session):
    known = [r for r in reports if r["available_session"] and r["available_session"] <= session]
    return max(
        known,
        key=lambda r: (
            r["identity"]["period_end"],
            r["report_type"] != "quarterly",
            r["information_date"],
            r["source"],
        ),
        default=None,
    )


def select_summary(summaries, session):
    known = [r for r in summaries if r["available_session"] and r["available_session"] <= session]
    return max(
        known,
        key=lambda r: (r["information_date"], r["agreed_fields"]["prepared_on"], r["source_path"]),
        default=None,
    )


def alias_inventory(links, session):
    result = defaultdict(dict)
    for link in links:
        if link["available_session"] is None or link["available_session"] > session:
            continue
        old, new = link["old_symbol"][2:], link["current_symbol"][2:]
        if old == new:
            continue
        for left, right in ((old, new), (new, old)):
            result[left].setdefault(right, []).append(link)
    if any(len(pairs) != 1 for pairs in result.values()):
        raise ValueError("Available identity links are not one-to-one")
    return result


def match_stock(code, release, aliases):
    if release is None:
        return None, "NO_AVAILABLE_CAPCO_RELEASE", []
    inventory = release["inventory"]
    direct = inventory.get(code)
    alternatives = [
        (other, inventory[other]) for other in aliases.get(code, {}) if other in inventory
    ]
    if direct is not None and alternatives:
        return None, "AMBIGUOUS_DIRECT_AND_ALIAS", []
    if direct is not None:
        return direct, "DIRECT", []
    if len(alternatives) == 1:
        other, row = alternatives[0]
        evidence = [
            {
                k: link.get(k)
                for k in (
                    "old_symbol",
                    "current_symbol",
                    "source",
                    "source_sha256",
                    "page",
                    "row",
                    "available_session",
                    "information_date",
                )
            }
            for link in aliases[code][other]
        ]
        return row, "AVAILABLE_IDENTITY_LINK", evidence
    return None, "CODE_ABSENT_FROM_CURRENT_CAPCO_RELEASE", []


def equity_profile(report, release, aliases):
    nav = Decimal(report["reported_nav"]["amount_cny"])
    stocks = [
        (gi, ri, row)
        for gi, group in enumerate(report["groups"])
        if group["kind"] == "stocks"
        for ri, row in enumerate(group["rows"])
    ]
    sector_groups = [g for g in report["groups"] if g["kind"] == "sectors"]
    if not sector_groups and not all(report["stock_absence"][name] for name in ("fitz", "pypdf")):
        raise ValueError("A stock total cannot be inferred from a missing schedule")
    sector_total = sum(
        (
            Decimal(r["market_value_cny"])
            for g in sector_groups
            for r in g["rows"]
            if r["code"] == "TOTAL"
        ),
        Decimal(0),
    )
    named_total = sum((Decimal(row["market_value_cny"]) for _, _, row in stocks), Decimal(0))
    undisclosed = sector_total - named_total
    if undisclosed < 0 or (
        report["schedule_coverage"] == "ALL_REPORTED_STOCKS" and undisclosed != 0
    ):
        raise ValueError("Named and undisclosed stock partitions do not reconcile")
    residual = (
        Decimal(report["equity_sector_residual"]["amount_cny"])
        if report["equity_sector_residual"]
        else Decimal(0)
    )
    rows, by_division, unknown = [], defaultdict(Decimal), Decimal(0)
    for gi, ri, row in stocks:
        match, reason, links = match_stock(row["code"], release, aliases)
        amount = Decimal(row["market_value_cny"])
        group = "capco:" + match["section"] + match["division"] if match else None
        if group is None:
            unknown += amount
        else:
            by_division[group] += amount
        rows.append(
            {
                "source_group": gi,
                "source_row": ri,
                "code": row["code"],
                "name": row["name"],
                "market_value_cny": row["market_value_cny"],
                "division": group,
                "match": reason,
                "classification_row": {
                    k: match[k] for k in ("code", "name", "section", "division", "page", "row")
                }
                if match
                else None,
                "identity_links": links,
            }
        )
    if sum(by_division.values(), Decimal(0)) + unknown != named_total:
        raise ValueError("Matched/unmatched named partitions do not reconcile")
    return {
        "reported_nav_cny": str(nav),
        "reported_stock_schedule": report["schedule_coverage"],
        "source_report": report["source"],
        "source_sha256": report["source_sha256"],
        "period_end": report["identity"]["period_end"],
        "available_session": report["available_session"],
        "named_stocks": rows,
        "matched_divisions": {key: ratio(value, nav) for key, value in sorted(by_division.items())},
        "matched_named": ratio(named_total - unknown, nav),
        "unmatched_named": ratio(unknown, nav),
        "undisclosed_stocks": ratio(undisclosed, nav),
        "source_sector_stocks": ratio(sector_total, nav),
        "signed_equity_sector_residual": ratio(residual, nav),
        "source_stock_absence": report["stock_absence"] if not stocks else None,
        "source_sector_groups": [
            {
                "section": g["section"],
                "rows": [
                    {k: row[k] for k in ("code", "market_value_cny", "percent_nav")}
                    for row in g["rows"]
                ],
            }
            for g in sector_groups
        ],
        "non_equity_asset_rows": [
            {
                "section": g["section"],
                "source_row": ri,
                "name": row["name"],
                "amount_cny": row["amount_cny"],
                "percent_total_assets": row["percent_total_assets"],
            }
            for g in report["groups"]
            if g["kind"] == "assets"
            for ri, row in enumerate(g["rows"])
            if row["ordinal"] is not None and row["name"] not in ("权益投资", "合计")
        ],
        "capco_source": {
            key: release[key]
            for key in (
                "catalogue_date",
                "available_session",
                "source",
                "source_sha256",
                "output",
                "output_sha256",
            )
        }
        if release
        else None,
        "source_sectors_are_capco_divisions": False,
        "partial_weights_normalized": False,
        "residual_allocated_to_cash_or_industries": False,
        "complete_economic_exposure_certified": False,
    }


def inputs():
    protocol = read(PROTOCOL_PATH)
    for path, expected in protocol["source_sha256"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("Registered source changed: " + path)
    if sha(ROOT / protocol["baseline_bundle"]) != protocol["baseline_bundle_sha256"]:
        raise ValueError("Active bundle changed")
    bundle = joblib.load(ROOT / protocol["baseline_bundle"])
    calendar = [value.strftime("%Y-%m-%d") for value in bundle["calendar"]]
    reports = load_gzip(ROOT / "data/processed/etf_holdings_cells_2026-11-21/observations.json.gz")
    for report in reports:
        report["information_date"] = max(
            report["catalogue_date"], report["identity"]["delivery_date"]
        )
        report["available_session"] = next_session(calendar, report["information_date"])
    summaries = read(ROOT / "data/processed/etf_sources_2026-11-21/reviewed_observations.json")[
        "records"
    ]
    for summary in summaries:
        summary["information_date"] = max(
            summary["catalogue_date"], summary["agreed_fields"]["prepared_on"]
        )
        summary["available_session"] = next_session(calendar, summary["information_date"])
    releases = []
    for source in read(ROOT / "artifacts/checks/industry_cells_2026-11-21/review.json")["sources"]:
        if source["aliases"]:
            continue
        data = load_gzip(ROOT / source["output"])
        inventory = {row["code"]: row for row in data["records"]}
        if len(inventory) != len(data["records"]):
            raise ValueError("CAPCO table contains duplicate codes")
        releases.append(
            {
                **{key: data[key] for key in ("catalogue_date", "source", "source_sha256")},
                "available_session": next_session(calendar, data["catalogue_date"]),
                "inventory": inventory,
                "output": source["output"],
                "output_sha256": source["sha256"],
            }
        )
    links = read(ROOT / "artifacts/checks/industry_cells_2026-11-21/identity_links.json")["records"]
    for link in links:
        link["information_date"] = release_day(link)
        link["available_session"] = next_session(calendar, link["information_date"])
    return protocol, calendar, reports, summaries, releases, links


def main():
    if read(CHECKS / "financial_audit.json")["status"] != "PASS":
        raise ValueError("Financial field review did not pass")
    protocol, calendar, reports, summaries, releases, links = inputs()
    identities = {r["symbol"]: r for r in read(CHECKS / "identity_mapping.json")["records"]}
    save(
        CHECKS / "history_source_identity.json",
        {
            "started_at": now(),
            "source_sha256": sha(Path(__file__)),
            "protocol_sha256": sha(PROTOCOL_PATH),
            "identity_mapping_sha256": sha(CHECKS / "identity_mapping.json"),
            "financial_audit_sha256": sha(CHECKS / "financial_audit.json"),
            "financial_observations_sha256": sha(OUTPUT / "financial_observations.json.gz"),
        },
        immutable=True,
    )
    profiles, daily, cache = [], [], {}
    counts = Counter()
    for session in calendar:
        available_releases = [
            r for r in releases if r["available_session"] and r["available_session"] <= session
        ]
        release = max(available_releases, key=lambda r: r["catalogue_date"], default=None)
        aliases = alias_inventory(links, session)
        alias_dates = tuple(
            sorted(
                {
                    release_day(link)
                    for link in links
                    if link["available_session"] and link["available_session"] <= session
                }
            )
        )
        for symbol in protocol["symbols"]:
            report = select_report([r for r in reports if r["symbol"] == symbol], session)
            summary = select_summary([r for r in summaries if r["symbol"] == symbol], session)
            if report is None or summary is None:
                daily.append(
                    {
                        "session": session,
                        "symbol": symbol,
                        "profile": None,
                        "reason": "NO_AVAILABLE_REPORT_OR_IDENTITY",
                    }
                )
                continue
            identity = identities[symbol]
            wording = summary["reviewed_benchmark"]
            if (
                wording not in identity["accepted_wordings"]
                or summary["agreed_fields"]["fund_type"]["value"] != identity["legal_fund_type"]
            ):
                raise ValueError("Unregistered fund identity wording")
            key = (
                report["source"],
                summary["source_path"],
                release["catalogue_date"] if release else None,
                alias_dates,
            )
            if key not in cache:
                profile = equity_profile(report, release, aliases)
                profile.update(
                    symbol=symbol,
                    first_session=session,
                    identity={
                        "benchmark_id": identity["benchmark_id"],
                        "benchmark_wording": wording,
                        "legal_fund_type": identity["legal_fund_type"],
                        "source": summary["source_path"],
                        "source_sha256": summary["source_sha256"],
                        "information_date": summary["information_date"],
                        "available_session": summary["available_session"],
                    },
                    financial_observation_source=report["source"],
                    financial_fields="data/processed/etf_exposures_2026-11-21/financial_observations.json.gz",
                )
                cache[key] = len(profiles)
                profiles.append(profile)
            index = cache[key]
            profile = profiles[index]
            age = (
                date.fromisoformat(session) - date.fromisoformat(report["identity"]["period_end"])
            ).days
            daily.append(
                {"session": session, "symbol": symbol, "profile": index, "report_age_days": age}
            )
            counts["fund_sessions"] += 1
            counts["sessions_with_unmatched_named_stocks"] += (
                Decimal(profile["unmatched_named"]["amount_cny"]) != 0
            )
            counts["sessions_with_undisclosed_stocks"] += (
                Decimal(profile["undisclosed_stocks"]["amount_cny"]) != 0
            )
    for name, content in (
        ("equity_profiles.json.gz", profiles),
        ("session_profiles.json.gz", daily),
    ):
        dump_gzip(OUTPUT / name, content)
    summary = {
        "status": "CONSTRUCTED_INDEPENDENT_AUDIT_PENDING",
        "completed_at": now(),
        "source_sha256": sha(Path(__file__)),
        "calendar_start": calendar[0],
        "calendar_end": calendar[-1],
        "calendar_sessions": len(calendar),
        "funds": len(protocol["symbols"]),
        "profiles": len(profiles),
        "counts": dict(counts),
        "missing_identity_or_report_sessions": sum(d["profile"] is None for d in daily),
        "profile_stock_rows": sum(len(p["named_stocks"]) for p in profiles),
        "outputs": {
            name: sha(OUTPUT / name)
            for name in ("equity_profiles.json.gz", "session_profiles.json.gz")
        },
        "activated": False,
        "complete_economic_exposure_certified": False,
        "historical_publication_time_certified": False,
    }
    save(CHECKS / "history_construction.json", summary, immutable=True)
    print(summary)


if __name__ == "__main__":
    main()
