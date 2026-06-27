from __future__ import annotations

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_scoreboard,
    _render_runtime_evidence_scoreboard_markdown,
)


def test_runtime_evidence_scoreboard_adds_actionable_gap_plan_from_real_metrics() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "gap-plan-demo",
        "decisions": [
            {"candidate_id": "READ-1", "decision": "execute_readonly"},
            {"candidate_id": "WRITE-1", "decision": "execute_write_sandbox"},
            {"candidate_id": "BLOCKED-1", "decision": "blocked", "reason": "missing_path_params:order_id"},
            {"candidate_id": "BLOCKED-2", "decision": "blocked", "reason": "write_sandbox_not_approved"},
        ],
        "observations": [
            {
                "candidate_id": "READ-1",
                "request": {"path": "/orders/srv_1"},
                "response": {"status_code": 200},
                "verification": {"verdict": "needs_more_evidence", "reason": "response did not contain the runtime-bound fixture id"},
                "fixture_receipts": [
                    {"status": "executed", "accepted": False, "runtime_binding": {"bound": False, "source": "setup_response"}},
                ],
                "cleanup_receipts": [{"status": "executed", "accepted": False}],
            }
        ],
        "write_observations": [
            {
                "candidate_id": "WRITE-1",
                "request": {"path": "/orders/srv_2", "body_runtime_binding": {"bound": False, "source": "runtime_target_request_body"}},
                "responses": [{"status_code": 200, "runtime_binding": {"bound": False, "source": "flow_step_response"}}],
                "snapshots": {"before": [{"status_code": 500}], "after": []},
                "verification": {"verdict": "inconclusive", "reason": "snapshot observer failed"},
                "fixture_receipts": [{"status": "executed", "accepted": True, "body_runtime_binding": {"bound": True, "source": "fixture_body"}}],
                "cleanup_receipts": [{"status": "executed", "accepted": True}],
            }
        ],
        "findings": [],
    }

    scoreboard = _build_runtime_evidence_scoreboard(report)

    assert scoreboard["execution_coverage_rate"] == 50.0
    assert scoreboard["target_response_rate"] == 100.0
    assert scoreboard["oracle_resolution_rate"] == 0.0
    assert scoreboard["evidence_maturity"]["level"] == "runtime_evidence_blocked"
    assert scoreboard["evidence_maturity"]["customer_ready"] is False

    action_types = {a["gap_type"] for a in scoreboard["recommended_next_actions"]}
    assert "low_execution_coverage" in action_types
    assert "runtime_binding_instability" in action_types
    assert "snapshot_observer_instability" in action_types
    assert "weak_runtime_oracle_resolution" in action_types
    assert "needs_more_evidence_backlog" in action_types


def test_runtime_evidence_scoreboard_marks_customer_ready_when_gates_pass() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "ready-demo",
        "decisions": [
            {"candidate_id": "READ-1", "decision": "execute_readonly"},
            {"candidate_id": "WRITE-1", "decision": "execute_write_sandbox"},
        ],
        "observations": [
            {
                "candidate_id": "READ-1",
                "request": {"path": "/orders/srv_1?include=audit"},
                "response": {"status_code": 403},
                "verification": {"verdict": "falsified_or_protected", "reason": "protected"},
                "fixture_receipts": [{"status": "executed", "accepted": True, "runtime_binding": {"bound": True, "source": "setup_response"}}],
                "cleanup_receipts": [{"status": "executed", "accepted": True}],
            }
        ],
        "write_observations": [
            {
                "candidate_id": "WRITE-1",
                "request": {"path": "/orders/srv_2?mode=strict", "body_runtime_binding": {"bound": True, "source": "runtime_target_request_body"}},
                "responses": [{"status_code": 200, "runtime_binding": {"bound": True, "source": "flow_step_response"}}],
                "snapshots": {"before": [{"status_code": 200}], "after": [{"status_code": 200}]},
                "verification": {"verdict": "validated_candidate", "reason": "business invariant failed"},
                "fixture_receipts": [{"status": "executed", "accepted": True, "body_runtime_binding": {"bound": True, "source": "fixture_body"}}],
                "cleanup_receipts": [{"status": "executed", "accepted": True}],
            }
        ],
        "findings": [{"finding_id": "GPF-0001"}],
    }

    scoreboard = _build_runtime_evidence_scoreboard(report)

    assert scoreboard["evidence_maturity"]["level"] == "customer_ready_runtime_evidence"
    assert scoreboard["evidence_maturity"]["customer_ready"] is True
    assert scoreboard["recommended_next_actions"] == []

    markdown = _render_runtime_evidence_scoreboard_markdown(scoreboard)
    assert "Evidence maturity gates" in markdown
    assert "customer-ready `True`" in markdown
    assert "execution_coverage_gate: `pass`" in markdown
