"""Build a self-contained local research report from immutable experiment evidence."""

from __future__ import annotations

from html import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .dataset import check_integrity
from .settings import Paths
from .storage import load_gzip, read


def build_report(paths: Paths) -> dict:
    """Render the retained results, benchmarks and failed adoption gates without new claims."""
    check_integrity(paths)
    result = read(paths.reference / "evaluation_summary.json")
    benchmark = read(paths.root / "docs/references/frozen_v4_benchmark.json")
    destination = paths.root / "reports/latest"
    destination.mkdir(parents=True, exist_ok=True)
    primary = result["continuous"]["10bps"]["v6_selected"]["summary"]
    average = result["results"]["10bps"]["v6_selected"]["aggregate"]["mean_net_return"]
    curve = load_gzip(paths.reference / "evaluation/continuous/10bps/v6_selected.json.gz")[
        "ledger"
    ]["curve"]
    dates = pd.to_datetime([point["date"] for point in curve])
    figure, axis = plt.subplots(figsize=(11, 4.2), layout="constrained")
    figure.set_facecolor("#f7f9fc")
    axis.set_facecolor("#f7f9fc")
    axis.plot(
        dates,
        [point["nav"] / 1e6 for point in curve],
        color="#185adb",
        linewidth=2,
        label="V6 / matched momentum",
    )
    old_curve = benchmark["10bps"]["curve"]
    axis.plot(
        pd.to_datetime([point["date"] for point in old_curve]),
        [point["nav"] / 1e6 for point in old_curve],
        color="#a45b00",
        linewidth=1.5,
        alpha=0.8,
        label="Frozen V4 (no recovery)",
    )
    axis.axhline(1, color="#7d8794", linewidth=0.8)
    axis.set_ylabel("Net asset value / initial capital")
    axis.set_title("Continuous account · 10 bp slippage per side · historical replay", loc="left")
    axis.grid(alpha=0.18)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, loc="upper left")
    figure.savefig(destination / "equity_curve.png", dpi=160)
    plt.close(figure)
    rows = []
    for cost in ("10bps", "20bps", "30bps"):
        current = result["continuous"][cost]["v6_selected"]["summary"]
        momentum = result["continuous"][cost]["matched_momentum"]["summary"]
        old = benchmark[cost]["continuous"]
        episode = result["results"][cost]["v6_selected"]["aggregate"]
        rows.append(
            f"<tr><td>{cost}</td><td>{current['return']:.2%}</td>"
            f"<td>{momentum['return']:.2%}</td><td>{old['return']:.2%}</td>"
            f"<td>{current['max_drawdown']:.2%}</td>"
            f"<td>{episode['mean_net_return']:.2%}</td></tr>"
        )
    limitations = "".join(
        f"<li>{escape(item)}</li>"
        for item in [
            "V6与同风控动量的主策略结果相同，没有证明新增选股模型带来超额收益。",
            "高成本档连续账户亏损，收益优势门槛仍未通过。20bp结果高于10bp是成本改变成交与后续持仓路径所致，不能解释为成本越高越好。",
            "历史区间已被多轮研究使用；股票池和行业分类不是认证的历史时点数据。",
            "8%是回撤预警及下个可交易时点的退出目标，不能保证实际回撤永不超过8%。",
            "当前数据截止2026-11-18；候选股与历史决策均为研究记录，未生成或执行新的交易指令。",
        ]
    )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quantfolio · V6 研究结果</title><style>
:root{{color-scheme:light;font-family:system-ui,"Microsoft YaHei",sans-serif;color:#1c2c45;background:#f2f5f9}}
body{{margin:0}}main{{max-width:1120px;margin:auto;padding:42px 24px 64px}}a{{color:#185adb}}h1{{font-size:38px;margin:10px 0}}
.eyebrow{{letter-spacing:.14em;font-size:12px;color:#5e6f87}}.muted{{color:#52657e;line-height:1.7}}
.notice{{border-left:4px solid #ad6600;padding:14px 18px;background:#fff4df;margin:24px 0;line-height:1.7}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.card,section{{background:white;border:1px solid #dce3ed;border-radius:12px;padding:22px}}
.card span{{display:block;color:#52657e;font-size:13px}}.card strong{{display:block;font-size:29px;margin:10px 0 0}}
section{{margin-top:22px}}h2{{font-size:19px;margin-top:0}}img{{display:block;width:100%;height:auto}}
.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:14px 10px;text-align:right;border-bottom:1px solid #e4e9f0;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}
li{{margin:10px 0;line-height:1.7}}footer{{margin-top:24px;color:#52657e;font-size:13px;line-height:1.8}}
@media(max-width:700px){{main{{padding:24px 14px}}h1{{font-size:29px}}.cards{{grid-template-columns:repeat(2,1fr)}}.card{{padding:16px}}}}
</style></head><body><main>
<div class="eyebrow">QUANTFOLIO / RESEARCH / V6</div><h1>单一版本，完整证据</h1>
<p class="muted">均衡进攻 · 8%回撤预警 · 非恢复仓位机制<br>
连续账户区间：{result["trade_start"]} — {result["trade_end"]} ｜ 数据截止：2026-11-18</p>
<div class="notice"><strong>研究状态：收益优势验证未通过</strong><br>保留V6的依据是既有主策略的比较结果。文件整理与精确复现不会增加新的收益证据。</div>
<div class="cards"><div class="card"><span>连续账户净收益 · 10bp</span><strong>{primary["return"]:.2%}</strong></div>
<div class="card"><span>最大回撤 · 10bp</span><strong>{primary["max_drawdown"]:.2%}</strong></div>
<div class="card"><span>10日独立账户平均净收益</span><strong>{average:.2%}</strong></div>
<div class="card"><span>完整外层窗口 / 10日账户</span><strong>11 / 23</strong></div></div>
<section><h2>连续账户净值</h2><img src="equity_curve.png" alt="V6与同风控动量曲线重合，和冻结V4的历史净值比较">
<p class="muted">10日账户会重置本金；它们的平均收益不能直接连乘成连续账户收益。V4仅保留冻结比较数据。</p></section>
<section><h2>成本敏感性与基准</h2><div class="scroll"><table><thead><tr><th>每边滑点</th><th>V6连续净收益</th><th>同风控动量</th><th>冻结V4</th><th>V6最大回撤</th><th>V6平均10日</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<p class="muted">另计模型中的佣金、过户费、印花税及最低佣金。各成本档独立重放，成交和持仓路径可能不同。</p></section>
<section><h2>结论的边界</h2><ul>{limitations}</ul></section>
<section><h2>复现与查阅</h2><p><a href="../../README.md">项目说明</a> · <a href="../../docs/reproducibility.md">复现指南</a> · <a href="../../docs/maintenance/reorganization.md">整理记录</a></p>
<p><a href="../../artifacts/v6_2026-11-19/evaluation_summary.json">完整回测结果</a> · <a href="../../docs/references/version_selection.json">版本保留依据</a> · <a href="../../docs/references/frozen_v4_benchmark.json">冻结V4基准</a></p></section>
<footer>本报告由 quantfolio report 从本地冻结产物生成。无交易APP、微信或券商连接。</footer>
</main></body></html>"""
    (destination / "index.html").write_text(document, "utf-8", newline="\n")
    return {
        "status": "BUILT",
        "decision": "RESEARCH_ONLY",
        "report": str(destination / "index.html"),
    }
