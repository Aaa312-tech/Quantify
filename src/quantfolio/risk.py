"""Latched and registered research risk policies with an unchanged account high-water mark."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, TypedDict

CAPS = {"NORMAL": 0.9, "CAUTION45": 0.45, "FROZEN20": 0.2, "HALTED0": 0.0, "PROBE20": 0.2}
LEVELS = {"NORMAL": 0, "CAUTION45": 1, "FROZEN20": 2, "HALTED0": 3, "PROBE20": 2}
RISK_MODES = ("latched", "staged_restore", "probe_restore")


class RiskSnapshot(TypedDict):
    mode: str
    phase: str
    peak: float
    age: int
    healthy_streak: int
    probe_anchor: float | None
    observations: int


class _PreviousRisk(TypedDict):
    state_age_before: int
    healthy_streak_before: int
    previous_phase: str
    probe_anchor_before: float | None


class RiskDecision(_PreviousRisk):
    phase: str
    level: int
    cap: float
    can_buy: bool
    changed: bool
    reason: str
    peak: float
    drawdown: float
    state_age: int
    healthy_streak: int
    probe_anchor: float | None
    healthy: bool
    mode: str


@dataclass
class RiskState:
    """Portfolio drawdown state; the default policy latches risk reductions."""

    mode: str = "latched"
    phase: str = "NORMAL"
    peak: float = 1_000_000.0
    age: int = 0
    healthy_streak: int = 0
    probe_anchor: float | None = None
    observations: int = 0

    def update(self, nav: float, healthy: bool) -> RiskDecision:
        """Advance risk state from preceding-close NAV."""
        if self.mode not in RISK_MODES:
            raise ValueError("Unknown risk mode")
        if self.phase not in CAPS or (self.phase == "PROBE20" and self.mode != "probe_restore"):
            raise ValueError("Risk phase is not supported by this mode")
        if not math.isfinite(nav) or nav <= 0:
            raise ValueError("Invalid preceding-close NAV")
        self.peak = max(self.peak, float(nav))
        drawdown = 1 - nav / self.peak
        self.age += int(self.observations > 0)
        self.observations += 1
        self.healthy_streak = self.healthy_streak + 1 if healthy else 0
        previous = self.phase
        next_phase, reason = self._transition(drawdown)
        evidence: _PreviousRisk = {
            "state_age_before": self.age,
            "healthy_streak_before": self.healthy_streak,
            "previous_phase": previous,
            "probe_anchor_before": self.probe_anchor,
        }
        if next_phase != previous:
            self.phase = next_phase
            self.age = 0
            self.healthy_streak = 0
            self.probe_anchor = float(nav) if next_phase == "PROBE20" else None
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

    def _transition(self, drawdown):
        """Select the next phase without changing the current state."""
        previous = self.phase
        if previous == "HALTED0":
            return previous, "UNCHANGED"
        if drawdown >= 0.08 - 1e-12:
            return "HALTED0", "GLOBAL_DRAWDOWN_8"
        if drawdown >= 0.06 - 1e-12 and LEVELS[previous] < 2:
            return "FROZEN20", "GLOBAL_DRAWDOWN_6"
        if drawdown >= 0.04 - 1e-12 and LEVELS[previous] < 1:
            return "CAUTION45", "GLOBAL_DRAWDOWN_4"
        if previous == "PROBE20" and drawdown >= 0.07 - 1e-12:
            return "FROZEN20", "PROBE_DRAWDOWN_7"
        if self.mode != "latched" and self.age >= 5 and self.healthy_streak >= 3:
            if previous in ("FROZEN20", "PROBE20") and drawdown < 0.05 - 1e-12:
                return "CAUTION45", "RESTORE_BELOW_5"
            if previous == "CAUTION45" and drawdown < 0.025 - 1e-12:
                return "NORMAL", "RESTORE_BELOW_2_5"
            if (
                self.mode == "probe_restore"
                and previous == "FROZEN20"
                and self.age >= 10
                and self.healthy_streak >= 5
                and drawdown < 0.07 - 1e-12
            ):
                return "PROBE20", "PROBE_AFTER_HEALTHY_MARKET"
        return previous, "UNCHANGED"


def healthy_market(panels: dict, date: Any) -> bool:
    """Evaluate breadth and benchmark trend at the completed session close."""
    eligible = panels["eligible"].loc[date]
    breadth = float(((panels["trend20"].loc[date] > 0) & eligible).sum() / max(1, eligible.sum()))
    above = panels["trend20"].at[date, "sh510300"] > 0
    closes = panels["close"].loc[:date, "sh510300"].dropna()
    positive = len(closes) >= 6 and closes.iloc[-1] / closes.iloc[-6] - 1 > 0
    return bool(above and breadth >= 0.55 and positive)
