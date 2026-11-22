"""Opening gaps must not inherit numerical noise from the cumulative signal index."""

from fractions import Fraction

import joblib
import numpy as np
import pandas as pd
import pytest

from quantfolio.features import cross_ranks, price_features
from quantfolio.ledger import causal_price_index


def raw_prices(periods=180):
    dates = pd.bdate_range("2025-01-02", periods=periods)
    close = np.resize([10.3, 9.7, 10.1, 11.13, 9.94, 10.61, 10.07], periods)
    return pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume_lots": 10000.0,
            "amount": 1e8,
            "turnover": 1.0,
        },
        index=dates,
    )


def action(date, cash=0.0, multiplier=1.0):
    return {"ex_date": str(date.date()), "cash_per_share": cash, "share_multiplier": multiplier}


def gap(raw, actions=()):
    adjusted, _ = causal_price_index(raw, actions)
    return price_features(raw, adjusted, adjusted.close.pct_change(fill_method=None))["gap"]


def rational_gap(previous_close, current, events):
    quantity, cash = Fraction(1), Fraction(0)
    for event in events:
        cash += quantity * Fraction(str(event["cash_per_share"]))
        quantity *= Fraction(str(event["share_multiplier"]))
    close = Fraction(str(current.close))
    close_wealth = quantity * close + cash
    close_index_ratio = close_wealth / Fraction(str(previous_close))
    return float(close_index_ratio * Fraction(str(current.open)) / close - 1)


def test_unchanged_open_is_exactly_zero_after_cumulative_price_changes():
    raw = raw_prices()
    values = gap(raw)
    assert pd.isna(values.iloc[0])
    np.testing.assert_array_equal(values.iloc[1:], np.zeros(len(raw) - 1))


def test_past_entitlement_cannot_change_later_gap_values_or_tied_ranks():
    raw = raw_prices()
    before = gap(raw, [action(raw.index[20], 0.4)])
    after = gap(raw, [action(raw.index[20], 1.4)])
    assert before.iloc[20] != after.iloc[20]
    pd.testing.assert_series_equal(before.iloc[21:], after.iloc[21:], check_exact=True)
    table = pd.DataFrame(
        {
            "date_index": np.repeat(np.arange(21, len(raw)), 2),
            "eligible": True,
            "gap": np.column_stack((after.iloc[21:], np.zeros(len(raw) - 21))).ravel(),
        }
    )
    ranks = cross_ranks(table, ["gap"])
    np.testing.assert_array_equal(ranks.rank_gap, np.full(len(table), 0.25))


@pytest.mark.parametrize("cash,multiplier", [(0.0, 1.0), (0.4, 1.0), (0.0, 1.5), (1.4, 1.5)])
def test_cash_and_splits_match_an_independent_rational_reference(cash, multiplier):
    raw = raw_prices()
    index = 50
    event = action(raw.index[index], cash, multiplier)
    raw.loc[raw.index[index], "open"] = 10.26
    result = gap(raw, [event])
    expected = rational_gap(raw.close.iloc[index - 1], raw.iloc[index], [event])
    assert abs(result.iloc[index] - expected) <= 16 * np.finfo(float).eps * max(1, abs(expected))
    # The event's completed-day feature retains the same economic definition.
    changed = raw.copy()
    changed.loc[raw.index[index], "close"] *= 1.1
    if cash:
        assert gap(changed, [event]).iloc[index] != result.iloc[index]


def test_multiple_actions_between_quotes_use_then_current_share_entitlements():
    raw = raw_prices()
    dates = raw.index
    events = [action(dates[42], 0.75, 1.5), action(dates[40], 0.5, 2.0)]
    raw = raw.drop(dates[40:44])
    result = gap(raw, events)
    expected = rational_gap(raw.loc[dates[39], "close"], raw.loc[dates[44]], events[::-1])
    assert abs(result.loc[dates[44]] - expected) <= 16 * np.finfo(float).eps * max(1, abs(expected))
    assert result.index.equals(raw.index)
    assert not result.index.isin(dates[40:44]).any()
    outside = [
        action(dates[0] - pd.Timedelta(days=1), 1.0, 2.0),
        action(dates[-1] + pd.Timedelta(days=1), 1.0),
    ]
    pd.testing.assert_series_equal(result, gap(raw, [*events, *outside]), check_exact=True)


def test_small_real_move_is_not_rounded_to_zero_and_future_changes_preserve_prefix():
    raw = raw_prices()
    raw.loc[raw.index[50], "open"] = raw.close.iloc[49] * (1 + 1e-10)
    result = gap(raw)
    assert 0 < result.iloc[50] < 2e-10
    changed = raw.copy()
    changed.iloc[120:, changed.columns.get_indexer(["open", "high", "low", "close"])] *= 1.1
    after = gap(changed, [action(raw.index[125], 0.5)])
    pd.testing.assert_series_equal(result.iloc[:120], after.iloc[:120], check_exact=True)


@pytest.mark.parametrize("fault", ["label", "eligibility", "action", "gap_missing"])
def test_snapshot_rejects_changes_outside_registered_gap_values(tmp_path, monkeypatch, fault):
    from copy import deepcopy

    from quantfolio import preprocessing
    from quantfolio.settings import Paths
    from quantfolio.snapshots import repair_snapshot
    from quantfolio.storage import save, sha
    from tests.factories import raw_history

    meta, raws = raw_history()
    original = preprocessing.build_dataset(meta, raws, [])
    source = tmp_path / "data/processed/before/bundle.joblib"
    source.parent.mkdir(parents=True)
    joblib.dump(original, source)
    config = tmp_path / "configs"
    config.mkdir()
    (config / "project.toml").write_text(
        'manifest = "configs/manifest.json"\narchive = "configs/archive.toml"\n', encoding="utf-8"
    )
    (config / "archive.toml").write_text("", encoding="utf-8")
    protocol_file = config / "protocols/gap_precision.json"
    protocol = {
        "name": "gap_precision",
        "source_bundle": source.relative_to(tmp_path).as_posix(),
        "source_bundle_sha256": sha(source),
        "source_data_cutoff": original["cutoff"],
        "checks_output": "artifacts/checks/gap_test",
        "snapshot_output": "data/processed/after",
        "limitations": [],
    }
    save(protocol_file, protocol)
    parent = tmp_path / protocol["checks_output"] / "parent_rebuild.json"
    save(
        parent,
        {"status": "PASS", "data_sha256": sha(source), "protocol_sha256": sha(protocol_file)},
    )
    save(
        config / "manifest.json",
        {
            "files": {
                f.relative_to(tmp_path).as_posix(): sha(f) for f in (source, protocol_file, parent)
            }
        },
    )
    changed = deepcopy(original)
    if fault == "label":
        changed["table"].at[0, "y5"] = 0.1
    elif fault == "eligibility":
        changed["panels"]["eligible"].iloc[0, 0] = True
    elif fault == "action":
        changed["actions"].append({"symbol": "sh600001", "cash_per_share": 0.1})
    else:
        changed["table"].at[0, "gap"] = 0.0
    monkeypatch.setattr(preprocessing, "build_dataset", lambda *args, **kwargs: changed)
    with pytest.raises((AssertionError, ValueError)):
        repair_snapshot(
            Paths(tmp_path), protocol["snapshot_output"], protocol_file.relative_to(tmp_path)
        )
    assert sha(source) == protocol["source_bundle_sha256"]
    assert not (tmp_path / protocol["snapshot_output"]).exists()
