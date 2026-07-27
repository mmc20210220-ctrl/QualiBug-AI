from __future__ import annotations

from ai_test_asset_center.scan_operational_metrics import (
    collect_observed_scan_operational_metrics,
)


def _scan_result(
    *,
    usage: dict[str, float],
    agent_status: str = "VERIFIED_WITH_REJECTIONS",
) -> dict:
    return {
        "v12": {
            "phases": {
                "execution": {
                    "observed_http_request_count": 7,
                    "production_http_requests": 0,
                    "scenario_attempts": 2,
                    "executed": 2,
                },
            },
            "agent_semantic_link_receipt": {
                "status": agent_status,
                "usage": usage,
            },
        },
        "dedupe_report": {
            "input_count": 3,
            "collapsed_count": 1,
        },
    }


def _runtime_view() -> dict:
    return {
        "target": {
            "runtime": {
                "environment_type": "test",
            },
        },
    }


def test_agent_model_request_is_counted_when_provider_omits_cost() -> None:
    metrics = collect_observed_scan_operational_metrics(
        scan_result=_scan_result(usage={
            "request_count": 1,
            "total_tokens": 1234,
            "cost_usd": 0.0,
            "responses_with_cost": 0,
        }),
        wall_clock_seconds=12.5,
        runtime_view=_runtime_view(),
    )

    assert metrics["request_count"] == 8
    assert metrics["model_request_count"] == 1
    assert metrics["estimated_cost_usd"] is None
    assert metrics["model_cost_status"] == "NOT_REPORTED"
    assert metrics["engine_success_rate"] == 1.0


def test_agent_model_cost_is_measured_only_from_provider_usage() -> None:
    metrics = collect_observed_scan_operational_metrics(
        scan_result=_scan_result(usage={
            "request_count": 1,
            "total_tokens": 1234,
            "cost_usd": 0.42,
            "responses_with_cost": 1,
        }),
        wall_clock_seconds=12.5,
        runtime_view=_runtime_view(),
    )

    assert metrics["estimated_cost_usd"] == 0.42
    assert metrics["model_cost_status"] == "MEASURED"


def test_agent_semantic_gap_status_keeps_observed_usage_measurable() -> None:
    metrics = collect_observed_scan_operational_metrics(
        scan_result=_scan_result(
            agent_status="VERIFIED_WITH_GAPS",
            usage={
                "request_count": 2,
                "total_tokens": 1800,
                "cost_usd": 0.0,
                "responses_with_cost": 0,
            },
        ),
        wall_clock_seconds=12.5,
        runtime_view=_runtime_view(),
    )

    assert metrics["model_request_count"] == 2
    assert metrics["engine_success_rate"] == 1.0
