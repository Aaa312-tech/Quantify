"""Frozen next-session paper intentions, manual opening quotes and closing snapshots."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from .accounts import (
    audit_fills,
    date_only,
    new_account,
    number,
    restore_account,
    snapshot_account,
    timestamp,
)
from .allocation import allocate
from .dataset import check_integrity, eligible_rows, load_bundle
from .evaluation import experiment_folds
from .ledger import lot_rule, nearest_tick, tick_price
from .market import board_limit, regime
from .models import prediction_map
from .risk import RiskState, healthy_market
from .simulation import _Execution, buy_ceiling
from .status_data import load_status_review
from .storage import digest, now, read, save, sha

SOURCE_MODULES = (
    "accounts",
    "planning",
    "allocation",
    "ledger",
    "market",
    "risk",
    "rules",
    "simulation",
    "status_data",
)


def load_calendar(paths):
    """Use a reviewed finite exchange calendar; never assume future weekdays are sessions."""
    path = (paths.root / paths.config["calendar"]).resolve()
    if not path.is_relative_to(paths.root):
        raise ValueError("Trading calendar escapes the project")
    expected = read(paths.manifest)["files"].get(path.relative_to(paths.root).as_posix())
    if sha(path) != expected:
        raise ValueError("Trading calendar is not frozen or its hash changed")
    calendar = read(path)
    sessions = calendar["sessions"]
    if not sessions or sorted(set(sessions)) != sessions:
        raise ValueError("Trading sessions must be sorted and unique")
    if calendar["timezone"] != "Asia/Shanghai":
        raise ValueError("Unsupported trading timezone")
    for day in sessions:
        if (
            not calendar["coverage_start"] <= day <= calendar["coverage_end"]
            or date_only(day).weekday() >= 5
        ):
            raise ValueError("Invalid reviewed trading session")
    return calendar


def next_session(calendar, day):
    date_only(day)
    if day not in calendar["sessions"]:
        raise ValueError("Valuation date is not a reviewed trading session")
    following = [value for value in calendar["sessions"] if value > day]
    if not following:
        raise ValueError("The reviewed calendar does not cover the next session")
    return following[0]


def session_time(day, hour, minute=0):
    return date_only(day).tz_localize("Asia/Shanghai") + pd.Timedelta(hours=hour, minutes=minute)


def input_path(paths, value):
    path = (paths.root / value).resolve()
    if not path.is_relative_to(paths.root) or not path.is_file():
        raise ValueError("Paper inputs must be existing project files")
    return path


def register_output(paths, output):
    """Append completed paper evidence without changing an existing frozen file."""
    manifest = read(paths.manifest)
    for file in sorted(output.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(paths.root).as_posix()
        value = sha(file)
        if relative in manifest["files"] and manifest["files"][relative] != value:
            raise ValueError("Paper output would alter frozen evidence")
        manifest["files"][relative] = value
    save(paths.manifest, manifest)


def initialize_account(paths, output, account_id, as_of=None):
    output = paths.output_path(output, "accounts")
    calendar = load_calendar(paths)
    day = as_of or paths.config["data_cutoff"]
    next_session(calendar, day)
    if timestamp(now()) < session_time(day, 16):
        raise ValueError("Initialize a closing account only after its complete session")
    account = new_account(day, account_id)
    save(output / "account.json", account, immutable=True)
    register_output(paths, output)
    return {"status": "CREATED", "kind": "paper", "account": str(output / "account.json")}


def latest_forecasts(paths, bundle):
    """Require the active completed experiment's latest fit and exact saved prediction rows."""
    evaluation = (
        paths.artifact_path(paths.config["evaluation"], "evaluations") / "evaluation_summary.json"
    )
    manifest = read(paths.manifest)["files"]
    if sha(evaluation) != manifest.get(evaluation.relative_to(paths.root).as_posix()):
        raise ValueError("The active evaluation is not frozen")
    summary = read(evaluation)
    run = paths.artifact_path(summary["source_run"], "experiments")
    status, _ = experiment_folds(paths, bundle, run)
    if timestamp(status["completed_at"]) > timestamp(now()):
        raise ValueError("The training completion time is in the future")
    if (
        status["status"] != "COMPLETE"
        or not status["latest_fit_included"]
        or summary["identity"] != status["identity"]
    ):
        raise ValueError("Next-session planning needs a complete active latest fit")
    folder = run / "final_fit"
    result = read(folder / "result.json")
    index = len(bundle["calendar"]) - 1
    if (
        not result["final_fit"]
        or result["identity"] != status["identity"]
        or result["fold"]["test_start"] != index
        or result["fold"]["test_end"] != index
        or result.get("turnover_policy", "legacy") != paths.config.get("turnover_policy", "legacy")
    ):
        raise ValueError("The latest model is stale or belongs to another experiment")
    file = folder / "outer_predictions.npz"
    if sha(file) != result["predictions_sha256"]:
        raise ValueError("Latest predictions changed")
    with np.load(file) as saved:
        arrays = dict(saved)
    expected = eligible_rows(bundle["table"], index, index)
    if not np.array_equal(expected, arrays["row_indices"]):
        raise ValueError("Latest prediction coverage is incomplete")
    name = result["selected_candidate"]
    for key in ("common_mu", "common_margin", name + "_rank"):
        if arrays[key].shape != expected.shape or not np.isfinite(arrays[key]).all():
            raise ValueError("Latest forecasts contain invalid scores")
    files = [
        evaluation,
        run / "protocol.json",
        run / "training_status.json",
        folder / "result.json",
        file,
    ]
    for family in ("ridge", "tree"):
        metadata = read(folder / "outer_fit" / family / "fit.json")
        model = folder / "outer_fit" / family / "model.joblib"
        if (
            sha(model) != metadata["model_sha256"]
            or metadata["latest_training_label_end"] >= index + 1
        ):
            raise ValueError("Latest estimator identity or label availability is invalid")
        files.extend([model, model.with_name("fit.json")])
    return prediction_map(bundle["table"], arrays, name)[index], result, files


