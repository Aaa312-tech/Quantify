"""Exercise real paper fills, carried account state and failure of stale or altered inputs."""

from copy import deepcopy

import pytest

from quantfolio import accounts, planning
from quantfolio.accounts import audit_fills, new_account, restore_account
from quantfolio.planning import (
    close_account,
    freeze_plan,
    make_intent,
    next_session,
    resolve_intent,
    resolve_plan,
    validate_parent,
)
from quantfolio.settings import Paths
from quantfolio.simulation import simulate
from quantfolio.storage import read, save, sha
from tests.factories import bundle_prefix, market_bundle, opening_quotes, trading_calendar


@pytest.mark.parametrize("cost", [0.001, 0.002, 0.003])
def test_multiday_paper_targets_fills_and_risk_match_the_simulator(cost):
    bundle, forecasts = market_bundle()
    forecasts[5] = dict(list(forecasts[5].items())[:2])
    for day in range(10, 30):
        for value in forecasts[day].values():
            value["mu"] = -0.1
    _, expected = simulate(bundle, 6, 20, forecasts, slippage=cost)
    account = new_account("2026-01-12", "synthetic")
    trades = []
    for index in range(6, 21):
        day = str(bundle["calendar"][index].date())
        intent = make_intent(
            bundle_prefix(bundle, index - 1), account, forecasts[index - 1], "baseline", day, cost
        )
        result = resolve_intent(
            intent, account, [], trading_calendar(bundle), opening_quotes(intent)
        )
        trades.extend(result["trades"])
        reference = expected["decisions"][index - 6]
        assert intent["target_weights"] == reference["target_weights"]
        assert intent["state"] == reference["recovery"]
        account = result["opening_account"]
        assert account["risk"]["observations"] == index - 5
        assert result["audit"]["status"] == "PASS"
    assert trades == expected["trades"]
    assert account["cash"] == expected["curve"][-1]["cash"]
    assert not account["lots"] and any(t["side"] == "SELL" for t in trades)


def test_missing_quotes_limits_and_later_fields_cannot_create_fills():
    bundle, forecasts = market_bundle()
    account = new_account("2026-01-12", "synthetic")
    intent = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", "2026-01-13", 0.001
    )
    quotes = opening_quotes(intent)
    expected = resolve_intent(intent, account, [], trading_calendar(bundle), quotes)
    for q in quotes.values():
        q.update(high=0.0, low=1000.0, close=999.0, volume=0)
    assert (
        resolve_intent(intent, account, [], trading_calendar(bundle), quotes)["trades"]
        == expected["trades"]
    )
    symbols = list(quotes)
    quotes[symbols[0]]["open"] = 11
    quotes[symbols[1]]["suspended"] = True
    quotes.pop(symbols[2])
    result = resolve_intent(intent, account, [], trading_calendar(bundle), quotes)
    assert not {t["symbol"] for t in result["trades"]} & set(symbols[:3])
    assert {r["reason"] for r in result["rejections"]} >= {
        "OPEN_AT_UPPER_LIMIT",
        "SUSPENDED_AT_OPEN",
        "NO_QUOTE",
    }
    quotes = opening_quotes(intent)
    quotes[symbols[0]]["market_at"] = "2026-01-13T15:00:00+08:00"
    with pytest.raises(ValueError, match="auction timestamp"):
        resolve_intent(intent, account, [], trading_calendar(bundle), quotes)


def test_independent_paper_audit_detects_changed_fees_and_balances():
    bundle, forecasts = market_bundle()
    account = new_account("2026-01-12", "synthetic")
    intent = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", "2026-01-13", 0.001
    )
    result = resolve_intent(intent, account, [], trading_calendar(bundle), opening_quotes(intent))
    result["trades"][0]["fee"] += 0.01
    with pytest.raises(ValueError, match="cost reconciliation"):
        audit_fills(account, result, bundle["meta"], "2026-01-13", 0.001, 0.9)


