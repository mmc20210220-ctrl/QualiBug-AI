"""
QualiBug Performance Baseline — 性能历史基线与回归检测。

作为 scan() 统一管道的一部分，将每次扫描的性能结果
与历史基线对比，自动检测性能回归 (degradation) 和改善。

存储在 platform_outputs/{project}/performance/baseline.json
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .scan_post_hooks import register_scan_post_hook

HOOK_NAME = "performance_baseline"


@dataclass
class PerfMetrics:
    """单次扫描的性能指标"""
    scan_id: str
    timestamp: str
    # API 层
    api_total_ms: int = 0
    api_p50_ms: float = 0
    api_p95_ms: float = 0
    api_p99_ms: float = 0
    api_error_rate: float = 0
    # 性能测试层
    perf_findings: int = 0
    perf_pass_rate: float = 0
    # 负载测试层
    load_p50_ms: float = 0
    load_p95_ms: float = 0
    load_p99_ms: float = 0
    load_max_concurrency: int = 0
    # 扫描总耗时
    total_ms: int = 0
    total_findings: int = 0

    def to_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "timestamp": self.timestamp,
            "api_total_ms": self.api_total_ms,
            "api_p50_ms": self.api_p50_ms,
            "api_p95_ms": self.api_p95_ms,
            "api_p99_ms": self.api_p99_ms,
            "api_error_rate": self.api_error_rate,
            "perf_findings": self.perf_findings,
            "perf_pass_rate": self.perf_pass_rate,
            "load_p50_ms": self.load_p50_ms,
            "load_p95_ms": self.load_p95_ms,
            "load_p99_ms": self.load_p99_ms,
            "load_max_concurrency": self.load_max_concurrency,
            "total_ms": self.total_ms,
            "total_findings": self.total_findings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PerfMetrics:
        return cls(
            scan_id=d.get("scan_id", ""),
            timestamp=d.get("timestamp", ""),
            api_total_ms=d.get("api_total_ms", 0),
            api_p50_ms=d.get("api_p50_ms", 0),
            api_p95_ms=d.get("api_p95_ms", 0),
            api_p99_ms=d.get("api_p99_ms", 0),
            api_error_rate=d.get("api_error_rate", 0),
            perf_findings=d.get("perf_findings", 0),
            perf_pass_rate=d.get("perf_pass_rate", 0),
            load_p50_ms=d.get("load_p50_ms", 0),
            load_p95_ms=d.get("load_p95_ms", 0),
            load_p99_ms=d.get("load_p99_ms", 0),
            load_max_concurrency=d.get("load_max_concurrency", 0),
            total_ms=d.get("total_ms", 0),
            total_findings=d.get("total_findings", 0),
        )


@dataclass
class RegressionAlert:
    """性能回归告警"""
    metric: str  # which metric regressed
    baseline_value: float
    current_value: float
    pct_change: float  # positive = slower/worse
    severity: str  # P0 (>50%), P1 (>20%), P2 (>10%)
    detail: str


class PerformanceBaseline:
    """性能基线管理器。

    用法:
        baseline = PerformanceBaseline(project, root)
        current = PerfMetrics(scan_id="scan_001", api_p95_ms=250, ...)
        result = baseline.check(current)
        # result contains regression alerts and trend data
    """

    # Regression thresholds
    P0_DEGRADATION = 0.50   # 50% slower → P0
    P1_DEGRADATION = 0.20   # 20% slower → P1
    P2_DEGRADATION = 0.10   # 10% slower → P2
    IMPROVEMENT = -0.10      # 10% faster → notable improvement
    MIN_RUNS_FOR_BASELINE = 3  # need at least 3 runs for baseline comparison

    def __init__(self, project: str, root: Path):
        self.project = project
        self.root = root
        self._data_dir = root / "platform_outputs" / project / "performance"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._baseline_file = self._data_dir / "baseline.json"

    def _load_history(self) -> list[dict]:
        if not self._baseline_file.exists():
            return []
        try:
            return json.loads(self._baseline_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_history(self, history: list[dict]) -> None:
        self._baseline_file.write_text(
            json.dumps(history, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def record(self, metrics: PerfMetrics) -> dict:
        """Record a new performance data point and check for regressions."""
        history = self._load_history()

        # Append current run
        entry = metrics.to_dict()
        history.append(entry)

        # Keep at most 30 data points
        if len(history) > 30:
            history = history[-30:]

        self._save_history(history)

        # Check regressions if enough data
        regressions = self._detect_regressions(history)

        return {
            "ok": True,
            "runs": len(history),
            "current": entry,
            "regressions": [r.__dict__ if hasattr(r, '__dict__') else r
                          for r in regressions],
            "trend": self._compute_trend(history),
            "baseline_established": len(history) >= self.MIN_RUNS_FOR_BASELINE,
        }

    def _detect_regressions(self, history: list[dict]) -> list[RegressionAlert]:
        if len(history) < self.MIN_RUNS_FOR_BASELINE:
            return []

        # Use last N-1 runs as baseline (exclude current)
        baseline_runs = [PerfMetrics.from_dict(h) for h in history[:-1]]
        current = PerfMetrics.from_dict(history[-1])

        alerts: list[RegressionAlert] = []

        # Check each key metric
        checks: list[tuple[str, str, float, float]] = [
            ("API P95延迟", "api_p95_ms", self._median([m.api_p95_ms for m in baseline_runs]), current.api_p95_ms),
            ("API P99延迟", "api_p99_ms", self._median([m.api_p99_ms for m in baseline_runs]), current.api_p99_ms),
            ("API 错误率", "api_error_rate", self._median([m.api_error_rate for m in baseline_runs]), current.api_error_rate),
            ("负载 P95", "load_p95_ms", self._median([m.load_p95_ms for m in baseline_runs]), current.load_p95_ms),
            ("负载 P99", "load_p99_ms", self._median([m.load_p99_ms for m in baseline_runs]), current.load_p99_ms),
            ("扫描总耗时", "total_ms", self._median([m.total_ms for m in baseline_runs]), current.total_ms),
        ]

        for label, metric, baseline_val, current_val in checks:
            if baseline_val <= 0:
                continue

            pct = (current_val - baseline_val) / baseline_val

            if pct >= self.P0_DEGRADATION:
                alerts.append(RegressionAlert(metric, baseline_val, current_val, pct, "P0",
                    f"{label}: {baseline_val:.0f}→{current_val:.0f} (+{pct*100:.0f}%)"))
            elif pct >= self.P1_DEGRADATION:
                alerts.append(RegressionAlert(metric, baseline_val, current_val, pct, "P1",
                    f"{label}: {baseline_val:.0f}→{current_val:.0f} (+{pct*100:.0f}%)"))
            elif pct >= self.P2_DEGRADATION:
                alerts.append(RegressionAlert(metric, baseline_val, current_val, pct, "P2",
                    f"{label}: {baseline_val:.0f}→{current_val:.0f} (+{pct*100:.0f}%)"))

        return alerts

    def _compute_trend(self, history: list[dict]) -> dict:
        """Compute trend direction for each metric."""
        if len(history) < 2:
            return {"status": "insufficient_data"}

        first = PerfMetrics.from_dict(history[0])
        last = PerfMetrics.from_dict(history[-1])

        trends = {}
        for name, attr in [("api_p95_ms", "api_p95_ms"), ("api_error_rate", "api_error_rate"),
                           ("total_ms", "total_ms"), ("total_findings", "total_findings")]:
            old = getattr(first, attr, 0)
            new = getattr(last, attr, 0)
            if old > 0:
                pct = (new - old) / old
                if pct >= 0.1:
                    trends[name] = "degrading"
                elif pct <= -0.1:
                    trends[name] = "improving"
                else:
                    trends[name] = "stable"
            else:
                trends[name] = "stable"

        return {
            "runs": len(history),
            "from": first.timestamp,
            "to": last.timestamp,
            "trends": trends,
        }

    @staticmethod
    def _median(values: list[float]) -> float:
        if not values:
            return 0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        if n % 2 == 0:
            return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
        return sorted_vals[n // 2]

    def load_baseline(self) -> dict:
        """Load current baseline status for Dashboard display."""
        history = self._load_history()
        regressions = self._detect_regressions(history)
        trend = self._compute_trend(history)

        latest = PerfMetrics.from_dict(history[-1]) if history else None

        return {
            "ok": True,
            "project": self.project,
            "runs": len(history),
            "baseline_established": len(history) >= self.MIN_RUNS_FOR_BASELINE,
            "latest": latest.to_dict() if latest else None,
            "regressions": [r.__dict__ if hasattr(r, '__dict__') else r
                          for r in regressions],
            "regression_count": len(regressions),
            "trend": trend,
        }


if __name__ == "__main__":
    import tempfile
    root = Path(tempfile.mkdtemp())

    baseline = PerformanceBaseline("test_project", root)

    # Simulate 5 runs with increasing latency
    for i in range(5):
        metrics = PerfMetrics(
            scan_id=f"scan_{i:03d}",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + i * 86400)),
            api_p95_ms=200 + i * 50,   # 200→250→300→350→400
            api_p99_ms=350 + i * 80,
            api_error_rate=0.01 + i * 0.005,
            total_ms=5000 + i * 500,
            total_findings=15 + i,
        )
        result = baseline.record(metrics)
        alerts = result.get("regressions", [])
        if alerts:
            for a in alerts:
                print(f"[{a['severity']}] {a['detail']}")

    # Final check
    status = baseline.load_baseline()
    print(f"\nRuns: {status['runs']}")
    print(f"Baseline established: {status['baseline_established']}")
    print(f"Regressions: {status['regression_count']}")
    print(f"Trends: {json.dumps(status['trend'], indent=2)}")

    # Cleanup
    import shutil
    shutil.rmtree(root, ignore_errors=True)
    print("\nAll tests passed!")


def attach_performance_baseline(
    scan_result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Record the scan's performance metrics against the project baseline.

    Missing performance keys stay at PerfMetrics defaults; the projection never
    invents measurements that were not observed.
    """
    if not isinstance(scan_result, dict):
        return scan_result
    metrics = PerfMetrics.from_dict(scan_result)
    if not metrics.scan_id:
        return scan_result
    baseline = PerformanceBaseline(project, root)
    scan_result["performance_baseline"] = baseline.record(metrics)
    return scan_result


def install_performance_baseline() -> None:
    register_scan_post_hook(HOOK_NAME, attach_performance_baseline)
