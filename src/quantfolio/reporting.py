"""Build a self-contained local research report from immutable experiment evidence."""

from __future__ import annotations

import os
from html import escape
from importlib.resources import files
from pathlib import Path
from string import Template

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .dataset import check_integrity
from .settings import Paths
from .status_data import load_status_review
from .storage import load_gzip, read, sha


def build_report(paths: Paths, run: str | Path | None = None, *, reference: bool = False) -> dict:
    """Render frozen results or an evaluated experiment without mixing sample periods."""
    check_integrity(paths)
    requested = run or (None if reference else paths.config.get("evaluation"))
    reference = requested is None
    source = paths.reference if reference else paths.artifact_path(requested, "evaluations")
    result = read(source / "evaluation_summary.json")
    if not reference and (
        result.get("schema_version") != 1 or result["status"] not in {"COMPLETE", "PARTIAL"}
    ):
        raise ValueError("Report requires a completed evaluation summary")
    costs = sorted(result["results"], key=lambda cost: int(cost[:-3]))
    cost = costs[0]
    cost_bps = int(cost[:-3])
    benchmark = read(paths.benchmark) if reference else None
    destination = (
        paths.root
        / "reports"
        / (f"experiments/{source.name}" if run else "reference" if reference else "latest")
    ).resolve()
    if not destination.is_relative_to(paths.root / "reports"):
        raise ValueError("Report destination escapes the project")
    destination.mkdir(parents=True, exist_ok=True)
    primary_policy = result["primary_policy"]
    primary = result["continuous"][cost][primary_policy]["summary"]
    aggregate = result["results"][cost][primary_policy]["aggregate"]

    def curve_for(policy):
        filename = paths.archive.get("policies", {}).get(policy, policy) if reference else policy
        relative = f"evaluation/continuous/{cost}/{filename}.json.gz"
        path = source / relative
        if not reference and sha(path) != result["artifacts"][relative]:
            raise ValueError(f"Evaluation ledger hash mismatch: {relative}")
        return load_gzip(path)["ledger"]["curve"]

    curve = curve_for(primary_policy)
    dates = pd.to_datetime([point["date"] for point in curve])
    figure, axis = plt.subplots(figsize=(11, 4.2), layout="constrained")
    figure.set_facecolor("#f7f9fc")
    axis.set_facecolor("#f7f9fc")
    axis.plot(
        dates,
        [point["nav"] / 1e6 for point in curve],
        color="#185adb",
        linewidth=2,
        label="Walk-forward momentum",
    )
    momentum_curve = curve_for("matched_momentum")
    axis.plot(
        pd.to_datetime([point["date"] for point in momentum_curve]),
        [point["nav"] / 1e6 for point in momentum_curve],
        color="#6a57a5",
        linestyle="--",
        linewidth=1.3,
        label="Matched momentum",
    )
    if benchmark:
        old_curve = benchmark[cost]["curve"]
        axis.plot(
            pd.to_datetime([point["date"] for point in old_curve]),
            [point["nav"] / 1e6 for point in old_curve],
            color="#a45b00",
            linewidth=1.5,
            alpha=0.8,
            label="Historical baseline (no recovery)",
        )
    axis.axhline(1, color="#7d8794", linewidth=0.8)
    axis.set_ylabel("Net asset value / initial capital")
    axis.set_title(f"Continuous account · {cost_bps} bp slippage per side", loc="left")
    axis.grid(alpha=0.18)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, loc="upper left")
    figure.savefig(destination / "equity_curve.png", dpi=160)
    plt.close(figure)
    rows = []
    for current_cost in costs:
        current = result["continuous"][current_cost][primary_policy]["summary"]
        momentum = result["continuous"][current_cost]["matched_momentum"]["summary"]
        episode = result["results"][current_cost][primary_policy]["aggregate"]
        old_cell = (
            f"<td>{benchmark[current_cost]['continuous']['return']:.2%}</td>" if benchmark else ""
        )
        rows.append(
            f"<tr><td>{current_cost}</td><td>{current['return']:.2%}</td>"
            f"<td>{momentum['return']:.2%}</td>{old_cell}"
            f"<td>{current['max_drawdown']:.2%}</td>"
            f"<td>{episode['mean_net_return']:.2%}</td></tr>"
        )
    template = files("quantfolio").joinpath("templates/report.html").read_text("utf-8")
    title = f"{paths.config['strategy_name']}回测" if reference else f"实验回测：{source.name}"
    notice = (
        "修正前的冻结记录：原成交器使用了开盘后才可知的高低价和全天成交量。"
        "这些结果只用于追溯，不能作为修正后的收益验证。原收益优势检验也未通过。"
        if reference
        else "此实验尚未进行收益优势检验，结果用于研究。"
    )
    if not reference and result["status"] == "PARTIAL":
        notice = "部分窗口回测。" + notice
    if (
        not reference
        and result["status"] == "COMPLETE"
        and all(
            values[primary_policy]["summary"]["return"]
            == values["matched_momentum"]["summary"]["return"]
            for values in result["continuous"].values()
        )
    ):
        notice = (
            "当前各档连续账户收益与同风控动量相同，未显示超额收益；工程验收通过不代表预测有效。"
        )
    elif (
        not reference
        and result["status"] == "COMPLETE"
        and all(
            values[primary_policy]["summary"]["return"]
            < values["matched_momentum"]["summary"]["return"]
            for values in result["continuous"].values()
        )
    ):
        notice = "当前各档连续账户收益均低于同风控动量，未通过收益优势要求。"
    if not reference and any(
        values[primary_policy]["summary"]["max_drawdown"] < -0.08
        for values in result["continuous"].values()
    ):
        notice += " 已观察到超过8%的回撤；预警线不是保证的损失上限。"
    if result.get("purpose") == "EXECUTION_DIAGNOSTIC":
        notice = (
            "执行诊断：固定旧预测及旧选型，仅更换成交与成本模型。"
            "尚未重新训练，不代表选股改进或收益优势通过；未解决公司行为须继续核查。"
        )
    if result.get("purpose") == "DATA_DIAGNOSTIC":
        notice = (
            "数据诊断：固定旧预测及旧选型，比较核验公司行为与现金可用时点后的结果。"
            "尚未重新训练，不代表选股改进或收益优势通过；数据覆盖限制仍保留。"
        )
    active = paths.config.get("evaluation")
    if active and source == paths.artifact_path(active, "evaluations"):
        review = load_status_review(paths)
        if review is not None and review["blocking"]:
            notice = (
                "历史数据待修正：已确认ST期间仍有样本通过资格筛选，且影响实际模拟买入。"
                "此处保留原回测结果；修正与重新训练尚未完成，次日建议已暂停。 " + notice
            )
    links = {
        "methodology_link": "docs/methodology.md",
        "reproduction_link": "docs/reproducibility.md",
        "results_link": (source / "evaluation_summary.json").relative_to(paths.root).as_posix(),
        "readme_link": "README.md",
    }
    document = Template(template).substitute(
        title=escape(title),
        notice=escape(notice),
        cost_bps=cost_bps,
        data_cutoff=escape(result.get("data_cutoff", paths.config["data_cutoff"])),
        folds=len(result["fold_choices"]),
        episodes=aggregate["complete_episodes"],
        trade_start=result["trade_start"],
        trade_end=result["trade_end"],
        net_return=f"{primary['return']:.2%}",
        drawdown=f"{primary['max_drawdown']:.2%}",
        average=f"{aggregate['mean_net_return']:.2%}",
        benchmark_header="<th>历史基准（不恢复仓位）</th>" if benchmark else "",
        scope="此页展示冻结回测，不包含实时行情或新交易指令。"
        if reference
        else "仅展示本实验已完成窗口，不能与不同区间的完整历史收益直接比较。",
        rows="".join(rows),
        **{
            name: escape(Path(os.path.relpath(paths.root / relative, destination)).as_posix())
            for name, relative in links.items()
        },
    )
    (destination / "index.html").write_text(document, "utf-8", newline="\n")
    return {
        "status": "BUILT",
        "decision": "RESEARCH_ONLY",
        "report": str(destination / "index.html"),
    }
