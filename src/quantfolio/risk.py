"""Non-recovering drawdown states; the original account high-water mark never resets."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

CAPS = {"NORMAL": 0.9, "CAUTION45": 0.45, "FROZEN20": 0.2, "HALTED0": 0.0}
LEVELS = {"NORMAL": 0, "CAUTION45": 1, "FROZEN20": 2, "HALTED0": 3}


@dataclass
class RiskState:
    """Latch exposure down at 4%, 6% and 8% drawdown; never automatically restore it."""

    mode: str = "latched"
    phase: str = "NORMAL"
    peak: float = 1_000_000.0
    age: int = 0
    healthy_streak: int = 0
    probe_anchor: float | None = None
    observations: int = 0

    def update(self, nav: float, healthy: bool) -> dict[str, Any]:
        """Use preceding-close NAV, preserving the frozen ledger evidence schema."""
        if self.mode != "latched":
            raise ValueError("Only the retained non-recovering risk policy is supported")
        if not math.isfinite(nav) or nav <= 0:
            raise ValueError("Invalid preceding-close NAV")
        self.peak = max(self.peak, float(nav))
        drawdown = 1 - nav / self.peak
        self.age += int(self.observations > 0)
        self.observations += 1
        self.healthy_streak = self.healthy_streak + 1 if healthy else 0
        previous = self.phase
        next_phase, reason = previous, "UNCHANGED"
        if previous == "HALTED0":
            pass
        elif drawdown >= 0.08 - 1e-12:
            next_phase, reason = "HALTED0", "GLOBAL_DRAWDOWN_8"
        elif drawdown >= 0.06 - 1e-12 and LEVELS[previous] < 2:
            next_phase, reason = "FROZEN20", "GLOBAL_DRAWDOWN_6"
        elif drawdown >= 0.04 - 1e-12 and LEVELS[previous] < 1:
            next_phase, reason = "CAUTION45", "GLOBAL_DRAWDOWN_4"
        evidence = {
            "state_age_before": self.age,
            "healthy_streak_before": self.healthy_streak,
            "previous_phase": previous,
            "probe_anchor_before": self.probe_anchor,
        }
        if next_phase != previous:
            self.phase = next_phase
            self.age = 0
            self.healthy_streak = 0
            self.probe_anchor = None
        return {
            **evidence,
            "phase": self.phase,
            "level": LEVELS[self.phase],
            "cap": CAPS[self.phase],
            "can_buy": self.phase not in ("FROZEN20", "HALTED0"),
            "changed": next_phase != previous,
            "reason": reason,
            "peak": self.peak,
            "drawdown": drawdown,
            "state_age": self.age,
            "healthy_streak": self.healthy_streak,
            "probe_anchor": self.probe_anchor,
            "healthy": bool(healthy),
            "mode": self.mode,
        }


def healthy_market(panels: dict, date: Any) -> bool:
    """Evaluate only the completed signal-date close, retained as diagnostic evidence."""
    eligible = panels["eligible"].loc[date]
    breadth = float(((panels["trend20"].loc[date] > 0) & eligible).sum() / max(1, eligible.sum()))
    above = panels["trend20"].at[date, "sh510300"] > 0
    closes = panels["close"].loc[:date, "sh510300"].dropna()
    positive = len(closes) >= 6 and closes.iloc[-1] / closes.iloc[-6] - 1 > 0
    return bool(above and breadth >= 0.55 and positive)
