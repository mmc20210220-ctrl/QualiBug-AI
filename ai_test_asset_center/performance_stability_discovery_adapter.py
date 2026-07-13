from __future__ import annotations

"""Performance and stability discovery adapters."""

from pathlib import Path
from typing import Any

from .defect_signal_schema import normalize_defect_signal
from .performance_monitor import PerformanceMetrics
from .performance_oracles import evaluate_performance_oracles
from .stability_oracles import evaluate_stability_oracles


def generate_performance_stability_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
    max_count: int = 8,
) -> list[dict[str, Any]]:
    del root
    paths = openapi.get("paths") if isinstance(openapi, dict) else {}
    probes: list[dict[str, Any]] = []
    timeout = int(cfg.get("request_timeout_seconds") or 10)
    if isinstance(paths, dict):
        for path, methods in paths.items():
            if len(probes) >= max_count:
                break
            method = "GET"
            if isinstance(methods, dict):
                method = next((str(name).upper() for name in methods.keys()), "GET")
            probes.append(
                normalize_defect_signal(
                    {
                        "probe_id": f"PERF_STAB_{len(probes)+1:04d}",
                        "title": f"性能与稳定性巡检：{method} {path}",
                        "defect_family": "performance",
                        "risk_type": "performance_regression",
                        "severity": "P2",
                        "source": "performance_stability_adapter",
                        "method": method,
                        "path": str(path),
                        "expected": f"接口在 timeout={timeout}s 内稳定返回，且不存在明显退化或异常抖动",
                        "actual": "待验证慢请求、超时、错误率与稳定性信号",
                        "status": "planned_probe",
                        "confidence": 0.3,
                        "evidence": {"timeout_budget_seconds": timeout, "project_id": project_id},
                    },
                    signal_kind="probe",
                    default_source="performance_stability_adapter",
                    default_status="planned_probe",
                    default_confidence=0.3,
                )
            )
    if not probes:
        probes.append(
            normalize_defect_signal(
                {
                    "probe_id": "PERF_STAB_0000",
                    "title": "全局性能与稳定性健康检查",
                    "defect_family": "stability",
                    "risk_type": "stability_timeout",
                    "severity": "P2",
                    "source": "performance_stability_adapter",
                    "expected": "关键请求延迟、超时与错误模式保持在可接受范围内",
                    "actual": "待验证",
                    "status": "planned_probe",
                    "confidence": 0.25,
                    "evidence": {"project_id": project_id},
                },
                signal_kind="probe",
                default_source="performance_stability_adapter",
                default_status="planned_probe",
                default_confidence=0.25,
            )
        )
    return probes


def collect_performance_stability_issues(
    executions: list[dict[str, Any]],
    *,
    request_timeout_seconds: int = 10,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    metrics = PerformanceMetrics.get_summary()
    oracle_issues = [
        *evaluate_performance_oracles(executions, metrics, request_timeout_seconds=request_timeout_seconds),
        *evaluate_stability_oracles(executions, request_timeout_seconds=request_timeout_seconds),
    ]
    for oracle_issue in oracle_issues:
        normalized = normalize_defect_signal(
            {
                "issue_id": f"ISSUE_PERF_STAB_{len(issues)+1:04d}",
                "status": "needs_human_review",
                "source": "performance_stability_adapter",
                **oracle_issue,
            },
            signal_kind="issue",
            default_source="performance_stability_adapter",
        )
        issues.append(normalized)
    return issues
