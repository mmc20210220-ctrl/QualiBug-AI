from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ai_test_asset_center.discovery_funnel import (
    DiscoveryFunnelError,
    build_funnel,
    build_pipeline_health,
    effective_execution_status,
    reconcile_product_pipeline_health,
)
from ai_test_asset_center.discovery_trace_ledger import build_discovery_trace_ledger
from ai_test_asset_center.obligation_attempt_ledger import build_obligation_attempt_ledger
from ai_test_asset_center.v12_pipeline import (
    _normalize_executable_api_document,
    _publish_behavior_contract_snapshot,
    _redacted_execution_trace_graph,
    _record_pipeline_failure,
)


def _attempt_result(
    outcomes: list[tuple[str, str]],
    *,
    run_id: str = "run-health",
) -> dict:
    selected = [
        {
            "obligation_id": f"obl-{index}",
            "risk_family": "generic",
            "required_operations": [f"op-{index}"],
        }
        for index, _ in enumerate(outcomes, start=1)
    ]
    compile_results: dict[str, dict] = {}
    execution_results: dict[str, dict] = {}
    gate_results: dict[str, dict] = {}
    for index, (terminal, reason) in enumerate(outcomes, start=1):
        obligation_id = f"obl-{index}"
        if terminal in {"BLOCKED", "DEFERRED"}:
            compile_results[obligation_id] = {
                "status": terminal,
                "reason_code": reason,
                "cost_coverage_status": "MEASURED",
            }
            continue
        compile_results[obligation_id] = {
            "status": "COMPILED",
            "experiment_id": f"exp-{index}",
            "cost_coverage_status": "MEASURED",
        }
        if terminal == "HARNESS_FAILED":
            execution_results[obligation_id] = {
                "status": terminal,
                "reason_code": reason,
                "execution_id": f"exec-{index}",
                "cost_coverage_status": "MEASURED",
            }
            continue
        execution_results[obligation_id] = {
            "status": "EXECUTED",
            "execution_id": f"exec-{index}",
            "observation_receipt_ids": [f"obs-{index}"],
            "oracle_receipt_id": f"oracle-{index}",
            "cost_coverage_status": "MEASURED",
        }
        gate_results[obligation_id] = {
            "status": terminal,
            "reason_code": reason,
            "gate_receipt_id": f"gate-{index}",
            "cost_coverage_status": "MEASURED",
        }
    ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": run_id, "campaign_id": "campaign-health"},
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    return {
        "obligation_attempt_ledger": ledger,
        "formal_count_projection": {
            "schema_version": "qualibug.discovery-quality-projection.v2",
            "authority_status": "VERIFIED",
            "formal_customer_deliverable_count": 0,
            "canonical_defect_count": 0,
            "canonical_defect_ids": [],
            "delivery_occurrence_count": 0,
            "delivery_occurrence_finding_ids": [],
        },
    }


def test_pipeline_health_marks_failed_safe_when_execution_observability_gap():
    result = _attempt_result([("HARNESS_FAILED", "EXECUTION_OBSERVABILITY_GAP")])
    result["stage_failures"] = ["execution_observability_gap"]
    health = build_pipeline_health(result)
    assert health["status"] == "FAILED_SAFE"
    assert health["empty_findings_means_no_bugs"] is False
    assert "must not" in health["operator_note"]


def test_pipeline_health_marks_blocked_when_no_execution_receipts():
    health = build_pipeline_health(
        _attempt_result([("BLOCKED", "BLOCKED_MISSING_ACTOR")])
    )
    assert health["status"] == "BLOCKED"
    assert health["empty_findings_means_no_bugs"] is False


def test_product_health_never_reports_ok_when_preflight_blocked_execution() -> None:
    health = reconcile_product_pipeline_health(
        {"status": "OK", "empty_findings_means_no_bugs": True},
        execution_status="not_executed",
        preflight_diagnostics={
            "ready": False,
            "all_checks_passed": False,
            "errors": 1,
        },
    )

    assert health["status"] == "BLOCKED"
    assert health["empty_findings_means_no_bugs"] is False
    assert health["execution_reason"] == "preflight_not_ready"
    assert health["preflight"]["errors"] == 1


