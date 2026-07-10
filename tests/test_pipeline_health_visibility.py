from __future__ import annotations

from ai_test_asset_center.discovery_funnel import build_funnel, build_pipeline_health


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