def test_halt_and_restricted_shares_survive_manual_input():
    bundle, forecasts = market_bundle()
    account = new_account("2026-01-05", "manual")
    account.update(
        kind="manual",
        as_of="2026-01-12",
        cash=800000,
        sessions_completed=3,
        next_lot_id=8,
        lots=[
            {
                "id": 7,
                "symbol": "sh600001",
                "quantity": 20000,
                "bought": "2026-01-06",
                "available": None,
                "stop_cost": 10,
            }
        ],
    )
    account["risk"].update(phase="HALTED0", peak=1100000, observations=3, age=1)
    intent = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", "2026-01-13", 0.001
    )
    assert intent["state"]["phase"] == "HALTED0" and intent["state"]["peak"] == 1100000
    result = resolve_intent(intent, account, [], trading_calendar(bundle), opening_quotes(intent))
    assert not result["trades"] and result["opening_account"]["lots"] == account["lots"]
    assert result["opening_account"]["risk"]["phase"] == "HALTED0"
    assert result["rejections"][0]["reason"] == "T1_OR_MINIMUM_QUANTITY"


def test_lot_identifiers_remain_unique_after_all_active_lots_are_sold():
    bundle, _ = market_bundle()
    account = new_account("2026-01-05", "manual")
    account.update(
        kind="manual",
        as_of="2026-01-12",
        sessions_completed=2,
        next_lot_id=23,
        entitlements={"dividend": [[22, 100, 10, "2026-01-06"]]},
    )
    account["risk"]["observations"] = 2
    book, _ = restore_account(account, bundle["meta"])
    assert book.next_id == 23
    account["next_lot_id"] = 1
    with pytest.raises(ValueError, match="reuse"):
        restore_account(account, bundle["meta"])


def test_fresh_cash_account_cannot_claim_historical_dividend_rights():
    bundle, _ = market_bundle()
    account = new_account("2026-01-12", "synthetic")
    account.update(next_lot_id=2, entitlements={"unearned_dividend": [[1, 100, 10, "2026-01-12"]]})
    with pytest.raises(ValueError, match="fresh account"):
        restore_account(account, bundle["meta"])


def test_corporate_rights_and_cash_dates_cannot_be_invented():
    bundle, forecasts = market_bundle()
    account = new_account("2026-01-05", "manual")
    account.update(
        kind="manual",
        as_of="2026-01-12",
        cash=900000,
        sessions_completed=2,
        next_lot_id=2,
        lots=[
            {
                "id": 1,
                "symbol": "sh600001",
                "quantity": 10000,
                "bought": "2026-01-06",
                "available": "2026-01-07",
                "stop_cost": 10,
            }
        ],
    )
    account["risk"]["observations"] = 2
    action = {
        "id": "dividend",
        "symbol": "sh600001",
        "record_date": "2026-01-09",
        "ex_date": "2026-01-13",
        "cash_per_share": 0.1,
        "share_multiplier": 1,
        "cash_pay_date": "2026-01-13",
        "cash_available_date": None,
        "share_list_date": None,
    }
    bundle["actions"] = [action]
    intent = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", "2026-01-13", 0.001
    )
    with pytest.raises(ValueError, match="Missing historical record"):
        resolve_intent(intent, account, [action], trading_calendar(bundle), opening_quotes(intent))
    account["entitlements"] = {"dividend": [[1, 10000, 10, "2026-01-06"]]}
    result = resolve_intent(
        intent, account, [action], trading_calendar(bundle), opening_quotes(intent)
    )
    assert result["opening_account"]["cash"] == 900000
    assert result["opening_account"]["receivables"][0]["cents"] == 80000
    assert result["opening_account"]["receivables"][0]["available_date"] == "2026-01-14"
    unresolved = {**action, "cash_pay_date": None}
    with pytest.raises(ValueError, match="remains unresolved"):
        resolve_intent(
            intent, account, [unresolved], trading_calendar(bundle), opening_quotes(intent)
        )


