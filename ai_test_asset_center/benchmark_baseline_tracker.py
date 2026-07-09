"""Benchmark Baseline Tracker — Honest, regression-aware metrics history.

Design principles (per AGENTS.md):
  - No fake data: every snapshot is derived from a real benchmark run
  - Observable: every operation logs to a structured JSON ledger
  - Pure JSON output: no HTML beautification — this feeds into the Round 5 dashboard

Usage::

    tracker = BenchmarkBaselineTracker("ecommerce")
    snapshot = tracker.record_run(metrics)
    delta = tracker.compare_last_two()
    regressions = tracker.detect_regressions()
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═════════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════════

BASELINE_VERSION = "baseline_tracker.v1"
MAX_HISTORY_ENTRIES = 200
DEFAULT_REGRESSION_THRESHOLD = 0.05  # 5% absolute drop = regression alert


# Key metrics that MUST be present in every snapshot
REQUIRED_METRIC_KEYS = frozenset({
    "recall", "precision", "f1_score", "false_positive_rate",
    "evidence_completeness_rate",
})

# Metric display order for reports
METRIC_ORDER = [
    "recall", "precision", "f1_score", "false_positive_rate",
    "high_value_recall", "evidence_completeness_rate",
    "reproduction_success_rate", "regression_success_rate",
]


@dataclass
class BaselineSnapshot:
    """One benchmark run's worth of metrics."""
    run_id: str
    timestamp: str
    industry: str
    metrics: dict[str, Any]
    ground_truth_bug_count: int = 0
    scan_findings_total: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0


# ═════════════════════════════════════════════════════════════════════════════
# Baseline Tracker
# ═════════════════════════════════════════════════════════════════════════════

