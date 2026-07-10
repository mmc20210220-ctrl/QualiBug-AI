from __future__ import annotations

import pytest

from ai_test_asset_center.scan_operational_metrics import (
    OperationalMetricsNotMeasured,
    collect_observed_scan_operational_metrics,
)


def _runtime_view() -> dict:
    return {"target": {"runtime": {"environment_type": "sandbox"}}}


def _scan_result() -> dict:
    return {
        "dedupe_report": {"input_count": 10, "collapsed_count": 2},
        "v12": {
            "mainline_unification": {"analyzer": {"input": 2, "bound": 1}},
            "phases": {
                "execution": {
                    "observed_http_request_count": 8,
                    "production_http_requests": 0,
                    "scenario_attempts": 5,
                    "executed": 4,
                },
            },
            "evidence_graphs": [],
        },
    }


def test_collector_derives_non_llm_metrics_from_observed_scan_only() -> None:
    metrics = collect_observed_scan_operational_metrics(
        scan_result=_scan_result(),
        wall_clock_seconds=2.5,
        runtime_view=_runtime_view(),
    )

    assert metrics == {
        "wall_clock_seconds": 2.5,
        "estimated_cost_usd": 0.0,
        "request_count": 8,
        "production_http_requests": 0,
        "cleanup_failures": 0,
        "safety_incidents": 0,
        "dirty_test_environments": 0,
        "execution_success_rate": 0.8,
        "engine_success_rate": 1.0,
        "duplicate_rate": 0.2,
    }


def test_collector_rejects_llm_cost_when_provider_did_not_report_every_request() -> None:
    scan = _scan_result()
    scan["v12"]["mainline_unification"]["llm_reasoner"] = {
        "status": "ok",
        "total_engines": 2,
        "successful_engine_count": 2,
        "observed_model_request_count": 2,
        "model_usage": {
            "request_count": 2,
            "responses_with_cost": 0,
            "cost_usd": 0,
        },
    }

    with pytest.raises(OperationalMetricsNotMeasured, match="did not report cost_usd"):
        collect_observed_scan_operational_metrics(
            scan_result=scan,
            wall_clock_seconds=2.5,
            runtime_view=_runtime_view(),
        )


def test_collector_surfaces_cleanup_failure_as_dirty_environment() -> None:
    scan = _scan_result()
    scan["v12"]["evidence_graphs"] = [{
        "execution": {"sandbox_write": {"cleanup": {"status": "failed"}}},
    }]

    metrics = collect_observed_scan_operational_metrics(
        scan_result=scan,
        wall_clock_seconds=2.5,
        runtime_view=_runtime_view(),
    )

    assert metrics["cleanup_failures"] == 1
    assert metrics["dirty_test_environments"] == 1