def test_product_health_degrades_completed_run_when_preflight_failed() -> None:
    health = reconcile_product_pipeline_health(
        {"status": "OK", "empty_findings_means_no_bugs": True},
        execution_status="completed",
        preflight_diagnostics={"ready": False, "errors": 2},
    )

    assert health["status"] == "DEGRADED"
    assert health["empty_findings_means_no_bugs"] is False
    assert health["execution_reason"] == "preflight_health_failed"


def test_product_health_preserves_warning_only_preflight_on_completed_run() -> None:
    health = reconcile_product_pipeline_health(
        {"status": "OK", "empty_findings_means_no_bugs": True},
        execution_status="completed",
        preflight_diagnostics={
            "ready": True,
            "all_checks_passed": False,
            "errors": 0,
            "warnings": 1,
        },
    )

    assert health["status"] == "OK"
    assert health["empty_findings_means_no_bugs"] is True
    assert health["preflight"]["all_checks_passed"] is False
    assert health["preflight"]["warnings"] == 1
    assert "execution_reason" not in health


def test_attempt_receipts_override_conflicting_legacy_execution_counters() -> None:
    result = {
        **_attempt_result([
            ("REJECTED", "ORACLE_NOT_VIOLATED"),
            ("BLOCKED", "BLOCKED_MISSING_BINDING"),
        ]),
        "phases": {"execution": {"status": "blocked", "executed": 0}},
        "auto_har": {"status": "no_traffic", "entry_count": 0},
        "experiment_execution": {
            "selected_count": 2,
            "executed_count": 1,
            "blocked_count": 1,
            "results": [
                {
                    "status": "EXECUTED",
                    "steps": [{"method": "GET", "path": "/declared", "status_code": 200}],
                },
                {"status": "BLOCKED", "steps": []},
            ],
        },
    }

    assert effective_execution_status(result) == "completed"
    health = build_pipeline_health(result)
    assert health["status"] == "DEGRADED"
    assert health["execution_status"] == "completed"
    assert health["selected_obligation_count"] == 2
    assert health["empty_findings_means_no_bugs"] is False


def test_product_health_keeps_partial_experiment_run_degraded_not_blocked() -> None:
    health = reconcile_product_pipeline_health(
        {"status": "DEGRADED", "empty_findings_means_no_bugs": False},
        execution_status="partial",
        preflight_diagnostics={"ready": True, "all_checks_passed": True, "errors": 0},
    )

    assert health["status"] == "DEGRADED"
    assert health["execution_status"] == "partial"
    assert health["execution_reason"] == "partial_execution"


def test_build_funnel_embeds_pipeline_health_and_warns_on_zero_bugs():
    result = _attempt_result([("HARNESS_FAILED", "EXECUTION_OBSERVABILITY_GAP")])
    result["stage_failures"] = ["execution_observability_gap"]
    funnel = build_funnel(result)
    assert funnel["pipeline_health"]["status"] == "FAILED_SAFE"
    assert funnel["validated_bug_count"] == 0
    assert "FAILED_SAFE" in funnel["explanation"] or "不能据此宣称" in funnel["explanation"]
    assert any(
        str(item.get("reason") or "") == "EXECUTION_OBSERVABILITY_GAP"
        for item in funnel["top_blocking_reasons"]
    )


def test_pipeline_health_ok_when_execution_healthy():
    health = build_pipeline_health(_attempt_result([
        ("REJECTED", "ORACLE_NOT_VIOLATED"),
        ("REJECTED", "ORACLE_NOT_VIOLATED"),
        ("REJECTED", "ORACLE_NOT_VIOLATED"),
    ]))

    assert health["status"] == "OK"
    assert health["empty_findings_means_no_bugs"] is True


def test_pipeline_health_surfaces_offline_reasoner_as_degraded() -> None:
    health = build_pipeline_health({
        **_attempt_result([("REJECTED", "ORACLE_NOT_VIOLATED")]),
        "phases": {
            "execution": {
                "status": "completed",
                "executed": 3,
                "observability": [{"kind": "multi_role_accounts", "status": "ok"}],
            }
        },
        "mainline_unification": {
            "llm_reasoner": {
                "status": "degraded",
                "failed_engine_count": 11,
                "failed_engine_names": ["causality", "invariant"],
                "engine_error_class_counts": {"network": 11},
                "observed_model_request_count": 0,
                "model_usage": {"request_count": 0},
            }
        },
        "findings": [],
    })

    assert health["status"] == "DEGRADED"
    assert health["reasoner_status"] == "degraded"
    assert health["reasoner_failure_count"] == 11
    assert health["reasoner_error_class_counts"] == {"network": 11}
    assert health["empty_findings_means_no_bugs"] is False