class BenchmarkBaselineTracker:
    """Track benchmark metrics over time with regression detection.

    All data is stored in platform_outputs/_benchmark/ for cross-project access.
    """

    def __init__(
        self,
        industry: str = "default",
        *,
        root: str | Path | None = None,
        regression_threshold: float = DEFAULT_REGRESSION_THRESHOLD,
    ) -> None:
        self.industry = industry
        self.regression_threshold = regression_threshold

        if root is None:
            root = Path(os.environ.get(
                "QUALIBUG_WORKSPACE_ROOT",
                str(Path(__file__).resolve().parents[1])
            ))
        self.root = Path(root)

        # Storage paths
        self._baseline_dir = self.root / "platform_outputs" / "_benchmark"
        self._baseline_dir.mkdir(parents=True, exist_ok=True)
        self._history_path = self._baseline_dir / f"baseline_history_{industry}.json"
        self._ledger_path = self._baseline_dir / f"baseline_ledger_{industry}.jsonl"

    # ── Recording ───────────────────────────────────────────────────────

    def record_run(
        self,
        metrics: dict[str, Any],
        *,
        run_id: str | None = None,
        ground_truth_bug_count: int = 0,
        scan_findings_total: int = 0,
        true_positives: int = 0,
        false_positives: int = 0,
        false_negatives: int = 0,
    ) -> BaselineSnapshot:
        """Record one benchmark run and append to history.

        Args:
            metrics: Dict from benchmark_evaluator.metrics.compute_metrics()
                     or benchmark_compute.compute_benchmark().
            run_id: Optional identifier. Auto-generated if omitted.

        Returns:
            The recorded BaselineSnapshot.
        """
        if run_id is None:
            run_id = f"{self.industry}_{int(time.time() * 1000)}"

        snapshot = BaselineSnapshot(
            run_id=run_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            industry=self.industry,
            metrics=self._normalize_metrics(metrics),
            ground_truth_bug_count=ground_truth_bug_count,
            scan_findings_total=scan_findings_total,
            true_positives=true_positives,
            false_positives=false_positives,
            false_negatives=false_negatives,
        )

        # Append to history
        history = self._load_history()
        history.append(self._snapshot_to_dict(snapshot))

        # Trim to max entries
        if len(history) > MAX_HISTORY_ENTRIES:
            history = history[-MAX_HISTORY_ENTRIES:]

        self._history_path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Append to ledger (append-only JSONL)
        ledger_entry = {
            "event": "run_recorded",
            "run_id": run_id,
            "timestamp": snapshot.timestamp,
            "industry": self.industry,
            "metric_keys": sorted(snapshot.metrics.keys()),
        }
        with open(self._ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ledger_entry, ensure_ascii=False) + "\n")

        return snapshot

    def _normalize_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Extract and normalize the key metrics from a metrics payload."""
        result: dict[str, Any] = {}
        for key in METRIC_ORDER:
            if key in metrics:
                val = metrics[key]
                if isinstance(val, (int, float)):
                    result[key] = round(float(val), 4)
                else:
                    result[key] = val

        # Also pull in any per-risk-type breakdown
        if "risk_family_breakdown" in metrics:
            result["risk_family_breakdown"] = metrics["risk_family_breakdown"]
        if "bug_type_breakdown" in metrics:
            result["bug_type_breakdown"] = metrics["bug_type_breakdown"]

        # Ensure required keys exist
        for key in REQUIRED_METRIC_KEYS:
            if key not in result:
                result[key] = None

        return result

    # ── Comparison ──────────────────────────────────────────────────────

    def compare_last_two(self) -> dict[str, Any]:
        """Compare the two most recent runs and return a delta report.

        Returns:
            Dict with 'previous', 'current', and 'delta' for each metric.
            Returns empty dict if fewer than 2 runs exist.
        """
        history = self._load_history()
        if len(history) < 2:
            return {
                "available": False,
                "reason": f"Need at least 2 runs, have {len(history)}",
                "run_count": len(history),
            }

        prev = history[-2]
        curr = history[-1]

        prev_metrics = prev.get("metrics", {})
        curr_metrics = curr.get("metrics", {})

        delta: dict[str, dict[str, Any]] = {}
        for key in METRIC_ORDER:
            prev_val = prev_metrics.get(key)
            curr_val = curr_metrics.get(key)
            if isinstance(prev_val, (int, float)) and isinstance(curr_val, (int, float)):
                delta[key] = {
                    "previous": round(float(prev_val), 4),
                    "current": round(float(curr_val), 4),
                    "delta": round(float(curr_val) - float(prev_val), 4),
                    "direction": "improved" if float(curr_val) > float(prev_val) else (
                        "regressed" if float(curr_val) < float(prev_val) else "unchanged"
                    ),
                }
            else:
                delta[key] = {
                    "previous": prev_val,
                    "current": curr_val,
                    "delta": None,
                    "direction": "non_numeric",
                }

        return {
            "available": True,
            "previous_run_id": prev.get("run_id"),
            "current_run_id": curr.get("run_id"),
            "previous_timestamp": prev.get("timestamp"),
            "current_timestamp": curr.get("timestamp"),
            "delta": delta,
        }

    def compare_to_baseline(
        self, baseline_index: int = 0
    ) -> dict[str, Any]:
        """Compare the most recent run to a specific baseline (default: first run)."""
        history = self._load_history()
        if len(history) < 2:
            return {
                "available": False,
                "reason": f"Need at least 2 runs, have {len(history)}",
            }

        if baseline_index < 0 or baseline_index >= len(history) - 1:
            return {
                "available": False,
                "reason": f"baseline_index {baseline_index} out of range [0, {len(history) - 2}]",
            }

        baseline = history[baseline_index]
        current = history[-1]

        baseline_metrics = baseline.get("metrics", {})
        current_metrics = current.get("metrics", {})

        delta: dict[str, dict[str, Any]] = {}
        for key in METRIC_ORDER:
            b_val = baseline_metrics.get(key)
            c_val = current_metrics.get(key)
            if isinstance(b_val, (int, float)) and isinstance(c_val, (int, float)):
                delta[key] = {
                    "baseline": round(float(b_val), 4),
                    "current": round(float(c_val), 4),
                    "delta": round(float(c_val) - float(b_val), 4),
                    "pct_change": round(
                        (float(c_val) - float(b_val)) / max(abs(float(b_val)), 0.0001), 4
                    ),
                }
            else:
                delta[key] = {
                    "baseline": b_val,
                    "current": c_val,
                    "delta": None,
                    "pct_change": None,
                }

        return {
            "available": True,
            "baseline_run_id": baseline.get("run_id"),
            "current_run_id": current.get("run_id"),
            "total_runs": len(history),
            "delta": delta,
        }

    # ── Regression Detection ────────────────────────────────────────────

    def detect_regressions(self) -> dict[str, Any]:
        """Compare the last two runs and flag any regressions.

        A regression is defined as an absolute drop >= regression_threshold
        in any tracked metric (higher is better for recall, precision, F1;
        lower is better for false_positive_rate).

        Returns:
            Dict with 'has_regressions' (bool) and 'regressions' (list of details).
        """
        comparison = self.compare_last_two()
        if not comparison.get("available"):
            return {
                "has_regressions": False,
                "checked": False,
                "reason": comparison.get("reason", "no data"),
            }

        delta = comparison.get("delta", {})
        regressions: list[dict[str, Any]] = []

        # Metrics where higher is better
        higher_better = {"recall", "precision", "f1_score", "high_value_recall",
                         "evidence_completeness_rate", "reproduction_success_rate",
                         "regression_success_rate"}

        # Metrics where lower is better
        lower_better = {"false_positive_rate"}

        for key, d in delta.items():
            if d.get("direction") != "regressed":
                continue
            abs_drop = abs(d.get("delta", 0))
            if abs_drop >= self.regression_threshold:
                regressions.append({
                    "metric": key,
                    "previous": d["previous"],
                    "current": d["current"],
                    "delta": d["delta"],
                    "threshold": self.regression_threshold,
                    "severity": "critical" if abs_drop >= self.regression_threshold * 3 else "warning",
                })

        # Also check false_positive_rate (higher is worse)
        fpr = delta.get("false_positive_rate", {})
        if isinstance(fpr.get("delta"), (int, float)) and fpr["delta"] > self.regression_threshold:
            regressions.append({
                "metric": "false_positive_rate",
                "previous": fpr["previous"],
                "current": fpr["current"],
                "delta": fpr["delta"],
                "threshold": self.regression_threshold,
                "severity": "critical" if fpr["delta"] >= self.regression_threshold * 3 else "warning",
            })

        return {
            "has_regressions": len(regressions) > 0,
            "checked": True,
            "regression_count": len(regressions),
            "regressions": regressions,
            "previous_run_id": comparison.get("previous_run_id"),
            "current_run_id": comparison.get("current_run_id"),
        }

    # ── History Access ──────────────────────────────────────────────────

    def get_history(self, last_n: int = 0) -> list[dict[str, Any]]:
        """Return the full history or the last N entries."""
        history = self._load_history()
        if last_n > 0:
            return history[-last_n:]
        return history

    def get_latest(self) -> dict[str, Any] | None:
        """Return the most recent snapshot, or None."""
        history = self._load_history()
        return history[-1] if history else None

    def get_run_count(self) -> int:
        """Return the total number of recorded runs."""
        return len(self._load_history())

    # ── Trend Summary ───────────────────────────────────────────────────

    def trend_summary(self, last_n: int = 10) -> dict[str, Any]:
        """Compute trend direction over the last N runs.

        Returns a simple 'improving' / 'stable' / 'declining' assessment
        for each key metric based on linear trend direction.
        """
        history = self._load_history()
        if len(history) < 2:
            return {"available": False, "reason": "Need at least 2 runs for trend"}

        recent = history[-min(last_n, len(history)):]

        trends: dict[str, dict[str, Any]] = {}
        for key in METRIC_ORDER:
            values = []
            for snap in recent:
                val = snap.get("metrics", {}).get(key)
                if isinstance(val, (int, float)):
                    values.append(float(val))

            if len(values) < 2:
                trends[key] = {"direction": "insufficient_data", "values": values}
                continue

            # Simple linear trend: compare first half avg vs second half avg
            mid = len(values) // 2
            first_half_avg = sum(values[:mid]) / mid
            second_half_avg = sum(values[mid:]) / (len(values) - mid)
            diff = second_half_avg - first_half_avg

            if abs(diff) < self.regression_threshold / 2:
                direction = "stable"
            elif diff > 0:
                direction = "improving"
            else:
                direction = "declining"

            trends[key] = {
                "direction": direction,
                "first_half_avg": round(first_half_avg, 4),
                "second_half_avg": round(second_half_avg, 4),
                "delta": round(diff, 4),
                "sample_count": len(values),
            }

        return {
            "available": True,
            "total_runs": len(history),
            "analyzed_runs": len(recent),
            "trends": trends,
        }

    # ── Internal ────────────────────────────────────────────────────────

    def _load_history(self) -> list[dict[str, Any]]:
        if not self._history_path.exists():
            return []
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8") or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @staticmethod
    def _snapshot_to_dict(snapshot: BaselineSnapshot) -> dict[str, Any]:
        return {
            "run_id": snapshot.run_id,
            "timestamp": snapshot.timestamp,
            "industry": snapshot.industry,
            "metrics": snapshot.metrics,
            "ground_truth_bug_count": snapshot.ground_truth_bug_count,
            "scan_findings_total": snapshot.scan_findings_total,
            "true_positives": snapshot.true_positives,
            "false_positives": snapshot.false_positives,
            "false_negatives": snapshot.false_negatives,
        }
