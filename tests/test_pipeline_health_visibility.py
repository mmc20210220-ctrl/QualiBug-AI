from __future__ import annotations

import json
from types import SimpleNamespace

from ai_test_asset_center.discovery_funnel import (
    build_funnel,
    build_pipeline_health,
    effective_execution_status,
    reconcile_product_pipeline_health,
)
from ai_test_asset_center.discovery_trace_ledger import build_discovery_trace_ledger
from ai_test_asset_center.v12_pipeline import (
    _normalize_executable_api_document,
    _publish_behavior_contract_snapshot,
    _redacted_execution_trace_graph,
    _record_pipeline_failure,
)


def test_pipeline_health_marks_failed_safe_when_execution_observability_gap():
    health = build_pipeline_health({
        "phases": {
            "execution": {
                "status": "completed",
                "executed": 2,
                "observability_status": "FAILED_SAFE",
                "observability": [
                    {"kind": "multi_role_accounts", "status": "missing", "reason": "test_accounts_json_missing"},
                ],
            }
        },
        "findings": [],
    })
    assert health["status"] == "FAILED_SAFE"
    assert health["empty_findings_means_no_bugs"] is False
    assert "无缺陷" in health["operator_note"] or "伪影" in health["operator_note"] or "不能" in health["operator_note"]


def test_pipeline_health_marks_blocked_when_no_execution_receipts():
    health = build_pipeline_health({
        "phases": {
            "execution": {
                "status": "blocked",
                "reason": "test_actor_identity_missing",
                "executed": 0,
            }
        }
    })
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


