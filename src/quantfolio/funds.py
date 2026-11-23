"""Dated fund identities and partial equity-exposure estimates."""

from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import pandas as pd


def reported_components(profile):
    """Keep source NAV ratios, unknown stocks and signed discrepancies separate."""
    nav = Fraction(profile["reported_nav_cny"])
    if nav <= 0:
        raise ValueError("Fund report NAV must be positive")
    known = {
        key: Fraction(value["amount_cny"]) / nav
        for key, value in profile["matched_divisions"].items()
    }
    if any(not key.startswith("capco:") or value < 0 for key, value in known.items()):
        raise ValueError("Invalid reported company-division amount")
    components = {
        key: Fraction(profile[key]["amount_cny"]) / nav
        for key in ("unmatched_named", "undisclosed_stocks", "signed_equity_sector_residual")
    }
    if components["unmatched_named"] < 0 or components["undisclosed_stocks"] < 0:
        raise ValueError("Unknown stock amounts cannot be negative")
    components["residual_uncertainty"] = abs(components.pop("signed_equity_sector_residual"))
    contracts = profile["reported_index_contracts"]
    if any(row["kind"] != "stock_index_futures" for row in contracts):
        raise ValueError("Only reported stock-index contract values enter the equity proxy")
    components["reported_index_contracts"] = sum(
        (abs(Fraction(row["reported_market_value_cny"])) / nav for row in contracts), Fraction()
    )
    if (
        profile.get("complete_economic_exposure_certified") is not False
        or profile.get("economic_exposure_upper_bound", "missing") is not None
    ):
        raise ValueError("Reported fund proxies cannot certify complete economic exposure")
    return known, components


def uses_reported_exposure(meta):
    return any(
        instrument.get("fund_profile", {}).get("mode") == "reported"
        for instrument in meta.values()
        if instrument.get("fund_profile") is not None
    )


def concentration_rows(meta, names):
    """Rows are separate limits; uncertain exposure is not an additive NAV partition."""
    names = list(names)
    if any(meta[s]["industry"] is None for s in names):
        raise ValueError("Unknown industry in concentration constraints")
    rows: dict[str, dict[str, float]] = {
        group: {} for group in sorted({meta[s]["industry"] for s in names})
    }
    funds = {}
    divisions = {group for group in rows if group.startswith("capco:")}
    for symbol in names:
        rows[meta[symbol]["industry"]][symbol] = 1.0
        profile = meta[symbol].get("fund_profile")
        if profile is not None and profile["mode"] == "reported":
            funds[symbol] = reported_components(profile)
            divisions.update(funds[symbol][0])
    for group in sorted(divisions):
        row = rows.setdefault(group, {})
        for symbol, (known, components) in funds.items():
            row[symbol] = float(known.get(group, 0) + sum(components.values()))
    if funds:
        rows["reported:unnamed_division"] = {
            symbol: float(sum(components.values())) for symbol, (_, components) in funds.items()
        }
    return rows


def concentration_values(meta, values):
    return {
        group: sum(values[s] * coefficient for s, coefficient in row.items())
        for group, row in concentration_rows(meta, values).items()
    }


def purchase_capacity(meta, values, symbol, nav):
    """Pre-fee capacity; the integer-fill check must recompute the post-cost denominator."""
    rows = concentration_rows(meta, list(dict.fromkeys([*values, symbol])))
    limits = []
    for row in rows.values():
        coefficient = row.get(symbol, 0)
        current = sum(values.get(s, 0) * value for s, value in row.items())
        if coefficient > 0:
            limits.append(max(0.0, (0.30 * nav - current) / coefficient))
        elif current > 0.30 * nav + 1e-8:
            return 0.0
    return min(limits)