@pd.option_context("compute.use_numexpr", False)
def make_intent(bundle, account, forecasts, config, execute_on, slippage, *, turnover="legacy"):
    """Calculate the same completed-close targets as the historical strategy, before quotes."""
    book, risk = restore_account(account, bundle["meta"])
    signal = date_only(account["as_of"])
    execution = date_only(execute_on)
    if signal != bundle["calendar"][-1] or execution <= signal:
        raise ValueError("Account and completed market snapshot must share the signal date")
    panels = bundle["panels"]
    close = {}
    for symbol in book.lots:
        raw = bundle["raws"][symbol]
        if signal not in raw.index or not np.isfinite(raw.at[signal, "close"]):
            raise ValueError(f"Holding has no verified closing mark: {symbol}")
        close[symbol] = float(raw.at[signal, "close"])
    prior = book.nav(close)
    if prior <= 0:
        raise ValueError("Paper account NAV is not positive")
    valuation = account.get("valuation")
    if valuation is not None and (
        abs(valuation["nav"] - prior) > 0.005
        or valuation["positions"] != book.values(close)
        or valuation["quotes"] != close
    ):
        raise ValueError(
            "Account closing marks differ from the reviewed market snapshot; reconcile first"
        )
    state = risk.update(prior, healthy_market(panels, signal))
    cap = min(regime(panels, signal)[0], state["cap"])
    values = book.values(close)
    stopped = [symbol for symbol in values if close[symbol] / book.stop_cost(symbol) - 1 <= -0.08]
    scheduled = account["sessions_completed"] % 5 == 0
    forced = state["changed"] or sum(values.values()) / prior > cap + 0.01
    planned = scheduled or forced
    valid = {
        symbol: value
        for symbol, value in forecasts.items()
        if panels["eligible"].at[signal, symbol]
        and np.isfinite(value["mu"])
        and np.isfinite(value["margin"])
    }
    if planned:
        targets, decision = allocate(
            bundle["meta"],
            valid,
            {s: v / prior for s, v in values.items()},
            cap,
            state["level"],
            panels["adv20"].loc[signal].to_dict(),
            prior,
            {s: book.available(s, execution) * close[s] / prior for s in values},
            panels["ret1"].loc[:signal].tail(60),
            slippage,
            sum(r["cents"] for r in book.receivables) / 100 / prior,
            config=config,
            turnover=turnover,
        )
    else:
        targets, decision = (
            {},
            {
                "status": "HOLD_BETWEEN_REBALANCES",
                "cap": cap,
                "level": state["level"],
                "selected": [],
            },
        )
    required = list(dict.fromkeys([*book.lots, *targets]))
    index = len(bundle["calendar"]) - 1
    atr = {
        (int(row.date_index), str(row.symbol)): float(row.atr_pct)
        for row in bundle["table"].loc[bundle["table"].date_index == index].itertuples()
    }
    contracts = {}
    by_symbol = defaultdict(list)
    for action in bundle["actions"]:
        by_symbol[action["symbol"]].append(action)
    for symbol in required:
        meta = bundle["meta"][symbol]
        raw = bundle["raws"][symbol]
        previous = float(raw.at[signal, "close"])
        reference = previous
        for action in by_symbol[symbol]:
            if action["ex_date"] == execute_on:
                reference = (
                    reference - action.get("ex_reference_cash_per_share", action["cash_per_share"])
                ) / action.get("ex_reference_share_multiplier", action["share_multiplier"])
                reference = action.get("price_limit_reference", reference)
        number(reference, "ex-date opening reference", positive=True)
        tick = 0.001 if meta["kind"] == "etf" else 0.01
        limit = board_limit(meta, execution)
        ceiling = buy_ceiling(
            raw, signal, execution, by_symbol[symbol], atr.get((index, symbol), np.nan)
        )
        contracts[symbol] = {
            "meta": meta,
            "prior_close": previous,
            "reference_quote": nearest_tick(reference, tick),
            "adv20": float(panels["adv20"].at[signal, symbol]),
            "atr_pct": atr.get((index, symbol)),
            "tick": tick,
            "assumed_lower_limit": nearest_tick(reference * (1 - limit), tick),
            "assumed_upper_limit": nearest_tick(reference * (1 + limit), tick),
            "buy_ceiling": None if ceiling is None else tick_price(ceiling, tick),
            "buy_minimum": lot_rule(meta)[0],
            "buy_increment": lot_rule(meta)[1],
            "held_quantity": book.quantity(symbol),
            "sellable_quantity": book.available(symbol, execution),
        }
        if not np.isfinite(contracts[symbol]["adv20"]):
            raise ValueError(f"Missing prior liquidity estimate: {symbol}")
        if contracts[symbol]["atr_pct"] is not None and not np.isfinite(
            contracts[symbol]["atr_pct"]
        ):
            contracts[symbol]["atr_pct"] = None
    return {
        "signal_date": account["as_of"],
        "execution_date": execute_on,
        "prior_close_nav": prior,
        "scheduled": scheduled,
        "forced": forced,
        "planned": planned,
        "state": state,
        "risk_after_observation": asdict(risk),
        "cap": cap,
        "stopped": stopped,
        "rule_config": config,
        "turnover_policy": turnover,
        "target_weights": targets,
        "decision": decision,
        "contracts": contracts,
        "forecasts": {s: valid[s] for s in required if s in valid},
        "slippage_rate": slippage,
        "account_meta": {
            s: bundle["meta"][s] for s in set(book.lots) | {r["symbol"] for r in book.receivables}
        },
    }