def paper_project(tmp_path, monkeypatch):
    bundle, forecasts = market_bundle()
    paths = Paths(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/project.toml").write_text(
        'data_cutoff="2026-01-12"\nbundle="data.bin"\ncalendar="configs/calendar.json"\nmanifest="configs/integrity_manifest.json"\n',
        encoding="utf-8",
    )
    (tmp_path / "data.bin").write_bytes(b"synthetic fixture; not a pickled market snapshot")
    save(tmp_path / "configs/calendar.json", trading_calendar(bundle))
    save(tmp_path / "configs/competition.json", {"initial_cash": 1000000})
    save(tmp_path / "fit.json", {"fixture": "synthetic forecasts"})
    save(
        tmp_path / "configs/integrity_manifest.json",
        {"files": {"configs/calendar.json": sha(tmp_path / "configs/calendar.json")}},
    )
    clock = ["2026-01-12T17:00:00+08:00"]
    monkeypatch.setattr(planning, "now", lambda: clock[0])
    monkeypatch.setattr(accounts, "now", lambda: clock[0])
    monkeypatch.setattr(planning, "load_bundle", lambda p: bundle_prefix(bundle, 5))
    monkeypatch.setattr(
        planning,
        "latest_forecasts",
        lambda p, b: (
            forecasts[5],
            {"selected_candidate": "momentum", "selected_rules": "baseline"},
            [tmp_path / "fit.json"],
        ),
    )
    save(tmp_path / "account.json", new_account("2026-01-12", "synthetic"))
    return paths, bundle, forecasts, clock


def test_frozen_plan_quote_resolution_and_closing_account_round_trip(tmp_path, monkeypatch):
    paths, bundle, forecasts, clock = paper_project(tmp_path, monkeypatch)
    result = freeze_plan(paths, "account.json", "artifacts/plans/january_open")
    assert result["status"] == "FROZEN_CONDITIONAL_PLAN"
    plan = read(result["plan"])
    assert plan["orders"] and all(type(t["quantity"]) is int for t in plan["orders"])
    clock[0] = "2026-01-13T09:26:00+08:00"
    save(
        tmp_path / "opening.json",
        {
            "session": "2026-01-13",
            "source": "synthetic fixture",
            "recorded_at": "2026-01-13T09:25:30+08:00",
            "quotes": opening_quotes(plan["intent"]),
        },
    )
    resolved = resolve_plan(paths, result["plan"], "opening.json", "artifacts/plans/january_quotes")
    assert resolved["use"] == "OPENING_PAPER_REVIEW"
    detail = read(resolved["resolution"])
    assert detail["trades"] == plan["orders"]
    clock[0] = "2026-01-13T16:01:00+08:00"
    save(
        tmp_path / "closing.json",
        {
            "session": "2026-01-13",
            "source": "synthetic fixture",
            "recorded_at": "2026-01-13T15:01:00+08:00",
            "quotes": {
                s: {"close": 10.2, "market_at": "2026-01-13T15:00:00+08:00"}
                for s in plan["intent"]["contracts"]
            },
        },
    )
    closed = close_account(
        paths, resolved["resolution"], "closing.json", "artifacts/accounts/january_close"
    )
    account = read(closed["account"])
    validate_parent(paths, account)
    assert (
        account["sessions_completed"] == 1 and account["risk"] == detail["opening_account"]["risk"]
    )
    assert account["valuation"]["nav"] > 1e6
    changed = deepcopy(account)
    changed["cash"] += 100
    with pytest.raises(ValueError, match="differs from its recorded parent"):
        validate_parent(paths, changed)
    paths.config["data_cutoff"] = "2026-01-13"
    monkeypatch.setattr(planning, "load_bundle", lambda p: bundle_prefix(bundle, 6))
    monkeypatch.setattr(
        planning,
        "latest_forecasts",
        lambda p, b: (
            forecasts[6],
            {"selected_candidate": "momentum", "selected_rules": "baseline"},
            [tmp_path / "fit.json"],
        ),
    )
    stale = freeze_plan(paths, closed["account"], "artifacts/plans/inconsistent_close")
    assert stale["status"] == "BLOCKED"
    assert "closing marks differ" in stale["blockers"][0]
    for symbol in account["valuation"]["quotes"]:
        bundle["raws"][symbol].at[bundle["calendar"][6], "close"] = 10.2
    second = freeze_plan(paths, closed["account"], "artifacts/plans/second_session")
    next_plan = read(second["plan"])
    assert second["status"] == "FROZEN_CONDITIONAL_PLAN"
    assert not next_plan["intent"]["scheduled"]
    assert next_plan["intent"]["state"]["peak"] == account["valuation"]["nav"]