def test_experiment_http_receipts_override_legacy_no_traffic_without_hiding_partial_execution() -> None:
    result = {
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

    assert effective_execution_status(result) == "partial"
    health = build_pipeline_health(result)
    assert health["status"] == "DEGRADED"
    assert health["execution_status"] == "partial"
    assert health["no_real_traffic"] is False
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
    funnel = build_funnel({
        "phases": {
            "incremental_discovery": {"selected_slice_ids": ["s1", "s2"], "total_slices": 2},
            "execution": {
                "status": "completed",
                "executed": 0,
                "observability_status": "FAILED_SAFE",
                "observability": [
                    {"kind": "disabled_account_login_probe", "status": "failed", "reason": "boom"},
                ],
                "reason": "execution_observability_gap",
            },
            "oracle": {"total_evaluated": 0, "violations_found": 0},
        },
        "findings": [],
        "behavior_slice_ledger": {"total_slices": 2, "selected_slice_ids": ["s1", "s2"]},
    })
    assert funnel["pipeline_health"]["status"] == "FAILED_SAFE"
    assert funnel["validated_bug_count"] == 0
    assert "FAILED_SAFE" in funnel["explanation"] or "不能据此宣称" in funnel["explanation"]
    assert any(
        str(item.get("reason") or "") == "execution_observability_gap"
        for item in funnel["top_blocking_reasons"]
    )


def test_pipeline_health_ok_when_execution_healthy():
    health = build_pipeline_health({
        "phases": {
            "execution": {
                "status": "completed",
                "executed": 3,
                "observability": [{"kind": "multi_role_accounts", "status": "ok", "roles": ["admin:a"]}],
            }
        },
        "findings": [],
    })
    assert health["status"] == "OK"
    assert health["empty_findings_means_no_bugs"] is True


def test_pipeline_health_degraded_when_slice_budget_leaves_unattempted():
    health = build_pipeline_health({
        "phases": {
            "execution": {"status": "completed", "executed": 2},
            "incremental_discovery": {
                "stop_reason": "slice_budget_reached",
                "pending_slice_ids": ["s3", "s4", "s5"],
                "selected_slice_ids": ["s1", "s2"],
                "total_slices": 5,
            },
        },
        "findings": [],
    })
    assert health["status"] == "DEGRADED"
    assert health["empty_findings_means_no_bugs"] is False
    assert "未执行" in health["operator_note"] or "slice_budget" in health["operator_note"]


def test_pipeline_health_failed_safe_when_path_binding_blocks_all_execution():
    health = build_pipeline_health({
        "phases": {
            "execution": {
                "status": "completed",
                "executed": 0,
                "skip_telemetry": {
                    "reason_counts": {"missing_runtime_path_binding": 4, "precondition_not_met": 2},
                    "path_binding_misses": {"order_id": 2, "patient_id": 2},
                    "scenarios_blocked": 4,
                    "scenarios_with_http": 0,
                },
            }
        },
        "findings": [],
    })
    assert health["status"] == "FAILED_SAFE"
    assert health["empty_findings_means_no_bugs"] is False
    assert "binding" in health["operator_note"].lower() or "路径" in health["operator_note"]


def test_funnel_surfaces_unexecuted_and_binding_blockers():
    funnel = build_funnel({
        "phases": {
            "incremental_discovery": {
                "selected_slice_ids": ["s1"],
                "pending_slice_ids": ["s2", "s3"],
                "total_slices": 3,
                "stop_reason": "slice_budget_reached",
            },
            "execution": {
                "status": "completed",
                "executed": 1,
                "skip_telemetry": {
                    "reason_counts": {"missing_runtime_path_binding": 2},
                    "path_binding_misses": {"appointment_id": 2},
                },
            },
            "oracle": {"total_evaluated": 1, "violations_found": 0},
        },
        "findings": [],
        "behavior_slice_ledger": {
            "total_slices": 3,
            "selected_slice_ids": ["s1"],
            "pending_slice_ids": ["s2", "s3"],
            "stop_reason": "slice_budget_reached",
        },
    })
    reasons = {str(item.get("reason") or "") for item in funnel["top_blocking_reasons"]}
    assert "slice_budget_reached" in reasons or "unattempted_behavior_slices" in reasons
    assert "missing_runtime_path_binding" in reasons
    assert funnel["pipeline_health"]["status"] in {"DEGRADED", "FAILED_SAFE"}
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
    funnel = build_funnel(result)

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
    generation = next(
        stage for stage in funnel["stages"]
        if stage["name"] == "candidate_generation"
    )
    selection = next(
        stage for stage in funnel["stages"]
        if stage["name"] == "probe_selection"
    )
    assert generation["output"] == 2
    assert selection["output"] == 0
    assert funnel["pipeline_health"]["status"] == "FAILED_SAFE"
    assert funnel["pipeline_health"]["empty_findings_means_no_bugs"] is False


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
    normalized = __import__("json").loads(executable_document)

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
    normalized = __import__("json").loads(executable_document)

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
    v12_result = {
        "behavior_slices": [
            {
                "slice_id": "slice-1",
                "kind": "invariant",
                "endpoints": ["/api/orders/{id}"],
                "source_refs": [{"kind": "api", "quote": "orders"}],
            }
        ],
        "behavior_slice_ledger": {
            "campaign_id": "campaign-1",
            "round": 1,
            "selected_slice_ids": ["slice-1"],
            "attempted_slice_ids": ["slice-1"],
        },
        "phases": {
            "scenario_generation": {"selected_slice_ids": ["slice-1"]},
            "execution": {"status": "completed", "executed": 1},
        },
        "findings": [],
        "evidence_graphs": [],
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

    trace = ledger["traces"][0]
    assert trace["execution"]["trace_observed"] is True
    assert trace["execution"]["http_step_count"] == 1
    assert trace["execution"]["normalized_paths"] == ["/api/orders/{id}"]
    assert trace["verification"]["oracle_pass_votes"] == 1
    assert trace["outcome"] == "valid_success_control"
    assert "SELECTED_WITHOUT_EXECUTION_TRACE" not in trace["failure_signatures"]
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "do-not-persist" not in serialized
    assert "password" not in serialized
