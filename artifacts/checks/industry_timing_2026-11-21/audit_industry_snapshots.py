"""Independent all-row source joins, information clocks and industry-rank reconstruction."""

from collections import defaultdict
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from quantfolio.dataset import load_snapshot
from quantfolio.features import INDUSTRY_FEATURES, industry_features
from quantfolio.industries import metadata_at
from quantfolio.settings import Paths
from quantfolio.storage import dump_gzip, load_gzip, now, read, save, sha

root = Path.cwd()
paths = Paths(root)
protocol_file = root / "configs/protocols/industry_timing.json"
protocol = read(protocol_file)
checks = root / protocol["checks_output"]
parent = load_snapshot(paths, root / protocol["source_bundle"], protocol["source_data_cutoff"])
sources = {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
stocks = {s for s, instrument in parent["meta"].items() if instrument["kind"] == "stock"}
code_index = defaultdict(set)
for symbol in stocks:
    code_index[symbol[2:]].add(symbol)
for record in read(root / protocol["identity_links"])["records"]:
    if record["current_symbol"] in stocks:
        code_index[record["old_symbol"][2:]].add(record["current_symbol"])
assert all(len(symbols) == 1 for symbols in code_index.values())
review = read(root / protocol["source_review"])
releases = {}
source_join = []
for item in review["sources"]:
    if item["aliases"]:
        continue
    data = load_gzip(root / item["output"])
    mapping = {}
    for row in data["records"]:
        for symbol in code_index.get(row["code"], set()):
            assert symbol not in mapping, (item["catalogue_date"], symbol)
            mapping[symbol] = "capco:" + row["section"] + row["division"]
            source_join.append((item["catalogue_date"], symbol, row["code"], row["section"], row["division"], row["page"], row["row"]))
    releases[item["catalogue_date"]] = mapping
prior_join = load_gzip(root / "data/processed/industry_source_cells_2026-11-21/universe_join.json.gz")
assert sorted(source_join) == sorted((r["catalogue_date"], r["symbol"], r["source_code"], r["section"], r["division"], r["page"], r["row"]) for r in prior_join)
latest = releases[max(releases)]
calendar = parent["calendar"]
symbols = parent["table"].symbol.to_numpy()
date_rows = parent["table"].groupby("date_index", sort=True).indices
expected_groups, expected_known = {}, np.ones(len(parent["table"]), dtype=bool)
for index, indices in date_rows.items():
    day = str(calendar[index].date())
    earlier = [date for date in releases if date < day]
    mapping = releases[max(earlier)] if earlier else {}
    expected_groups[index] = mapping
    for row in indices:
        symbol = symbols[row]
        if symbol in stocks:
            expected_known[row] = symbol in mapping
expected_eligible = parent["table"].eligible.to_numpy() & expected_known
removed = parent["table"].eligible.to_numpy() & ~expected_known
assert int(removed.sum()) == 245


def independent_ranks(table):
    """Assign tie ranks by explicit sort positions, without pandas groupby.rank."""
    original = table[INDUSTRY_FEATURES].to_numpy()
    assert not np.isinf(original).any()
    eligible = table.eligible.to_numpy()
    expected = np.full(original.shape, np.nan, dtype=np.float32)
    groups = table.groupby(["date_index", "industry"], sort=False, dropna=True).indices
    for indices in groups.values():
        included = indices[eligible[indices]]
        for column in range(len(INDUSTRY_FEATURES)):
            valid = included[np.isfinite(original[included, column])]
            count = len(valid)
            if count < 3:
                expected[included, column] = 0
                continue
            values = original[valid, column]
            order = np.argsort(values, kind="stable")
            boundaries = np.r_[0, np.flatnonzero(np.diff(values[order]) != 0) + 1, count]
            ranks = np.empty(count, dtype=np.float64)
            ranks[order] = np.repeat((boundaries[:-1] + boundaries[1:] + 1) / 2, np.diff(boundaries)) / count
            expected[valid, column] = ((ranks - (count + 1) / (2 * count)) * count / (count + 5)).astype(np.float32)
    actual = table[["industry_" + name for name in INDUSTRY_FEATURES]].to_numpy()
    if not np.array_equal(actual, expected, equal_nan=True):
        bad = np.argwhere(~((actual == expected) | (np.isnan(actual) & np.isnan(expected))))
        raise AssertionError(("Independent industry ranks differ", len(bad), bad[:10].tolist()))
    return {"rows": len(table), "columns": len(INDUSTRY_FEATURES), "exact_cells": int(expected.size)}


snapshots, results = {}, {}
for variant, setting in protocol["variants"].items():
    file = root / setting["snapshot_output"] / "bundle.joblib"
    bundle = load_snapshot(paths, file, protocol["source_data_cutoff"])
    snapshots[variant] = bundle
    table = bundle["table"]
    assert list(table) == list(parent["table"]) and bundle["features"] == parent["features"]
    np.testing.assert_array_equal(table.eligible.to_numpy(), expected_eligible)
    expected = np.empty(len(table), dtype=object)
    for index, indices in date_rows.items():
        mapping = expected_groups[index]
        dated = {}
        for symbol, instrument in parent["meta"].items():
            group = instrument["industry"]
            if symbol in stocks:
                group = None if symbol not in mapping else (
                    instrument["industry"] if variant == "coverage" else latest[symbol] if variant == "static" else mapping[symbol]
                )
            dated[symbol] = group
        expected[indices] = [dated[symbols[row]] for row in indices]
        for hour in ("09:25:00", "16:00:00"):
            actual = metadata_at(bundle["meta"], str(calendar[index].date()) + "T" + hour + "+08:00")
            assert {s: m["industry"] for s, m in actual.items()} == dated
    pd.testing.assert_series_equal(table.industry, pd.Series(expected, index=table.index, name="industry"), check_exact=True)
    for symbol, instrument in bundle["meta"].items():
        assert {key: value for key, value in instrument.items() if key != "industry_history"} == parent["meta"][symbol]
        if symbol not in stocks:
            assert "industry_history" not in instrument
        pd.testing.assert_frame_equal(bundle["raws"][symbol], parent["raws"][symbol], check_exact=True)
    assert bundle["actions"] == parent["actions"]
    assert bundle["opening_status"] == parent["opening_status"]
    rank_check = independent_ranks(table)
    results[variant] = {"bundle_sha256": sha(file), "rows": len(table), "stocks": len(stocks),
                        "eligible_removed": int(removed.sum()), "dated_groups_exact": len(table),
                        "opening_and_close_groups_checked": len(table) * 2, "independent_ranks": rank_check}
    print(variant, results[variant], flush=True)

coverage = snapshots["coverage"]
for variant in ("static", "dated"):
    other = snapshots[variant]
    for section in ("panels", "scores"):
        assert coverage[section].keys() == other[section].keys()
        for name in coverage[section]:
            pd.testing.assert_frame_equal(coverage[section][name], other[section][name], check_exact=True)
    shared = [name for name in coverage["table"] if name != "industry" and not name.startswith("industry_")]
    pd.testing.assert_frame_equal(coverage["table"][shared], other["table"][shared], check_exact=True)

dated = snapshots["dated"]
prefixes = []
for cutoff in (300, 500, 650):
    changed = deepcopy(dated["meta"])
    deadline = calendar[cutoff].tz_localize("Asia/Shanghai") + pd.Timedelta(hours=16)
    edited = 0
    for instrument in changed.values():
        for record in instrument.get("industry_history", []):
            if pd.Timestamp(record["available_at"]) > deadline:
                record["industry"] = "future_perturbation"
                edited += 1
    prefix = dated["table"].loc[dated["table"].date_index <= cutoff]
    features, groups = industry_features(prefix, changed, dated["calendar"])
    pd.testing.assert_frame_equal(prefix[features.columns], features, check_exact=True)
    pd.testing.assert_series_equal(prefix.industry, groups, check_names=False, check_exact=True)
    prefixes.append({"cutoff_index": cutoff, "future_records_perturbed": edited, "rows_exact": len(prefix)})
unknown = [{"date": str(calendar[int(parent["table"].iloc[index].date_index)].date()), "symbol": str(symbols[index])} for index in np.flatnonzero(removed)]
save(checks / "removed_eligible_rows.json", unknown, immutable=True)
static_groups = snapshots["static"]["table"].industry
dated_groups = dated["table"].industry
different = ~(static_groups.eq(dated_groups) | (static_groups.isna() & dated_groups.isna()))
changes = [{"date": str(calendar[int(dated["table"].iloc[index].date_index)].date()), "symbol": str(symbols[index]), "static": static_groups.iloc[index], "dated": dated_groups.iloc[index]} for index in np.flatnonzero(different.to_numpy())]
dump_gzip(checks / "dated_static_groups.json.gz", changes)
assert sources == {f.name: sha(f) for f in sorted((root / "src/quantfolio").glob("*.py"))}
save(checks / "snapshot_review.json", {
    "status": "PASS", "checked_at": now(), "protocol_sha256": sha(protocol_file), "source_sha256": sources,
    "parent_sha256": sha(root / protocol["source_bundle"]), "source_join_records": len(source_join),
    "variants": results, "identical_non_industry_inputs_between_controls": True,
    "dated_future_perturbation_checks": prefixes, "dated_static_group_differences": len(changes),
    "removed_rows_sha256": sha(checks / "removed_eligible_rows.json"),
    "group_differences_sha256": sha(checks / "dated_static_groups.json.gz"),
    "historical_availability_certified": False, "activated": False,
    "scope": "All stored rows, independent extracted-source joins, both information clocks, explicit-sort industry ranks and exact shared controls. Catalogue-date availability and original ETF labels remain retrospective assumptions; current-universe bias is unchanged."
}, immutable=True)
print("ALL THREE INDUSTRY SNAPSHOTS INDEPENDENTLY CHECKED", flush=True)