def prepared_actions(account, actions, calendar, execute_on):
    """Require record-date rights and known corporate terms; resolve dates only from the reviewed calendar."""
    result = deepcopy(actions)
    for action in result:
        pay = action.get("cash_pay_date")
        if pay and action.get("cash_per_share", 0) and not action.get("cash_available_date"):
            later = [day for day in calendar["sessions"] if day > pay]
            action["cash_available_date"] = later[0] if later else None
        if action["ex_date"] != execute_on:
            continue
        record = action.get("record_date")
        if record is None or record >= execute_on:
            if any(lot["symbol"] == action["symbol"] for lot in account["lots"]):
                raise ValueError("A held corporate action has an unresolved record date")
            continue
        if (
            account["initial_date"] <= record < account["as_of"]
            and action["id"] not in account["entitlements"]
        ):
            raise ValueError(f"Missing historical record-date entitlement: {action['id']}")
    return result


def resolve_intent(intent, account, actions, calendar, quotes, *, preview=False):
    """Apply only the frozen intention and supplied opening quotes to a separate paper ledger."""
    contracts = intent["contracts"]
    meta = {s: item["meta"] for s, item in contracts.items()}
    meta.update(intent["account_meta"])
    # Receivables and old entitlements can refer to securities outside today's targets.
    for action in actions:
        if action["symbol"] not in meta and action.get("paper_meta"):
            meta[action["symbol"]] = action["paper_meta"]
    book, _ = restore_account(account, meta)
    signal, execution = date_only(intent["signal_date"]), date_only(intent["execution_date"])
    opening_at = session_time(intent["execution_date"], 9, 25)
    by_symbol = defaultdict(list)
    actions = prepared_actions(account, actions, calendar, intent["execution_date"])
    for action in actions:
        by_symbol[action["symbol"]].append(action)
    book.snapshot_record_dates(signal, actions)
    book.apply_actions(execution, [a for a in actions if a["ex_date"] == intent["execution_date"]])
    for event in book.events:
        if event["type"] == "CORPORATE_ACTION" and (
            event["unknown_cash_pay_date"]
            or event["unknown_cash_available_date"]
            or event["unknown_share_list_date"]
            or event["bonus_tax_not_modelled"]
            or event["fractional_shares_discarded"] > 0
        ):
            raise ValueError("A paper account corporate action remains unresolved")
    risk = RiskState(**intent["risk_after_observation"])
    opening = snapshot_account(
        book, account, account["as_of"], risk, account["sessions_completed"], None
    )
    prices, raws, statuses = {}, {}, {}
    for symbol, contract in contracts.items():
        quote = quotes.get(symbol)
        price = contract["reference_quote"] if preview else quote.get("open") if quote else None
        if price is not None:
            number(price, "opening quote", positive=True)
        if book.quantity(symbol) and price is None:
            raise ValueError(f"Missing opening valuation for existing holding: {symbol}")
        if not preview and quote is not None:
            if timestamp(quote["market_at"]) != opening_at or type(quote["suspended"]) is not bool:
                raise ValueError(
                    "Opening quotes need the target auction timestamp and explicit status"
                )
            statuses[symbol] = {"known_at": quote["market_at"], "suspended": quote["suspended"]}
        if price is not None:
            prices[symbol] = price
        raws[symbol] = pd.DataFrame(
            {"close": [contract["prior_close"], np.nan], "open": [np.nan, price]},
            index=[signal, execution],
        )
    bundle = {
        "calendar": pd.DatetimeIndex([signal, execution]),
        "raws": raws,
        "meta": meta,
        "panels": {
            "adv20": pd.DataFrame([{s: c["adv20"] for s, c in contracts.items()}], index=[signal])
        },
        "atr_lookup": {
            (0, s): (np.nan if c["atr_pct"] is None else c["atr_pct"]) for s, c in contracts.items()
        },
        "opening_status": {intent["execution_date"]: statuses},
    }
    nav = book.nav(prices)
    values = book.values(prices)
    targets = (
        {s: nav * w for s, w in intent["target_weights"].items()}
        if intent["planned"]
        else dict(values)
    )
    if intent["planned"]:
        for symbol in values:
            targets.setdefault(symbol, 0.0)
    for symbol in intent["stopped"]:
        targets[symbol] = 0.0
    if intent["state"]["level"] == 3:
        targets = {s: 0.0 for s in values}
    decision = deepcopy(intent["decision"])
    marks = []
    execution_engine = _Execution(
        book, bundle, 1, prices, by_symbol, intent["slippage_rate"], intent["cap"], marks
    )
    execution_engine.sell(targets)
    if intent["planned"] and intent["state"]["can_buy"]:
        execution_engine.buy(targets, intent["stopped"], intent["forecasts"], decision)
    after = snapshot_account(
        book, account, intent["execution_date"], risk, account["sessions_completed"] + 1, None
    )
    result = {
        "status": "REFERENCE_QUOTE_PREVIEW" if preview else "PAPER_OPENING_RESULT",
        "execution_date": intent["execution_date"],
        "opening_marks": prices,
        "opening_nav_after_costs": book.nav(prices),
        "opening_account": after,
        "trades": book.trades,
        "events": book.events,
        "rejections": book.rejections,
        "intermediate_weights": marks,
        "decision": decision,
    }
    result["audit"] = audit_fills(
        opening, result, meta, intent["execution_date"], intent["slippage_rate"], intent["cap"]
    )
    return result


