from __future__ import annotations

import pytest

from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.obligation_attempt_ledger import build_obligation_attempt_ledger
from ai_test_asset_center.scan_operational_metrics import (
    collect_observed_scan_operational_metrics,
)


def _runtime_view() -> dict:
    return {"target": {"runtime": {"environment_type": "sandbox"}}}


def _operational_receipt(*, cleanup_failures: int = 0) -> dict:
    return {
        "schema_version": "qualibug.execution-operational-receipt.v1",
        "receipt_id": "operational-1",
        "execution_status": "EXECUTED",
        "scenario_attempt_count": 1,
        "http_request_attempt_count": 8,
        "production_http_request_count": 0,
        "accepted_write_count": 0,
        "accepted_non_cleanup_write_count": 0,
        "accepted_cleanup_write_count": 0,
        "cleanup_outcome": {
            "status": "FAILED" if cleanup_failures else "NOT_REQUIRED",
            "attempted_count": 0,
            "completed_count": 0,
            "failure_count": cleanup_failures,
        },
    }


def _attempt_ledger(*, cleanup_failures: int = 0) -> dict:
    contract = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-1",
        campaign_id="campaign-1",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="shadow",
    )
    return build_obligation_attempt_ledger(
        mainline_run=contract,
        selected=[{"obligation_id": "obligation-1"}],
        compile_results={"obligation-1": {"status": "COMPILED"}},
        execution_results={
            "obligation-1": {
                "status": "EXECUTED",
                "operational_receipt": _operational_receipt(
                    cleanup_failures=cleanup_failures,
                ),
            }
        },
        gate_results={
            "obligation-1": {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
            }
        },
    )


def _scan_result(*, cleanup_failures: int = 0) -> dict:
    return {
        "dedupe_report": {"input_count": 10, "collapsed_count": 2},
        "v12": {
            "mainline_unification": {"analyzer": {"input": 2, "bound": 1}},
            "obligation_attempt_ledger": _attempt_ledger(
                cleanup_failures=cleanup_failures,
            ),
            "phases": {
                "execution": {
                    # Deliberately contradictory legacy counters: the terminal
                    # attempt receipts are the operational authority.
                    "observed_http_request_count": 999,
                    "production_http_requests": 999,
                    "scenario_attempts": 999,
                    "executed": 999,
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

    assert metrics["wall_clock_seconds"] == 2.5
    assert metrics["estimated_cost_usd"] == 0.0
    assert metrics["cost_measurement_status"] == "MEASURED"
    assert metrics["promotion_blockers"] == []
    assert metrics["target_http_request_count"] == 8
    assert metrics["request_count"] == 8
    assert metrics["scenario_attempts"] == 1
    assert metrics["accepted_write_count"] == 0
    assert metrics["production_http_requests"] == 0
    assert metrics["cleanup_failures"] == 0
    assert metrics["safety_incidents"] == 0
    assert metrics["dirty_test_environments"] == 0
    assert metrics["execution_success_rate"] == 1.0
    assert metrics["engine_success_rate"] == 1.0
    assert metrics["duplicate_rate"] == 0.2


def test_collector_preserves_known_metrics_when_provider_cost_is_unknown() -> None:
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

    metrics = collect_observed_scan_operational_metrics(
        scan_result=scan,
        wall_clock_seconds=2.5,
        runtime_view=_runtime_view(),
    )

    assert metrics["target_http_request_count"] == 8
    assert metrics["model_request_count"] == 2
    assert metrics["request_count"] == 10
    assert metrics["estimated_cost_usd"] is None
    assert metrics["cost_measurement_status"] == "NOT_MEASURED"
    assert metrics["promotion_blockers"] == ["COST_NOT_MEASURED"]


def test_collector_surfaces_cleanup_failure_as_dirty_environment() -> None:
    scan = _scan_result(cleanup_failures=1)

    metrics = collect_observed_scan_operational_metrics(
        scan_result=scan,
        wall_clock_seconds=2.5,
        runtime_view=_runtime_view(),
    )

    assert metrics["cleanup_failures"] == 1
    assert metrics["dirty_test_environments"] == 1


def test_collector_ignores_non_authoritative_nested_cleanup_statuses() -> None:
    scan = _scan_result()
    scan["v12"]["evidence_graphs"] = [{
        "execution": {"sandbox_write": {"cleanup": {"status": "failed"}}},
    }]

    metrics = collect_observed_scan_operational_metrics(
        scan_result=scan,
        wall_clock_seconds=2.5,
        runtime_view=_runtime_view(),
    )

    assert metrics["cleanup_failures"] == 0