def test_pipeline_health_degraded_when_slice_budget_leaves_unattempted():
    health = build_pipeline_health(_attempt_result([
        ("REJECTED", "ORACLE_NOT_VIOLATED"),
        ("DEFERRED", "SLICE_BUDGET_REACHED"),
    ]))

    assert health["status"] == "DEGRADED"
    assert health["empty_findings_means_no_bugs"] is False
    assert health["blocked_obligation_count"] == 1
    assert health["terminal_reason_counts"]["SLICE_BUDGET_REACHED"] == 1
    assert "未执行" in health["operator_note"] or "slice_budget" in health["operator_note"]


def test_pipeline_health_blocked_when_path_binding_blocks_all_execution():
    health = build_pipeline_health(_attempt_result([
        ("BLOCKED", "BLOCKED_MISSING_BINDING"),
        ("BLOCKED", "BLOCKED_MISSING_BINDING"),
    ]))

    assert health["status"] == "BLOCKED"
    assert health["empty_findings_means_no_bugs"] is False
    assert health["terminal_reason_counts"]["BLOCKED_MISSING_BINDING"] == 2
    assert "binding" in health["operator_note"].lower() or "路径" in health["operator_note"]


def test_funnel_surfaces_unexecuted_and_binding_blockers():
    funnel = build_funnel(_attempt_result([
        ("REJECTED", "ORACLE_NOT_VIOLATED"),
        ("DEFERRED", "SLICE_BUDGET_REACHED"),
        ("BLOCKED", "BLOCKED_MISSING_BINDING"),
    ]))
    reasons = {str(item.get("reason") or "") for item in funnel["top_blocking_reasons"]}
    assert "SLICE_BUDGET_REACHED" in reasons
    assert "BLOCKED_MISSING_BINDING" in reasons
    assert funnel["pipeline_health"]["status"] == "DEGRADED"
    assert funnel["pipeline_health"]["empty_findings_means_no_bugs"] is False

def test_v12_failure_preserves_grounded_candidate_pool_in_funnel() -> None:
    result = {
        "phases": {},
        "findings": [],
        "behavior_slice_ledger": {},
    }
    slices = [
        {
            "slice_id": "slice-permission",
            "kind": "permission",
            "entity": "resource",
            "endpoints": ["/api/resources/{id}"],
        },
        {
            "slice_id": "slice-state",
            "kind": "invariant",
            "entity": "resource",
            "endpoints": ["/api/resources/{id}"],
        },
    ]

    assert _publish_behavior_contract_snapshot(
        result,
        {"summary": {}, "coverage_gaps": []},
        slices,
    ) == 2
    _record_pipeline_failure(
        result,
        RuntimeError("api_document_parse_failed:ScannerError"),
    )
    assert result["phases"]["pipeline"]["status"] == "FAILED_SAFE"
    assert result["phases"]["pipeline"]["preserved_slice_count"] == 2
    assert result["stage_status"]["pipeline"] == "FAILED_SAFE"
    assert result["stage_failures"] == [
        "pipeline:RuntimeError:api_document_parse_failed:ScannerError"
    ]
    assert result["behavior_slice_ledger"]["pending_slice_ids"] == [
        "slice-permission",
        "slice-state",
    ]
    with pytest.raises(DiscoveryFunnelError, match="obligation_attempt_ledger_missing"):
        build_funnel(result)


def test_markdown_api_is_normalized_before_runtime_scenario_generation() -> None:
    markdown = """# Benchmark API

### GET /api/orders/{id}
Response note: values may include YAML-like colons: without quoting.

### POST /api/orders
```json
{"name": "qualibug-order"}
```
"""

    executable_document, receipt = _normalize_executable_api_document(markdown)
    normalized = json.loads(executable_document)

    assert receipt["status"] == "normalized"
    assert receipt["input_format"] == "markdown_api"
    assert receipt["normalized_path_count"] == 2
    assert receipt["normalized_operation_count"] == 2
    assert normalized["paths"]["/api/orders/{id}"]["get"]
    assert normalized["paths"]["/api/orders"]["post"]


