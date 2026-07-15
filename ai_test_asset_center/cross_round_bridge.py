"""Cross-Round Integration Bridge — Connects benchmark metrics, gap resolution,
and learning generation into a unified feedback loop.

This module is intentionally thin. It wires together the three rounds:
  Round 1 (Benchmark) → Round 2 (Gap Resolution) → Round 3 (Learning)
  
Specifically:
  1. Low recall in a risk_type → boosts learning priority for that type
  2. Gap resolved for a defect family → triggers probe generation for that family
  3. Learning generation output → feeds back into benchmark as new probe candidates
  
Design: callbacks are registered here, not hardcoded into individual modules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


# ═════════════════════════════════════════════════════════════════════════════
# Priority Signals
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class PrioritySignal:
    """A learning priority boost signal derived from benchmark metrics."""
    risk_type: str
    priority_boost: float          # 0.0-1.0, added to learning weight
    reason: str
    source: str                    # "benchmark_recall_gap" | "gap_resolved" | "manual"
    generated_at: str = ""


# ═════════════════════════════════════════════════════════════════════════════
# Integration Bridge
# ═════════════════════════════════════════════════════════════════════════════

class CrossRoundBridge:
    """Wires benchmark → gap → learning into a closed feedback loop.

    Usage::

        bridge = CrossRoundBridge(thresholds={"critical_recall": 0.3, "low_recall": 0.5})
        signals = bridge.derive_priority_signals_from_benchmark(metrics)
    """

    # Default thresholds — can be overridden per industry
    DEFAULT_THRESHOLDS = {
        "critical_recall": 0.3,     # recall below this → critical boost
        "low_recall": 0.5,           # recall below this → significant boost
        "moderate_recall": 0.7,      # recall below this → moderate boost
        "critical_boost": 0.4,
        "significant_boost": 0.25,
        "moderate_boost": 0.1,
        "high_value_recall_threshold": 0.5,
        "high_value_boost": 0.3,
        "evidence_recall_threshold": 0.3,
        "evidence_boost": 0.2,
    }

    def __init__(self, thresholds: dict[str, float] | None = None) -> None:
        self._priority_boosts: dict[str, list[PrioritySignal]] = {}
        self._resolved_families: list[str] = []
        self._log: list[str] = []
        self._hooks: dict[str, list[Callable]] = {
            "on_gap_resolved": [],
            "on_benchmark_complete": [],
            "on_learning_generated": [],
        }
        # Merge custom thresholds with defaults
        self.thresholds = dict(self.DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)

    # ── Priority Derivation from Benchmark ─────────────────────────────

    def derive_priority_signals_from_benchmark(
        self,
        metrics: dict[str, Any],
    ) -> list[PrioritySignal]:
        """Analyze benchmark metrics and produce priority signals.

        Low recall in a risk_type → boost learning priority for that type.
        """
        signals: list[PrioritySignal] = []
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Check risk_type_breakdown for low-recall types
        risk_breakdown = metrics.get("risk_type_breakdown", {})
        for risk_type, info in risk_breakdown.items():
            if not isinstance(info, dict):
                continue
            total = info.get("total", 0)
            detected = info.get("detected", 0)
            recall = info.get("recall", 0.0)

            if total == 0:
                continue

            # Compute priority boost based on recall gap
            t = self.thresholds
            if recall < t["critical_recall"]:
                boost = t["critical_boost"]
                reason = f"Very low recall ({recall:.0%}) for {risk_type}: {detected}/{total} detected"
            elif recall < t["low_recall"]:
                boost = t["significant_boost"]
                reason = f"Low recall ({recall:.0%}) for {risk_type}: {detected}/{total} detected"
            elif recall < t["moderate_recall"]:
                boost = t["moderate_boost"]
                reason = f"Moderate recall ({recall:.0%}) for {risk_type}: {detected}/{total} detected"
            else:
                continue  # Good recall, no boost needed

            signal = PrioritySignal(
                risk_type=risk_type,
                priority_boost=boost,
                reason=reason,
                source="benchmark_recall_gap",
                generated_at=timestamp,
            )
            signals.append(signal)

            # Accumulate
            self._priority_boosts.setdefault(risk_type, []).append(signal)

        # Check high_value_recall for P0/P1 gaps
        high_recall = metrics.get("high_value_recall", 1.0)
        if isinstance(high_recall, (int, float)) and high_recall < self.thresholds["high_value_recall_threshold"]:
            signal = PrioritySignal(
                risk_type="*",
                priority_boost=self.thresholds["high_value_boost"],
                reason=f"High-value (P0+P1) recall is low: {high_recall:.0%}",
                source="benchmark_recall_gap",
                generated_at=timestamp,
            )
            signals.append(signal)
            self._priority_boosts.setdefault("*", []).append(signal)

        # Check evidence_weighted_recall
        ev_recall = metrics.get("evidence_weighted_recall", 1.0)
        if isinstance(ev_recall, (int, float)) and ev_recall < self.thresholds["evidence_recall_threshold"]:
            signal = PrioritySignal(
                risk_type="*",
                priority_boost=self.thresholds["evidence_boost"],
                reason=f"Evidence-weighted recall is very low: {ev_recall:.0%}. Generate more runtime probes.",
                source="benchmark_recall_gap",
                generated_at=timestamp,
            )
            signals.append(signal)
            self._priority_boosts.setdefault("*", []).append(signal)

        self._log.append(
            f"derive_priority_signals: {len(signals)} signals from benchmark metrics"
        )

        # Fire hooks
        for hook in self._hooks["on_benchmark_complete"]:
            try:
                hook(metrics, signals)
            except Exception:
                pass

        return signals

    # ── Gap Resolution → Learning ──────────────────────────────────────

    def on_gap_resolved(self, defect_family: str) -> list[PrioritySignal]:
        """Called when a capability gap is resolved for a defect family.

        Generates priority signals so the learning generator focuses on
        the newly-unblocked family.
        """
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        signal = PrioritySignal(
            risk_type=defect_family,
            priority_boost=0.35,
            reason=f"Capability gap resolved for {defect_family} — generate probes for this family",
            source="gap_resolved",
            generated_at=timestamp,
        )
        self._priority_boosts.setdefault(defect_family, []).append(signal)
        self._resolved_families.append(defect_family)

        self._log.append(f"on_gap_resolved: {defect_family}")

        # Fire hooks
        for hook in self._hooks["on_gap_resolved"]:
            try:
                hook(defect_family, signal)
            except Exception:
                pass

        return [signal]

    def on_gaps_resolved_batch(
        self, defect_families: list[str]
    ) -> list[PrioritySignal]:
        """Batch version of on_gap_resolved."""
        signals: list[PrioritySignal] = []
        for family in defect_families:
            signals.extend(self.on_gap_resolved(family))
        return signals

    # ── Learning → Benchmark Feedback ──────────────────────────────────

    def on_learning_generated(
        self,
        generated_counts: dict[str, int],
    ) -> None:
        """Called after learning generates new artifacts.

        Records what was generated so future benchmark runs can measure
        whether the generated probes improved recall.
        """
        self._log.append(
            f"on_learning_generated: "
            f"probes={generated_counts.get('probes', 0)}, "
            f"oracles={generated_counts.get('oracles', 0)}, "
            f"fixtures={generated_counts.get('fixtures', 0)}"
        )
        for hook in self._hooks["on_learning_generated"]:
            try:
                hook(generated_counts)
            except Exception:
                pass

    # ── Query ──────────────────────────────────────────────────────────

    def get_learning_priority_boosts(
        self, risk_type: str | None = None
    ) -> dict[str, float]:
        """Get accumulated priority boosts, optionally filtered by risk_type.

        Returns {risk_type: total_boost} dict.
        """
        result: dict[str, float] = {}
        for rt, signals in self._priority_boosts.items():
            if risk_type and rt != risk_type and rt != "*":
                continue
            total = sum(s.priority_boost for s in signals)
            result[rt] = round(min(total, 1.0), 4)  # Cap at 1.0
        return result

    def get_resolved_families(self) -> list[str]:
        """Return list of defect families that had gaps resolved."""
        return list(self._resolved_families)

    def get_log(self) -> list[str]:
        return list(self._log)

    # ── Hook Registration ──────────────────────────────────────────────

    def register_hook(self, event: str, callback: Callable) -> None:
        """Register a callback for lifecycle events.

        Events: "on_gap_resolved", "on_benchmark_complete", "on_learning_generated"
        """
        if event in self._hooks:
            self._hooks[event].append(callback)

    # ── Closed-Loop Check ──────────────────────────────────────────────

    def build_closed_loop_summary(self) -> dict[str, Any]:
        """Build a summary of the cross-round feedback loop state."""
        return {
            "schema_version": "cross_round_bridge.v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "priority_signals_count": sum(len(v) for v in self._priority_boosts.values()),
            "resolved_families": self._resolved_families,
            "learning_priority_boosts": self.get_learning_priority_boosts(),
            "log_entries": len(self._log),
        }
