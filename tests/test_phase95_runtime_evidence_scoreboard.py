from __future__ import annotations

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_scoreboard,
    _render_runtime_evidence_scoreboard_markdown,
)


def test_runtime_evidence_scoreboard_counts_actual_setup_binding_snapshot_and_cleanup() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "scoreboard-demo",
        "decisions": [
            {"candidate_id": "READ-1", "decision": "execute_readonly"},
            {"candidate_id": "WRITE-1", "decision": "execute_write_sandbox"},
            {"candidate_id": "BLOCKED-1", "decision": "blocked", "reason": "missing_path_params:order_id"},
        ],
        "observations": [
            {
                "candidate_id": "READ-1",
                "request": {"path": "/orders/srv_1?include=audit"},
                "fixture_receipts": [
                    {"status": "executed", "accepted": True, "runtime_binding": {"bound": True, "source": "setup_response"}},
                ],
                "cleanup_receipts": [
                    {"status": "executed", "accepted": True},
                ],
                "response": {"status_code": 403},
                "verification": {"verdict": "falsified_or_protected", "reason": "anonymous rejected"},
            }
        ],
        "write_observations": [
            {
                "candidate_id": "WRITE-1",
                "request": {
                    "path": "/orders/srv_2?mode=strict",
                    "body_runtime_binding": {"bound": True, "source": "runtime_target_request_body"},
                },
                "fixture_receipts": [
                    {
                        "status": "executed",
                        "accepted": False,
                        "runtime_binding": {"bound": False, "source": "setup_response"},
                        "body_runtime_binding": {"bound": True, "source": "fixture_body"},
                    }
                ],
                "cleanup_receipts": [
                    {"status": "executed", "accepted": False, "error": "cleanup failed"},
                ],
                "responses": [
                    {
                        "status_code": 200,
                        "flow_path": "/orders/srv_2/approve?order=srv_2",
                        "runtime_binding": {"bound": True, "source": "flow_step_response"},
                        "request_body_runtime_binding": {"bound": True, "source": "step.body"},
                    }
                ],
                "snapshots": {
                    "before": [{"status_code": 200, "observer_kind": "resource_detail"}],
                    "after": [{"status_code": 500, "observer_kind": "resource_detail"}],
                },
                "verification": {"verdict": "validated_candidate", "reason": "state invariant failed"},
            }
        ],
        "findings": [{"finding_id": "GPF-0001"}],
    }

    scoreboard = _build_runtime_evidence_scoreboard(report)

    assert scoreboard["probe_count"] == 3
    assert scoreboard["executed_probe_count"] == 2
    assert scoreboard["target_http_response_count"] == 2
    assert scoreboard["decision_counts"] == {"execute_readonly": 1, "execute_write_sandbox": 1, "blocked": 1}
    assert scoreboard["verdict_counts"] == {"falsified_or_protected": 1, "validated_candidate": 1}
    assert scoreboard["fixture_setup_executed_count"] == 2
    assert scoreboard["fixture_setup_accepted_count"] == 1
    assert scoreboard["fixture_setup_success_rate"] == 50.0
    assert scoreboard["cleanup_executed_count"] == 2
    assert scoreboard["cleanup_accepted_count"] == 1
    assert scoreboard["snapshot_request_count"] == 2
    assert scoreboard["snapshot_accepted_count"] == 1
    assert scoreboard["runtime_binding_event_count"] == 6
    assert scoreboard["runtime_binding_success_count"] == 5
    assert scoreboard["query_bound_request_count"] == 3
    assert scoreboard["top_failure_or_gap_reasons"] == {"missing_path_params:order_id": 1}


def test_runtime_evidence_scoreboard_markdown_is_customer_readable() -> None:
    scoreboard = {
        "engine": "runtime_evidence_scoreboard_v1_phase95_runtime_ledger",
        "project_id": "demo",
        "execution_integrity_score": 81.5,
        "probe_count": 10,
        "executed_probe_count": 8,
        "target_http_response_count": 8,
        "decision_counts": {"execute_readonly": 3},
        "verdict_counts": {"validated_candidate": 2},
        "fixture_setup_accepted_count": 4,
        "fixture_setup_executed_count": 5,
        "fixture_setup_success_rate": 80.0,
        "runtime_binding_success_count": 7,
        "runtime_binding_event_count": 8,
        "runtime_binding_success_rate": 87.5,
        "snapshot_accepted_count": 5,
        "snapshot_request_count": 5,
        "snapshot_success_rate": 100.0,
        "cleanup_accepted_count": 4,
        "cleanup_executed_count": 4,
        "cleanup_success_rate": 100.0,
        "query_bound_request_count": 3,
        "runtime_binding_sources": {"setup_response": 4},
        "validated_candidate_count": 2,
        "protected_or_falsified_count": 1,
        "needs_more_evidence_count": 1,
        "inconclusive_count": 0,
        "finding_count": 2,
        "top_failure_or_gap_reasons": {"needs more observer evidence": 1},
    }

    markdown = _render_runtime_evidence_scoreboard_markdown(scoreboard)

    assert "Runtime Evidence Scoreboard" in markdown
    assert "execution integrity score" in markdown
    assert "fixture setup accepted/executed: 4/5" in markdown
    assert "runtime id/body binding success: 7/8" in markdown
    assert "needs more observer evidence: 1" in markdown