def test_plan_rejects_changes_to_its_independent_industry_auditor(tmp_path, monkeypatch):
    paths, _, _, _ = paper_project(tmp_path, monkeypatch)
    result = freeze_plan(paths, "account.json", "artifacts/plans/bound_auditor")
    original_sha = planning.sha
    monkeypatch.setattr(
        planning,
        "sha",
        lambda path: "changed" if path.name == "validation.py" else original_sha(path),
    )
    with pytest.raises(ValueError, match="calculation source changed"):
        resolve_plan(paths, result["plan"], "missing.json", "artifacts/plans/stale_auditor")
    assert not (tmp_path / "artifacts/plans/stale_auditor").exists()


@pytest.mark.parametrize("omit_quote", [False, True])
def test_missing_opening_quote_preserves_the_backtest_rejection(omit_quote):
    bundle, forecasts = market_bundle()
    dates, symbol = bundle["calendar"], "sh600001"
    bundle["raws"][symbol] = bundle["raws"][symbol].drop(index=dates[6])
    _, expected = simulate(bundle, 6, 6, forecasts)
    account = new_account(str(dates[5].date()), "historical_diagnostic")
    intent = make_intent(
        bundle_prefix(bundle, 5), account, forecasts[5], "baseline", str(dates[6].date()), 0.001
    )
    quotes = opening_quotes(intent)
    if omit_quote:
        del quotes[symbol]
    else:
        quotes[symbol]["open"] = None
    actual = resolve_intent(intent, account, [], trading_calendar(bundle), quotes)
    assert any(row["reason"] == "NO_QUOTE" for row in expected["rejections"])
    assert actual["rejections"] == expected["rejections"]
    assert actual["trades"] == expected["trades"]


def test_expired_plan_does_not_emit_instructions_or_resolve(tmp_path, monkeypatch):
    paths, _, _, clock = paper_project(tmp_path, monkeypatch)
    clock[0] = "2026-01-13T09:25:00+08:00"
    result = freeze_plan(paths, "account.json", "artifacts/plans/expired")
    plan = read(result["plan"])
    assert result["status"] == "BLOCKED" and plan["orders"] == []
    assert "NEXT_OPEN_ALREADY_OCCURRED_OR_DATA_STALE" in result["blockers"]
    with pytest.raises(ValueError, match="frozen before its opening"):
        resolve_plan(paths, result["plan"], "missing.json", "artifacts/plans/invalid")
    assert not (tmp_path / "artifacts/plans/invalid").exists()


@pytest.mark.parametrize("fault", [None, "changed_hash", "wrong_snapshot", "inconsistent_decision"])
def test_fresh_data_does_not_clear_a_known_historical_eligibility_defect(
    tmp_path, monkeypatch, fault
):
    paths, _, _, _ = paper_project(tmp_path, monkeypatch)
    relative = "artifacts/checks/status_review/impact.json"
    paths.config["historical_status_review"] = relative
    review = {
        "source_bundle_sha256": sha(paths.bundle),
        "status": "KNOWN_HISTORICAL_ST_ELIGIBILITY_DEFECT",
        "blocking": True,
    }
    if fault == "wrong_snapshot":
        review["source_bundle_sha256"] = "0" * 64
    if fault == "inconsistent_decision":
        review["blocking"] = False
    save(tmp_path / relative, review)
    manifest = read(paths.manifest)
    manifest["files"][relative] = sha(tmp_path / relative)
    save(paths.manifest, manifest)
    if fault == "changed_hash":
        save(tmp_path / relative, {**review, "blocking": False})
    if fault:
        with pytest.raises(ValueError):
            freeze_plan(paths, "account.json", "artifacts/plans/rejected")
        assert not (tmp_path / "artifacts/plans/rejected").exists()
    else:
        result = freeze_plan(paths, "account.json", "artifacts/plans/eligibility_hold")
        plan = read(result["plan"])
        assert result["blockers"] == ["HISTORICAL_ST_ELIGIBILITY_REPAIR_REQUIRED"]
        assert result["status"] == "BLOCKED" and plan["orders"] == []
        assert "intent" not in plan
        assert plan["input_sha256"][relative] == sha(tmp_path / relative)
        assert plan["historical_status_review"]["blocking"]