def test_unparseable_api_becomes_observable_safe_catalog(monkeypatch) -> None:
    from ai_test_asset_center import universal_api_parser

    def _explode(_document):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(universal_api_parser, "parse_to_openapi", _explode)
    executable_document, receipt = _normalize_executable_api_document(
        "### GET /api/orders"
    )
    normalized = json.loads(executable_document)

    assert receipt["status"] == "FAILED_SAFE"
    assert receipt["reason"] == "api_document_parse_failed"
    assert receipt["error_type"] == "RuntimeError"
    assert normalized["paths"] == {}


def test_successful_execution_without_violation_remains_in_trace_ledger() -> None:
    scenario = SimpleNamespace(
        id="scenario-1",
        behavior_slice_id="slice-1",
    )
    summary = _redacted_execution_trace_graph(
        scenario,
        {
            "scenario_id": "scenario-1",
            "actor_role": "readonly",
            "steps": [
                {
                    "method": "GET",
                    "path": "/api/orders/123?token=do-not-persist",
                    "status": 200,
                    "response": {
                        "status_code": 200,
                        "body": {"password": "do-not-persist"},
                    },
                }
            ],
            "errors": [],
        },
        discovery_round=1,
    )
    summary["oracle_results"].append(
        {"oracle_name": "ConsistencyOracle", "passed": True}
    )
    selected = [{
        "obligation_id": "obl-1",
        "risk_family": "invariant",
        "required_operations": ["op-read-order"],
        "behavior_slice_id": "slice-1",
        "source_refs": [{"kind": "api", "source_id": "SRC-1"}],
    }]
    attempt_ledger = build_obligation_attempt_ledger(
        mainline_run={"run_id": "run-1", "campaign_id": "campaign-1"},
        selected=selected,
        compile_results={
            "obl-1": {"status": "COMPILED", "experiment_id": "exp-1"}
        },
        execution_results={
            "obl-1": {
                "status": "EXECUTED",
                "execution_id": "exec-1",
                "observation_receipt_ids": ["obs-1"],
                "oracle_receipt_id": "oracle-1",
            }
        },
        gate_results={
            "obl-1": {
                "status": "REJECTED",
                "reason_code": "ORACLE_NOT_VIOLATED",
                "gate_receipt_id": "gate-1",
            }
        },
    )
    v12_result = {
        "obligation_attempt_ledger": attempt_ledger,
        "formal_count_projection": {
            "delivery_occurrence_finding_ids": [],
            "canonical_defect_ids": [],
        },
        "execution_trace_summaries": [summary],
    }

    ledger = build_discovery_trace_ledger(
        v12_result,
        run_id="run-1",
        policy_id="policy-1",
        target_id="target-1",
        project_id="project-1",
        industry="commerce",
        evaluation_mode="replay",
    )

    trace = ledger["attempts"][0]
    assert trace["obligation_id"] == "obl-1"
    assert trace["observation_receipt_ids"] == ["obs-1"]
    assert trace["oracle_receipt_id"] == "oracle-1"
    assert trace["outcome"] == "valid_success_control"
    assert trace["failure_signatures"] == []
    serialized = json.dumps(ledger, ensure_ascii=False)
    assert "do-not-persist" not in serialized
    assert "password" not in serialized


def test_redacted_execution_trace_keeps_operational_receipt_counts() -> None:
    scenario = SimpleNamespace(id="scenario-write", behavior_slice_id="slice-write")

    summary = _redacted_execution_trace_graph(
        scenario,
        {
            "scenario_id": "scenario-write",
            "steps": [
                {"method": "POST", "path": "/resources", "status": 201},
            ],
            "sandbox_write": {
                "status": "cleanup_incomplete",
                "cleanup": {"status": "failed", "receipt_ref": ""},
                "audit_records": [
                    {"operation_accepted": True, "environment_kind": "test"},
                    {"operation_accepted": False, "environment_kind": "test"},
                ],
            },
        },
        discovery_round=1,
    )

    operational = summary["execution_trace"]["operational_receipt"]
    assert operational == {
        "scenario_attempt_count": 1,
        "http_request_attempt_count": 1,
        "production_http_request_count": 0,
        "accepted_write_count": 1,
        "accepted_non_cleanup_write_count": 1,
        "accepted_cleanup_write_count": 0,
        "cleanup_attempted_count": 1,
        "cleanup_completed_count": 0,
        "cleanup_failure_count": 1,
    }