def exposure_report(meta, values, nav, as_of, *, sources=False):
    """Report the proxy and unbounded scope without treating missing economics as zero."""
    if nav <= 0:
        raise ValueError("Portfolio NAV must be positive")
    weights = {
        group: value / nav
        for group, value in concentration_values(meta, values).items()
        if group.startswith(("capco:", "reported:"))
    }
    unknown = dict.fromkeys(
        (
            "unmatched_named",
            "undisclosed_stocks",
            "residual_uncertainty",
            "reported_index_contracts",
        ),
        0.0,
    )
    capital, reports = 0.0, {}
    day = pd.Timestamp(as_of).tz_convert("Asia/Shanghai").date()
    for symbol, value in values.items():
        profile = meta[symbol].get("fund_profile")
        if profile is None or profile["mode"] != "reported":
            continue
        _, components = reported_components(profile)
        capital += value / nav
        for name, ratio in components.items():
            unknown[name] += value / nav * float(ratio)
        if sources:
            reports[symbol] = {
                "source": profile["source_report"],
                "source_sha256": profile["source_sha256"],
                "period_end": profile["period_end"],
                "report_age_days": (day - pd.Timestamp(profile["period_end"]).date()).days,
            }
    result = {
        "max_reported_equity_weight": max(weights.values(), default=0.0),
        "uncertified_fund_weight": capital,
        "uncertainty_weights": unknown,
        "complete_economic_exposure_certified": False,
        "economic_exposure_upper_bound": None,
    }
    if sources:
        result.update(division_weights=weights, reports=reports)
    return result


def compile_fund_histories(meta, calendar, profiles, sessions, financial, variant):
    """Bind reviewed report profiles to their already-audited availability boundaries."""
    if variant not in ("coverage", "identity", "reported"):
        raise ValueError("Unknown fund data control")
    if any("fund_history" in instrument for instrument in meta.values()):
        raise ValueError("Parent already contains fund history")
    observations = {row["source"]: row for row in financial}
    if len(observations) != len(financial):
        raise ValueError("Duplicate fund financial source")
    compiled = []
    for index, original in enumerate(profiles):
        profile = deepcopy({k: v for k, v in original.items() if k != "named_stocks"})
        observed = observations[profile["source_report"]]
        if (
            observed["symbol"] != profile["symbol"]
            or observed["identity"]["period_end"] != profile["period_end"]
            or observed["source_sha256"] != profile["source_sha256"]
        ):
            raise ValueError("Fund financial and stock reports differ")
        contracts = []
        for table in observed["contracts"]["tables"]:
            for row in table["rows"]:
                if table["unit"]["currency"] != "CNY":
                    raise ValueError("Fund contract currency is not reviewed CNY")
                contracts.append({**deepcopy(row), "source_unit": deepcopy(table["unit"])})
        profile.update(
            mode=variant,
            profile_index=index,
            economic_exposure_upper_bound=None,
            reported_index_contracts=[r for r in contracts if r["kind"] == "stock_index_futures"],
            other_reported_contracts=[r for r in contracts if r["kind"] != "stock_index_futures"],
            reported_nominal=deepcopy(observed["nominal"]),
            security_lending=deepcopy(observed["security_lending"]),
            absence_clauses=deepcopy(observed["explicit_absence_clauses"]),
        )
        known, _ = reported_components(profile)
        nav = Fraction(profile["reported_nav_cny"])
        for name in (
            "matched_named",
            "unmatched_named",
            "undisclosed_stocks",
            "source_sector_stocks",
            "signed_equity_sector_residual",
        ):
            value = profile[name]
            fraction = value["nav_fraction"]
            if Fraction(value["amount_cny"]) / nav != Fraction(
                fraction["numerator"], fraction["denominator"]
            ):
                raise ValueError("Fund source NAV fraction does not reconcile")
        if sum(known.values()) != Fraction(profile["matched_named"]["amount_cny"]) / nav:
            raise ValueError("Fund matched division amounts do not reconcile")
        if sum(
            Fraction(profile[k]["amount_cny"])
            for k in ("matched_named", "unmatched_named", "undisclosed_stocks")
        ) != Fraction(profile["source_sector_stocks"]["amount_cny"]):
            raise ValueError("Fund stock partition does not reconcile")
        compiled.append(profile)
    by_symbol: dict[str, dict[str, int | None]] = {
        s: {} for s, item in meta.items() if item["kind"] == "etf"
    }
    days = [str(day.date()) for day in calendar]
    for row in sessions:
        symbol, day, index = row["symbol"], row["session"], row["profile"]
        if symbol not in by_symbol or day in by_symbol[symbol]:
            raise ValueError("Invalid or duplicate fund session")
        if index is not None:
            profile = compiled[index]
            if profile["symbol"] != symbol or profile["first_session"] > day:
                raise ValueError("Fund profile is unavailable at the session")
            if (
                row["report_age_days"]
                != (pd.Timestamp(day) - pd.Timestamp(profile["period_end"])).days
            ):
                raise ValueError("Fund report age differs from source period")
        by_symbol[symbol][day] = index
    result = deepcopy(meta)
    for symbol, history in by_symbol.items():
        if sorted(history) != days:
            raise ValueError("Fund session inventory does not cover the signal calendar")
        entries = []
        previous: int | None = -1
        for day in days:
            profile_index = history[day]
            if profile_index == previous:
                continue
            current_profile = compiled[profile_index] if profile_index is not None else None
            group = (
                None
                if current_profile is None
                else (
                    meta[symbol]["industry"]
                    if variant == "coverage"
                    else current_profile["identity"]["benchmark_id"]
                )
            )
            entries.append(
                {
                    "available_at": day + "T00:00:00+08:00",
                    "industry": group,
                    "profile": current_profile,
                }
            )
            previous = profile_index
        result[symbol]["fund_history"] = entries
    return result


