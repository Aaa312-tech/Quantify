"""Build a self-contained local research report from immutable experiment evidence."""

from __future__ import annotations

from importlib.resources import files
from string import Template

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
    template = files("quantfolio").joinpath("templates/report.html").read_text("utf-8")
    document = Template(template).substitute(
        trade_start=result["trade_start"],
        trade_end=result["trade_end"],
        net_return=f"{primary['return']:.2%}",
        drawdown=f"{primary['max_drawdown']:.2%}",
        average=f"{average:.2%}",
        rows="".join(rows),
    )
    (destination / "index.html").write_text(document, "utf-8", newline="\n")
    return {
        "status": "BUILT",
        "decision": "RESEARCH_ONLY",
        "report": str(destination / "index.html"),
    }