def source_hashes():
    return {name: sha(Path(__file__).with_name(name + ".py")) for name in SOURCE_MODULES}


def validate_parent(paths, account):
    """A carried paper account must retain the actual balances and risk of its frozen parent."""
    parent = account["parent"]
    if parent is None:
        if account["kind"] == "paper" and account["sessions_completed"]:
            raise ValueError("A continuing paper account requires its frozen parent")
        return
    resolution_path = input_path(paths, parent["resolution"])
    closing_path = input_path(paths, parent["closing"])
    if sha(resolution_path) != parent["sha256"] or sha(closing_path) != parent["closing_sha256"]:
        raise ValueError("Parent paper evidence changed")
    manifest = read(paths.manifest)["files"]
    if manifest.get(resolution_path.relative_to(paths.root).as_posix()) != sha(resolution_path):
        raise ValueError("Parent paper result is not frozen")
    result = read(resolution_path)
    original = result["opening_account"]
    for key in (
        "account_id",
        "initial_date",
        "initial_cash",
        "as_of",
        "cash",
        "lots",
        "next_lot_id",
        "receivables",
        "risk",
        "sessions_completed",
    ):
        if account[key] != original[key]:
            raise ValueError(f"Paper account differs from its recorded parent: {key}")
    _, plan = verified_plan(paths, result["plan"])
    meta = {s: c["meta"] for s, c in plan["intent"]["contracts"].items()}
    meta.update(plan["intent"]["account_meta"])
    meta.update({a["symbol"]: a["paper_meta"] for a in plan["actions"]})
    book, risk = restore_account(original, meta)
    book.snapshot_record_dates(date_only(account["as_of"]), plan["actions"])
    expected = snapshot_account(
        book, original, account["as_of"], risk, account["sessions_completed"], parent
    )
    if account["entitlements"] != expected["entitlements"]:
        raise ValueError("Paper record-date entitlements differ from the frozen account")


