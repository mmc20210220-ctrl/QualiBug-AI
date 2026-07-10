from __future__ import annotations

from ai_test_asset_center.discovery_funnel import (
    build_funnel,
    build_pipeline_health,
    reconcile_product_pipeline_health,
)
from ai_test_asset_center.v12_pipeline import (
    _publish_behavior_contract_snapshot,
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

