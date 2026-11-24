from pathlib import Path
import json
import subprocess
import tomllib

from quantfolio.integrity import register_output
from quantfolio.settings import Paths
from quantfolio.storage import now, read, save, sha

root = Path.cwd()
paths = Paths(root, "configs/experiments/daily_close_2026-11-23.toml")
out = root / "artifacts/checks/daily_close_2026-11-23"
validation = read(out / "validation.json")
signals = read(out / "signals.json")
plan_path = root / "artifacts/plans/daily_close_2026-11-24/plan.json"
plan = read(plan_path)
assert validation["status"] == "PASS" and signals["positive_net_edge"] == 0
assert plan["status"] == "FROZEN_CONDITIONAL_PLAN" and not plan["blockers"]
assert plan["intent"]["decision"]["status"] == "CASH_NO_POSITIVE_EDGE" and not plan["orders"]
assert plan["account"]["cash"] == 1_000_000 and not plan["account"]["lots"]
baseline = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
assert baseline == "afab8da3b6ad3de77d316ab9019d5a049778ddf8"
old_config = tomllib.loads(subprocess.check_output(["git", "show", "HEAD:configs/project.toml"]).decode())
changed = {key for key in old_config if old_config[key] != paths.config[key]}
assert changed == {"data_cutoff", "bundle", "evaluation", "historical_status_review", "protocol"}
assert not subprocess.check_output(["git", "diff", "HEAD", "--name-only", "--", "src", "tests", "scripts", "environments", "uv.lock"])
old_manifest = json.loads(subprocess.check_output(["git", "show", "HEAD:configs/integrity_manifest.json"]))["files"]
manifest = read(paths.manifest)["files"]
assert all(manifest.get(name) == digest for name, digest in old_manifest.items())

best = max(signals["ranked"], key=lambda row: row["net_edge"])
stocks = [row for row in signals["ranked"] if row["kind"] == "stock"][:3]
assert len(stocks) == 3
report = root / "reports/plans/2026-11-24.md"
assert not report.exists()
report.parent.mkdir(parents=True, exist_ok=True)
text = [
    "# 2026年11月24日模拟账户操作", "",
    "**决定：保持空仓。买入0笔，卖出0笔，可用现金100万元。**", "",
    "账户来自用户确认的11月23日空仓余额。计划在指定交易日开盘前冻结，尚未记录任何成交。", "",
    "## 信号依据", "",
    "11月23日行情已补齐原股票池594只证券，568只符合当日选股资格，0只通过正收益边际门槛。市场处于策略定义的弱势区间，仓位上限30%，本次目标仓位0%。", "",
    f"最高保守净收益边际为{best['net_edge']:.2%}（{best['code']} {best['name']}）。该值为原模型的5个交易日收益校准值减去不确定性折减与往返成本，不是11月24日单日涨跌预测。", "",
    "动量排序最高的三只股票均未通过收益门槛：", "",
    "| 代码 | 名称 | 5日校准收益 | 不确定性折减 | 往返成本假设 | 净收益边际 | 操作 |",
    "| --- | --- | ---: | ---: | ---: | ---: | --- |",
]
for row in stocks:
    text.append(f"| {row['code']} | {row['name']} | {row['mu']:.2%} | {row['margin']:.2%} | {row['round_trip_cost_rate']:.3%} | {row['net_edge']:.2%} | 不买入 |")
text += [
    "", "成本采用每边10bp滑点及既有佣金、过户费和印花税假设；具体小额订单还受最低佣金约束。本次没有订单及交易费用。", "",
    "## 风控与数据核验", "",
    "沿用均衡进攻、原始净值高水位、4%/6%/8%分级降仓及降仓后不恢复规则。8%为预警和退出目标；本次没有修改门槛、仓位恢复方式或换手政策。", "",
    "568只沪深证券采用BaoStock，并与腾讯收盘报价核验。26只北交所证券核对腾讯日线与报价，其中5只另有东方财富日线佐证，其余21只仍依赖同一提供方。科创板成交量按既定规则统一单位；18处换手率口径差异保留，替换输入后采用策略的预测和计划完全一致。", "",
    "48个模型重载验证通过，11个历史窗口的预测、1425个历史账户结果及114份账本均与原结果完全一致。此次仅更新收盘数据和按既有流程计算信号，原策略研究继续暂停。", "",
    "[正式计划](../../artifacts/plans/daily_close_2026-11-24/plan.md) · [全部信号](../../artifacts/checks/daily_close_2026-11-23/signals.json) · [验证记录](../../artifacts/checks/daily_close_2026-11-23/validation.json)", "",
]
report.write_text("\n".join(text), "utf-8", newline="\n")
register_output(paths, report.parent)
register_output(paths, root / "reports/experiments/daily_close_2026-11-23")
(out / "completion_source.py").write_bytes(Path(__file__).read_bytes())
save(out / "completion.json", {
    "status": "VALIDATED", "completed_at": now(), "baseline_commit": baseline,
    "scope": "Daily data refresh, unchanged training/evaluation workflow and conditional paper plan only; original strategy research remains paused.",
    "strategy_source_changed": False, "strategy_settings_changed": False,
    "prior_manifest_entries_preserved": len(old_manifest),
    "cross_source": read(root / "artifacts/checks/session_2026-11-23_reviewed/cross_source.json")["status"],
    "models_reloaded": validation["models_reloaded"],
    "historical_accounts_exact": validation["historical_accounts_exact"],
    "historical_ledger_files_exact": validation["historical_ledger_files_exact"],
    "turnover_sensitivity": validation["turnover_sensitivity"],
    "signal_date": plan["signal_date"], "execution_date": plan["execution_date"],
    "plan_status": plan["status"], "plan_created_at": plan["created_at"],
    "plan_decision": plan["intent"]["decision"]["status"], "orders": 0, "cash": plan["account"]["cash"],
    "eligible_signals": len(signals["ranked"]), "positive_net_edge": 0,
    "best_net_edge": best["net_edge"], "executions_recorded": 0,
    "evidence": {p.relative_to(root).as_posix(): sha(p) for p in [plan_path, report, out / "validation.json", out / "signals.json", out / "turnover_sensitivity/result.json"]},
    "publication": "Full manifest scan, GitHub CI and independent remote-byte verification follow; no publication success is asserted by this pre-publication record."
}, immutable=True)
register_output(paths, out)
print(json.dumps({"status": "VALIDATED", "report": str(report), "decision": plan["intent"]["decision"]["status"], "best_net_edge": best["net_edge"]}, ensure_ascii=False))