def freeze_plan(paths, account_file, output, cost=10):
    """Create a future conditional plan, or a dated blocked record with no instructions."""
    output = paths.output_path(output, "plans")
    if cost not in (10, 20, 30):
        raise ValueError("Choose 10, 20 or 30 bps per side")
    check_integrity(paths)
    account_path = input_path(paths, account_file)
    account = read(account_path)
    validate_parent(paths, account)
    calendar = load_calendar(paths)
    cutoff = paths.config["data_cutoff"]
    execute_on = next_session(calendar, cutoff)
    created = timestamp(now())
    if timestamp(account["created_at"]) > created:
        raise ValueError("The account snapshot was created in the future")
    blockers = []
    status_review = load_status_review(paths)
    if status_review is not None and status_review["blocking"]:
        blockers.append("HISTORICAL_ST_ELIGIBILITY_REPAIR_REQUIRED")
    if account["as_of"] != cutoff:
        blockers.append("ACCOUNT_DATE_DIFFERS_FROM_DATA")
    if any(item["available_date"] is None for item in account["receivables"]):
        blockers.append("UNRESOLVED_RECEIVABLE_AVAILABILITY")
    if created < session_time(cutoff, 16):
        blockers.append("SIGNAL_SESSION_NOT_COMPLETE")
    if created >= session_time(execute_on, 9, 25):
        blockers.append("NEXT_OPEN_ALREADY_OCCURRED_OR_DATA_STALE")
    bundle = load_bundle(paths)
    restore_account(account, bundle["meta"])
    forecasts, fitted, files = latest_forecasts(paths, bundle)
    files.extend(
        [
            account_path,
            paths.bundle,
            paths.root / paths.config["calendar"],
            paths.root / "configs/competition.json",
        ]
    )
    if status_review is not None:
        files.append(paths.root / status_review["path"])
    record = {
        "schema_version": 1,
        "created_at": created.isoformat(),
        "status": "BLOCKED" if blockers else "FROZEN_CONDITIONAL_PLAN",
        "decision": "RESEARCH_ONLY",
        "blockers": blockers,
        "signal_date": cutoff,
        "execution_date": execute_on,
        "expires_at": session_time(execute_on, 9, 25).isoformat(),
        "resolve_by": session_time(execute_on, 9, 30).isoformat(),
        "account": account,
        "model": fitted["selected_candidate"],
        "rule_config": fitted["selected_rules"],
        "active_configuration": dict(paths.config),
        "source_sha256": source_hashes(),
        "input_sha256": {p.relative_to(paths.root).as_posix(): sha(p) for p in files},
        "calendar": calendar,
        "orders": [],
        "historical_status_review": status_review,
        "signal_horizon_sessions": 5,
        "limitations": [
            "Historical superiority has not passed.",
            "Manual paper workflow; no broker or APP connection.",
            "Fees, slippage and quote fills are research assumptions; contest matching remains unconfirmed.",
            "Current industry classifications, survival selection and historical trading-status coverage are incomplete.",
        ],
    }
    upcoming = [day for day in calendar["sessions"] if session_time(day, 9, 25) > created]
    if upcoming:
        position = calendar["sessions"].index(upcoming[0])
        required = calendar["sessions"][position - 1] if position else None
        record["next_review"] = {
            "execution_date": upcoming[0],
            "required_data_cutoff": required,
            "data_ready_after": session_time(required, 16).isoformat() if required else None,
        }
    if not blockers:
        actions = deepcopy(
            [
                a
                for a in bundle["actions"]
                if a["ex_date"] == execute_on or a.get("record_date") in (cutoff, execute_on)
            ]
        )
        for action in actions:
            action["paper_meta"] = bundle["meta"][action["symbol"]]
        try:
            intent = make_intent(
                bundle,
                account,
                forecasts,
                fitted["selected_rules"],
                execute_on,
                cost / 10000,
                turnover=fitted.get("turnover_policy", "legacy"),
            )
            preview = resolve_intent(intent, account, actions, calendar, {}, preview=True)
        except ValueError as error:
            record["status"] = "BLOCKED"
            record["blockers"].append(str(error))
        else:
            record.update(intent=intent, actions=actions, preview=preview)
            record["orders"] = preview["trades"]
    record["identity"] = digest(record)
    save(output / "plan.json", record, immutable=True)
    write_plan_report(record, output / "plan.md")
    register_output(paths, output)
    return {
        "status": record["status"],
        "blockers": record["blockers"],
        "execution_date": execute_on,
        "plan": str(output / "plan.json"),
        "report": str(output / "plan.md"),
        "next_review": record.get("next_review"),
    }