def build_fund_snapshot(paths, protocol_path, variant):
    """Build a registered fund snapshot without changing the active configuration."""
    import joblib

    from .dataset import check_integrity, load_snapshot, validate_features
    from .preprocessing import build_dataset
    from .storage import load_gzip, now, read, save, sha

    def inside(name):
        path = (paths.root / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(paths.root):
            raise ValueError("Fund paths must stay inside the project")
        return path

    protocol_file = inside(protocol_path)
    protocol = read(protocol_file)
    if protocol.get("name") != "etf_integration" or variant not in (
        "coverage",
        "identity",
        "reported",
    ):
        raise ValueError("Expected a registered fund comparison")
    target = inside(protocol["variants"][variant]["snapshot_output"])
    parent = (paths.root / "data/processed").resolve()
    if (
        not parent.is_relative_to(paths.root)
        or not target.is_relative_to(parent)
        or target == parent
    ):
        raise ValueError("Fund snapshot must be a named directory under data/processed")
    if target.exists():
        raise FileExistsError("Fund snapshot output already exists")
    integrity = check_integrity(paths)
    manifest = read(paths.manifest)
    if manifest["files"].get(Path(protocol_path).as_posix()) != sha(protocol_file):
        raise ValueError("Fund protocol is not frozen")
    for name, expected in protocol["source_sha256"].items():
        if manifest["files"].get(name) != expected or sha(inside(name)) != expected:
            raise ValueError(f"Fund input changed: {name}")
    receipt_file = inside(protocol["checks_output"]) / "parent_rebuild.json"
    receipt = read(receipt_file)
    if (
        manifest["files"].get(receipt_file.relative_to(paths.root).as_posix()) != sha(receipt_file)
        or receipt["status"] != "PASS"
        or receipt["protocol_sha256"] != sha(protocol_file)
        or receipt["data_sha256"] != protocol["source_bundle_sha256"]
        or receipt["source_sha256"] != protocol["implementation_before"]
    ):
        raise ValueError("Fund parent rebuild receipt differs")
    source = inside(protocol["source_bundle"])
    if sha(source) != protocol["source_bundle_sha256"]:
        raise ValueError("Fund parent dataset changed")
    original = load_snapshot(paths, source, protocol["source_data_cutoff"])

    def reviewed(filename):
        matches = [name for name in protocol["source_sha256"] if name.endswith("/" + filename)]
        if len(matches) != 1:
            raise ValueError(f"Expected one registered fund source: {filename}")
        return load_gzip(inside(matches[0]))

    meta = compile_fund_histories(
        original["meta"],
        original["calendar"],
        reviewed("equity_profiles.json.gz"),
        reviewed("session_profiles.json.gz"),
        reviewed("financial_observations.json.gz"),
        variant,
    )
    sources = {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    active_configuration = sha(paths.root / "configs/project.toml")
    provenance = {
        "started_at": now(),
        "purpose": "etf_integration",
        "variant": variant,
        "protocol_sha256": sha(protocol_file),
        "source_sha256": sources,
        "source_bundle_sha256": sha(source),
        "source_inputs": protocol["source_sha256"],
        "parent_rebuild_sha256": sha(receipt_file),
        "complete_economic_exposure_certified": False,
        "historical_availability_certified": False,
    }
    save(target / "provenance.json", provenance, immutable=True)
    rebuilt = build_dataset(
        meta, original["raws"], original["actions"], opening_status=original.get("opening_status")
    )
    rebuilt["fund_mode"] = variant
    rebuilt["provenance"] = provenance
    pd.testing.assert_index_equal(original["calendar"], rebuilt["calendar"], exact=True)
    if (
        original["features"] != rebuilt["features"]
        or original["actions"] != rebuilt["actions"]
        or original.get("opening_status") != rebuilt.get("opening_status")
    ):
        raise ValueError("Fund integration changed feature names, actions or opening status")
    for symbol, instrument in meta.items():
        if {k: v for k, v in instrument.items() if k != "fund_history"} != original["meta"][symbol]:
            raise ValueError("Fund integration changed original metadata")
        pd.testing.assert_frame_equal(
            original["raws"][symbol], rebuilt["raws"][symbol], check_exact=True
        )
    for section in ("panels", "scores"):
        for name, frame in original[section].items():
            pd.testing.assert_frame_equal(frame, rebuilt[section][name], check_exact=True)
    fixed = (
        list(original["table"])
        if variant == "coverage"
        else [c for c in original["table"] if c != "industry" and not c.startswith("industry_")]
    )
    # Any loss of parent ETF sessions requires a new protocol.
    pd.testing.assert_frame_equal(
        original["table"][fixed], rebuilt["table"][fixed], check_exact=True
    )
    features = validate_features(rebuilt)
    differences = {
        name: int(
            (
                ~(
                    original["table"][name].eq(rebuilt["table"][name])
                    | (original["table"][name].isna() & rebuilt["table"][name].isna())
                )
            ).sum()
        )
        for name in original["table"]
    }
    save(target / "field_changes.json", differences, immutable=True)
    joblib.dump(rebuilt, target / "bundle.joblib", compress=3)
    if sources != {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}:
        raise ValueError("Implementation changed during fund construction")
    if active_configuration != sha(paths.root / "configs/project.toml"):
        raise ValueError("Active configuration changed during fund construction")
    audit = {
        "status": "PASS",
        "completed_at": now(),
        "variant": variant,
        "source_integrity": integrity,
        "bundle_sha256": sha(target / "bundle.joblib"),
        "source_sha256": sources,
        "features": features,
        "exact_parent_table_columns": fixed,
        "raw_actions_calendar_panels_scores_exact": True,
        "removed_eligible_rows": 0,
        "independent_source_audit": "REQUIRED_SEPARATELY",
        "activated": False,
    }
    save(target / "audit.json", audit, immutable=True)
    for file in sorted(target.iterdir()):
        manifest["files"][file.relative_to(paths.root).as_posix()] = sha(file)
    save(paths.manifest, manifest)
    return audit
