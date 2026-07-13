from __future__ import annotations

"""Stability oracle helpers for runtime discovery."""

from collections import defaultdict
from typing import Any


def evaluate_stability_oracles(
    executions: list[dict[str, Any]],
    *,
    request_timeout_seconds: int = 10,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    execution_rows = [item for item in executions if isinstance(item, dict)]
    total = len(execution_rows)
    if not total:
        return issues

    error_rows = [item for item in execution_rows if item.get("error")]
    timeout_rows = [item for item in error_rows if "timeout" in str(item.get("error") or "").lower()]
    failure_rate = len(error_rows) / max(1, total)
    if failure_rate >= 0.25:
        issues.append(
            {
                "title": "运行期错误率偏高，稳定性不足",
                "defect_family": "stability",
                "risk_type": "stability_timeout",
                "severity": "P1",
                "confidence": 0.84,
                "expected": "执行阶段错误率应保持在较低水平",
                "actual": f"执行错误 {len(error_rows)}/{total}",
                "evidence": {"error_count": len(error_rows), "execution_count": total, "failure_rate": round(failure_rate, 4)},
            }
        )
    if timeout_rows:
        issues.append(
            {
                "title": "执行阶段触发超时，存在稳定性风险",
                "defect_family": "stability",
                "risk_type": "stability_timeout",
                "severity": "P1",
                "confidence": 0.87,
                "expected": f"关键调用应在 {request_timeout_seconds}s 内稳定返回",
                "actual": f"观测到 {len(timeout_rows)} 次 timeout",
                "evidence": {"timeout_count": len(timeout_rows), "paths": [((item.get('probe') or {}).get('path') if isinstance(item.get('probe'), dict) else None) for item in timeout_rows[:10]]},
            }
        )

    status_codes = [int(item.get("response_status") or 0) for item in execution_rows if item.get("response_status") is not None]
    server_error_count = sum(1 for code in status_codes if code >= 500)
    if server_error_count >= 2:
        issues.append(
            {
                "title": "执行期间出现服务端错误尖峰",
                "defect_family": "stability",
                "risk_type": "stability_timeout",
                "severity": "P1",
                "confidence": 0.8,
                "expected": "服务端错误应为可解释的偶发现象",
                "actual": f"观测到 {server_error_count} 个 5xx 响应",
                "evidence": {"status_codes": status_codes[:20], "server_error_count": server_error_count},
            }
        )

    per_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in execution_rows:
        probe = item.get("probe") if isinstance(item.get("probe"), dict) else {}
        route_key = f"{probe.get('method') or 'GET'} {probe.get('path') or probe.get('route') or ''}".strip()
        if route_key:
            per_route[route_key].append(item)
    for route_key, rows in per_route.items():
        if len(rows) < 2:
            continue
        outcomes = {("error" if row.get("error") else "ok") for row in rows}
        statuses = {int(row.get("response_status") or 0) for row in rows if row.get("response_status") is not None}
        if len(outcomes) > 1 or len(statuses) > 1:
            issues.append(
                {
                    "title": f"相同接口重复观测结果不一致，疑似 flaky：{route_key}",
                    "defect_family": "stability",
                    "risk_type": "stability_timeout",
                    "severity": "P2",
                    "confidence": 0.76,
                    "expected": "相同接口在同轮执行中应表现稳定",
                    "actual": "同一路径出现混合成功/失败或响应状态不一致",
                    "evidence": {"route": route_key, "statuses": sorted(statuses), "outcomes": sorted(outcomes), "sample_count": len(rows)},
                }
            )
        route_errors = sum(1 for row in rows if row.get("error"))
        if len(rows) >= 3 and route_errors >= 2:
            issues.append(
                {
                    "title": f"接口疑似重试风暴或重复失败：{route_key}",
                    "defect_family": "stability",
                    "risk_type": "stability_timeout",
                    "severity": "P1",
                    "confidence": 0.81,
                    "expected": "重复请求不应持续失败或反复重试后仍不收敛",
                    "actual": f"{len(rows)} 次观测中 {route_errors} 次失败",
                    "evidence": {"route": route_key, "sample_count": len(rows), "error_count": route_errors},
                }
            )
    return issues