def write_plan_report(plan, path):
    lines = [
        f"# 纸面次日计划：{plan['execution_date']}",
        "",
        f"状态：{plan['status']}。数据日期：{plan['signal_date']}。生成于{plan['created_at']}。",
        "",
        f"有效期至{plan['expires_at']}；仅适用于指定开盘，过期须重新生成。",
        "",
        "此计划用于研究；参考价股数需按开盘报价重新计算，费用和撮合采用模型假设。",
        "",
    ]
    if plan["blockers"]:
        reasons = {
            "ACCOUNT_DATE_DIFFERS_FROM_DATA": "账户估值日期与行情截止日不一致。",
            "UNRESOLVED_RECEIVABLE_AVAILABILITY": "应收款的可用日期尚未核实。",
            "SIGNAL_SESSION_NOT_COMPLETE": "信号日尚未达到完整日线的核验时点。",
            "HISTORICAL_ST_ELIGIBILITY_REPAIR_REQUIRED": "已确认历史ST期间的资格筛选错误，需修正数据、重训并验收后再生成建议。",
            "NEXT_OPEN_ALREADY_OCCURRED_OR_DATA_STALE": "对应开盘已经发生，现有数据不足以生成新的前向计划。",
        }
        lines += ["当前不生成买卖建议：", ""] + [
            f"- {reasons.get(reason, reason)}" for reason in plan["blockers"]
        ]
        review = plan.get("next_review")
        if review:
            lines += [
                "",
                f"下一次可研究的开盘：{review['execution_date']}；需要截至{review['required_data_cutoff']}的核验行情与对应最新模型。数据完整时点不早于{review['data_ready_after']}。",
            ]
    else:
        intent = plan["intent"]
        lines += [
            f"组合风险状态：{intent['state']['phase']}；总仓位上限{intent['cap']:.0%}；原净值高水位{intent['state']['peak']:.2f}元。",
            "",
            "| 标的 | 方向 | 参考股数 | 参考报价 | 买入报价上限 |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
        for order in plan["orders"]:
            ceiling = intent["contracts"][order["symbol"]]["buy_ceiling"]
            lines.append(
                f"| {order['symbol']} | {order['side']} | {order['quantity']} | {order['price']:.3f} | {ceiling if order['side'] == 'BUY' else '—'} |"
            )
        if not plan["orders"]:
            lines += ["", "参考报价下没有成交；仍须检查持仓止损和强制减仓条件。"]
        lines += [
            "",
            "先卖后买；按实际可卖股数与可用现金重新核算。单只买入后不超过净值15%、行业不超过30%；涨跌停、停牌、跳空超限或不足最小股数时跳过相应交易。",
            "",
            "完整目标权重、整手规则、费用、预测身份、拒单和报价条件保存在同目录plan.json。",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def verified_plan(paths, plan_file):
    file = input_path(paths, plan_file)
    expected = read(paths.manifest)["files"].get(file.relative_to(paths.root).as_posix())
    if sha(file) != expected:
        raise ValueError("Plan is not frozen or has changed")
    plan = read(file)
    if plan["identity"] != digest({k: v for k, v in plan.items() if k != "identity"}):
        raise ValueError("Plan identity mismatch")
    if plan["status"] != "FROZEN_CONDITIONAL_PLAN" or timestamp(plan["created_at"]) >= timestamp(
        plan["expires_at"]
    ):
        raise ValueError("Only a plan actually frozen before its opening can be resolved")
    if source_hashes() != plan["source_sha256"]:
        raise ValueError("Paper calculation source changed after the plan was frozen")
    for relative, expected in plan["input_sha256"].items():
        if sha(input_path(paths, relative)) != expected:
            raise ValueError(f"Plan input changed: {relative}")
    return file, plan


def resolve_plan(paths, plan_file, quotes_file, output):
    output = paths.output_path(output, "plans")
    file, plan = verified_plan(paths, plan_file)
    quote_path = input_path(paths, quotes_file)
    quotes = read(quote_path)
    observed = timestamp(now())
    opening = timestamp(plan["expires_at"])
    if observed < opening or quotes["session"] != plan["execution_date"] or not quotes["source"]:
        raise ValueError("Opening quotes are not yet available or belong to another session")
    supplied = timestamp(quotes["recorded_at"])
    if not opening <= supplied <= observed:
        raise ValueError("Quote collection timestamp is outside the observed period")
    if observed <= timestamp(plan["resolve_by"]) and paths.config != plan["active_configuration"]:
        raise ValueError("Active research configuration changed; generate a new paper plan")
    result = resolve_intent(
        plan["intent"], plan["account"], plan["actions"], plan["calendar"], quotes["quotes"]
    )
    result.update(
        created_at=observed.isoformat(),
        decision="RESEARCH_ONLY",
        use="OPENING_PAPER_REVIEW"
        if observed <= timestamp(plan["resolve_by"])
        else "RETROSPECTIVE_PAPER_REPLAY",
        plan=file.relative_to(paths.root).as_posix(),
        plan_sha256=sha(file),
        quotes=quote_path.relative_to(paths.root).as_posix(),
        quotes_sha256=sha(quote_path),
        account_id=plan["account"]["account_id"],
    )
    result["identity"] = digest(result)
    save(output / "resolution.json", result, immutable=True)
    register_output(paths, output)
    return {
        "status": result["status"],
        "use": result["use"],
        "trades": len(result["trades"]),
        "audit": result["audit"],
        "resolution": str(output / "resolution.json"),
    }


def close_account(paths, resolution_file, closing_file, output):
    """Mark a frozen paper result at supplied closes and carry shares and risk to the next plan."""
    output = paths.output_path(output, "accounts")
    file = input_path(paths, resolution_file)
    manifest = read(paths.manifest)["files"]
    if sha(file) != manifest.get(file.relative_to(paths.root).as_posix()):
        raise ValueError("Opening paper result is not frozen")
    result = read(file)
    if result["identity"] != digest({k: v for k, v in result.items() if k != "identity"}):
        raise ValueError("Opening paper result identity changed")
    _, plan = verified_plan(paths, result["plan"])
    if sha(paths.root / result["plan"]) != result["plan_sha256"]:
        raise ValueError("Opening result belongs to a different plan")
    if sha(input_path(paths, result["quotes"])) != result["quotes_sha256"]:
        raise ValueError("Opening quote evidence changed")
    closing_path = input_path(paths, closing_file)
    closing = read(closing_path)
    observed = timestamp(now())
    day = result["execution_date"]
    if observed < session_time(day, 16) or closing["session"] != day or not closing["source"]:
        raise ValueError("Closing account requires completed-session quotes")
    if not session_time(day, 15) <= timestamp(closing["recorded_at"]) <= observed:
        raise ValueError("Closing quote timestamp is invalid")
    meta = {s: c["meta"] for s, c in plan["intent"]["contracts"].items()}
    meta.update(plan["intent"]["account_meta"])
    meta.update({a["symbol"]: a["paper_meta"] for a in plan["actions"]})
    account = result["opening_account"]
    book, risk = restore_account(account, meta)
    prices = {}
    for symbol in book.lots:
        quote = closing["quotes"].get(symbol)
        if not quote or timestamp(quote["market_at"]) != session_time(day, 15):
            raise ValueError(f"Missing completed closing mark: {symbol}")
        prices[symbol] = number(quote["close"], "closing quote", positive=True)
    book.snapshot_record_dates(date_only(day), plan["actions"])
    parent = {
        "resolution": file.relative_to(paths.root).as_posix(),
        "sha256": sha(file),
        "closing": closing_path.relative_to(paths.root).as_posix(),
        "closing_sha256": sha(closing_path),
    }
    account = snapshot_account(book, account, day, risk, account["sessions_completed"], parent)
    account["valuation"] = {
        "nav": book.nav(prices),
        "cash": book.cash / 100,
        "receivables": sum(r["cents"] for r in book.receivables) / 100,
        "positions": book.values(prices),
        "quotes": prices,
    }
    save(output / "account.json", account, immutable=True)
    register_output(paths, output)
    return {
        "status": "CLOSED",
        "as_of": day,
        "nav": account["valuation"]["nav"],
        "account": str(output / "account.json"),
    }
