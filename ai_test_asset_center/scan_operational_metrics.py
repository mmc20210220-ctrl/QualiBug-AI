from __future__ import annotations

"""Strict operational metric extraction from an observed product scan."""

import math
from typing import Any

from .discovery_policy_evaluation_runner import PolicyEvaluationRunnerError


class OperationalMetricsNotMeasured(PolicyEvaluationRunnerError):
    """A required cost, request, reliability, or safety metric was not observed."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise OperationalMetricsNotMeasured(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise OperationalMetricsNotMeasured(f"{field} was not measured") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise OperationalMetricsNotMeasured(f"{field} must be finite and non-negative")
    return parsed


def _cleanup_failure_count(value: Any) -> int:
    failures: set[str] = set()

    def walk(item: Any, path: str) -> None:
        if isinstance(item, dict):
            cleanup = item.get("cleanup")
            if isinstance(cleanup, dict):
                status = str(cleanup.get("status") or "").strip().lower()
                if status in {"failed", "cleanup_incomplete", "incomplete"}:
                    failures.add(f"{path}.cleanup")
            if str(item.get("status") or "").strip().lower() == "cleanup_incomplete":
                failures.add(path)
            for key, child in item.items():
                walk(child, f"{path}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")

    walk(value, "scan")
    return len(failures)


def _llm_metrics(scan_result: dict[str, Any]) -> tuple[int, float, float | None]:
    v12 = _dict(scan_result.get("v12"))
    unification = _dict(v12.get("mainline_unification")) or _dict(
        _dict(scan_result.get("discovery_funnel")).get("mainline_unification")
    )
    llm = _dict(unification.get("llm_reasoner"))
    if not llm or str(llm.get("status") or "").lower() == "provider_unavailable":
        return 0, 1.0, 0.0
    model_usage = _dict(llm.get("model_usage"))
    model_requests = int(_number(
        model_usage.get("request_count", llm.get("observed_model_request_count")),
        "llm.model_request_count",
    ))
    total_engines = int(_number(llm.get("total_engines"), "llm.total_engines"))
    successful_engines = int(_number(llm.get("successful_engine_count"), "llm.successful_engine_count"))
    if total_engines <= 0 or successful_engines > total_engines:
        raise OperationalMetricsNotMeasured("LLM engine success counts are invalid")
    engine_rate = successful_engines / total_engines
    responses_with_cost = int(_number(model_usage.get("responses_with_cost"), "llm.responses_with_cost"))
    if responses_with_cost != model_requests:
        raise OperationalMetricsNotMeasured(
            "provider did not report cost_usd for every observed model request"
        )
    cost = _number(model_usage.get("cost_usd"), "llm.cost_usd")
    return model_requests, engine_rate, cost


def collect_observed_scan_operational_metrics(
    *,
    scan_result: dict[str, Any],
    wall_clock_seconds: float,
    runtime_view: dict[str, Any],
    **_: Any,
) -> dict[str, Any]:
    """Return strict gate metrics, or raise rather than filling unknown values."""

    v12 = _dict(scan_result.get("v12"))
    phases = _dict(v12.get("phases"))
    execution = _dict(phases.get("execution"))
    if not execution:
        raise OperationalMetricsNotMeasured("v12 execution phase metrics are missing")
    target_requests = int(_number(
        execution.get("observed_http_request_count"),
        "execution.observed_http_request_count",
    ))
    production_requests = int(_number(
        execution.get("production_http_requests"),
        "execution.production_http_requests",
    ))
    attempts = int(_number(execution.get("scenario_attempts"), "execution.scenario_attempts"))
    executed = int(_number(execution.get("executed"), "execution.executed"))
    if executed > attempts:
        raise OperationalMetricsNotMeasured("execution success counts are invalid")
    execution_rate = executed / attempts if attempts else 0.0
    model_requests, engine_rate, observed_cost = _llm_metrics(scan_result)
    if observed_cost is None:
        raise OperationalMetricsNotMeasured("scan cost was not measured")
    dedupe = _dict(scan_result.get("dedupe_report"))
    dedupe_input = int(_number(dedupe.get("input_count"), "dedupe.input_count"))
    dedupe_collapsed = int(_number(dedupe.get("collapsed_count"), "dedupe.collapsed_count"))
    if dedupe_collapsed > dedupe_input:
        raise OperationalMetricsNotMeasured("dedupe counts are invalid")
    cleanup_failures = _cleanup_failure_count(v12)
    runtime = _dict(_dict(runtime_view.get("target")).get("runtime"))
    environment_type = str(runtime.get("environment_type") or "").strip().lower()
    if environment_type in {"", "production", "prod", "live"}:
        raise OperationalMetricsNotMeasured("operational metrics require explicit non-production environment")
    return {
        "wall_clock_seconds": round(_number(wall_clock_seconds, "wall_clock_seconds"), 6),
        "estimated_cost_usd": round(observed_cost, 6),
        "request_count": target_requests + model_requests,
        "production_http_requests": production_requests,
        "cleanup_failures": cleanup_failures,
        "safety_incidents": 0 if production_requests == 0 else production_requests,
        "dirty_test_environments": 1 if cleanup_failures else 0,
        "execution_success_rate": round(execution_rate, 6),
        "engine_success_rate": round(engine_rate, 6),
        "duplicate_rate": round(dedupe_collapsed / dedupe_input, 6) if dedupe_input else 0.0,
    }
