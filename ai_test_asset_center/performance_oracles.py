from __future__ import annotations

"""Performance oracle helpers for runtime discovery."""

import math
from collections import defaultdict
from typing import Any


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    ratio = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * ratio


def evaluate_performance_oracles(
    executions: list[dict[str, Any]],
    metrics_summary: dict[str, Any],
    *,
    request_timeout_seconds: int = 10,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    durations = [float(item.get("duration_seconds") or 0.0) for item in executions if isinstance(item, dict) and item.get("duration_seconds") is not None]
    if durations:
        p50 = _percentile(durations, 0.50)
        p95 = _percentile(durations, 0.95)
        slow_threshold = max(1.0, min(float(request_timeout_seconds), 3.0))
        if p95 >= slow_threshold:
            slowest = max(
                (item for item in executions if isinstance(item, dict)),
                key=lambda row: float(row.get("duration_seconds") or 0.0),
                default={},
            )
            issues.append(
                {
                    "title": "关键请求长尾延迟超出性能预算",
                    "defect_family": "performance",
                    "risk_type": "performance_regression",
                    "severity": "P1" if p95 >= max(2.0, request_timeout_seconds * 0.8) else "P2",
                    "confidence": 0.84,
                    "expected": f"运行期请求 p95 应低于 {slow_threshold:.2f}s",
                    "actual": f"观测到 p50={p50:.3f}s, p95={p95:.3f}s",
                    "evidence": {"p50": round(p50, 4), "p95": round(p95, 4), "slowest_probe": slowest.get('probe_id'), "slowest_path": ((slowest.get('probe') or {}).get('path') if isinstance(slowest.get('probe'), dict) else None)},
                }
            )
        if p50 > 0 and p95 >= p50 * 2.5:
            issues.append(
                {
                    "title": "关键请求存在明显性能抖动",
                    "defect_family": "performance",
                    "risk_type": "performance_regression",
                    "severity": "P2",
                    "confidence": 0.74,
                    "expected": "同一轮发现执行中不应出现明显长尾抖动",
                    "actual": f"p50={p50:.3f}s, p95={p95:.3f}s",
                    "evidence": {"p50": round(p50, 4), "p95": round(p95, 4), "durations": [round(item, 4) for item in sorted(durations)[:20]]},
                }
            )

    metrics_with_counts = {name: item for name, item in metrics_summary.items() if isinstance(item, dict) and int(item.get("count") or 0) > 0}
    if metrics_with_counts:
        hot_metric = max(metrics_with_counts.items(), key=lambda pair: float((pair[1] or {}).get("max") or 0.0))
        metric_name, metric_summary = hot_metric
        max_duration = float(metric_summary.get("max") or 0.0)
        avg_duration = float(metric_summary.get("avg") or 0.0)
        if max_duration >= max(1.5, avg_duration * 2.2):
            issues.append(
                {
                    "title": "内部执行路径出现性能热点",
                    "defect_family": "performance",
                    "risk_type": "performance_regression",
                    "severity": "P2",
                    "confidence": 0.7,
                    "expected": "内部关键路径不应出现明显热点或 fanout 放大",
                    "actual": f"{metric_name} avg={avg_duration:.3f}s, max={max_duration:.3f}s",
                    "evidence": {"metric_name": metric_name, "metric_summary": metric_summary},
                }
            )

    endpoint_buckets: dict[str, list[float]] = defaultdict(list)
    for execution in executions:
        if not isinstance(execution, dict):
            continue
        probe = execution.get("probe") if isinstance(execution.get("probe"), dict) else {}
        route_key = f"{probe.get('method') or 'GET'} {probe.get('path') or probe.get('route') or ''}".strip()
        duration = execution.get("duration_seconds")
        if route_key and duration is not None:
            endpoint_buckets[route_key].append(float(duration))
    for route_key, route_durations in endpoint_buckets.items():
        if len(route_durations) < 2:
            continue
        route_p95 = _percentile(route_durations, 0.95)
        route_p50 = _percentile(route_durations, 0.50)
        if route_p50 > 0 and route_p95 >= route_p50 * 2.5:
            issues.append(
                {
                    "title": f"接口存在路径级性能长尾：{route_key}",
                    "defect_family": "performance",
                    "risk_type": "performance_regression",
                    "severity": "P2",
                    "confidence": 0.77,
                    "expected": "相同接口重复观测不应出现剧烈长尾",
                    "actual": f"route p50={route_p50:.3f}s, route p95={route_p95:.3f}s",
                    "evidence": {"route": route_key, "durations": [round(value, 4) for value in route_durations]},
                }
            )
    return issues