def test_late_quote_review_is_labelled_retrospective_and_changed_inputs_fail(tmp_path, monkeypatch):
    paths, _, _, clock = paper_project(tmp_path, monkeypatch)
    result = freeze_plan(paths, "account.json", "artifacts/plans/early")
    plan = read(result["plan"])
    clock[0] = "2026-01-13T16:00:00+08:00"
    save(
        tmp_path / "quotes.json",
        {
            "session": "2026-01-13",
            "source": "synthetic fixture",
            "recorded_at": clock[0],
            "quotes": opening_quotes(plan["intent"]),
        },
    )
    resolved = resolve_plan(paths, result["plan"], "quotes.json", "artifacts/plans/late_review")
    assert resolved["use"] == "RETROSPECTIVE_PAPER_REPLAY"
    save(tmp_path / "fit.json", {"fixture": "altered forecast identity"})
    with pytest.raises(ValueError, match="Plan input changed"):
        resolve_plan(paths, result["plan"], "quotes.json", "artifacts/plans/changed")


def test_calendar_crosses_weekends_but_never_extrapolates_uncovered_dates():
    calendar = {"sessions": ["2026-11-20", "2026-11-23"]}
    assert next_session(calendar, "2026-11-20") == "2026-11-23"
    with pytest.raises(ValueError, match="not a reviewed"):
        next_session(calendar, "2026-11-21")
    with pytest.raises(ValueError, match="does not cover"):
        next_session(calendar, "2026-11-23")


def test_receivable_for_sold_security_survives_another_closing_snapshot(tmp_path, monkeypatch):
    paths, _, _, clock = paper_project(tmp_path, monkeypatch)
    account = read(tmp_path / "account.json")
    account.update(kind="manual", initial_date="2026-01-05", cash=999000, sessions_completed=1)
    account["risk"]["observations"] = 1
    account["receivables"] = [
        {
            "symbol": "sh510300",
            "cents": 100000,
            "pay_date": "2026-01-15",
            "available_date": "2026-01-16",
            "event_id": "manual_receivable",
        }
    ]
    save(tmp_path / "account.json", account)
    # Make this a scheduled rebalance to exercise current symbols alongside the old receivable.
    account["sessions_completed"] = account["risk"]["observations"] = 5
    save(tmp_path / "account.json", account)
    planned = freeze_plan(paths, "account.json", "artifacts/plans/receivable")
    plan = read(planned["plan"])
    assert "sh510300" not in plan["intent"]["contracts"]
    clock[0] = "2026-01-13T09:26:00+08:00"
    save(
        tmp_path / "opening.json",
        {
            "session": "2026-01-13",
            "source": "manual test",
            "recorded_at": clock[0],
            "quotes": opening_quotes(plan["intent"]),
        },
    )
    resolved = resolve_plan(
        paths, planned["plan"], "opening.json", "artifacts/plans/receivable_open"
    )
    clock[0] = "2026-01-13T16:01:00+08:00"
    save(
        tmp_path / "closing.json",
        {
            "session": "2026-01-13",
            "source": "manual test",
            "recorded_at": clock[0],
            "quotes": {
                s: {"close": 10.0, "market_at": "2026-01-13T15:00:00+08:00"}
                for s in plan["intent"]["contracts"]
            },
        },
    )
    closed = close_account(
        paths, resolved["resolution"], "closing.json", "artifacts/accounts/receivable_close"
    )
    carried = read(closed["account"])
    assert carried["receivables"] == account["receivables"]
    validate_parent(paths, carried)
