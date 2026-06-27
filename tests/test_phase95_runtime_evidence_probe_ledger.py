from __future__ import annotations

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_probe_ledger,
    _render_runtime_evidence_probe_ledger_markdown,
)


def test_runtime_probe_ledger_maps_global_gaps_back_to_candidate_ids() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "probe-ledger-demo",
        "decisions": [
            {"candidate_id": "READ-OK", "decision": "execute_readonly", "method": "GET", "path": "/orders/{order_id}"},
            {"candidate_id": "WRITE-GAP", "decision": "execute_write_sandbox", "method": "PATCH", "path": "/orders/{order_id}"},
            {"candidate_id": "BLOCKED-1", "decision": "blocked", "reason": "missing_path_params:tenant_id"},
        ],
        "observations": [
            {
                "candidate_id": "READ-OK",
                "method": "GET",
                "path": "/orders/srv_1",
                "request": {"path": "/orders/srv_1?include=audit"},
                "response": {"status_code": 403},
                "verification": {"verdict": "falsified_or_protected", "reason": "anonymous rejected"},
                "fixture_receipts": [
                    {"status": "executed", "accepted": True, "runtime_binding": {"bound": True, "source": "setup_response"}},
                ],
                "cleanup_receipts": [{"status": "executed", "accepted": True}],
            }
        ],
        "write_observations": [
            {
                "candidate_id": "WRITE-GAP",
                "method": "PATCH",
                "path": "/orders/srv_2",
                "request": {
                    "path": "/orders/srv_2",
                    "body_runtime_binding": {"bound": False, "source": "runtime_target_request_body"},
                },
                "responses": [
                    {"status_code": 200, "runtime_binding": {"bound": True, "source": "flow_step_response"}},
                ],
                "snapshots": {"before": [{"status_code": 200}], "after": [{"status_code": 500}]},
                "verification": {"verdict": "needs_more_evidence", "reason": "snapshot observer failed"},
                "fixture_receipts": [{"status": "executed", "accepted": False}],
                "cleanup_receipts": [{"status": "executed", "accepted": False}],
            }
        ],
    }

    ledger = _build_runtime_evidence_probe_ledger(report)

    assert ledger["probe_count"] == 3
    assert ledger["entry_count"] == 3
    assert ledger["blocked_probe_count"] == 1
    assert ledger["protected_probe_count"] == 1
    assert ledger["evidence_gap_probe_count"] == 1
    assert ledger["customer_ready_probe_count"] == 0
    assert ledger["top_probe_gap_types"]["blocked_decision"] == 1
    assert ledger["top_probe_gap_types"]["runtime_binding_not_fully_bound"] == 1
    assert ledger["top_probe_gap_types"]["snapshot_not_fully_accepted"] == 1

    entries = {entry["candidate_id"]: entry for entry in ledger["entries"]}
    assert entries["BLOCKED-1"]["readiness_level"] == "blocked_before_execution"
    assert entries["BLOCKED-1"]["gap_types"] == ["blocked_decision", "blocked:missing_path_params:tenant_id"]
    assert entries["WRITE-GAP"]["runtime_binding"]["success_rate"] == 50.0
    assert entries["WRITE-GAP"]["fixture_setup"]["accepted_count"] == 0
    assert "Fix disposable fixture setup data" in entries["WRITE-GAP"]["next_action"]


def test_runtime_probe_ledger_marks_customer_ready_validated_candidates_and_renders_actions() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "ready-ledger-demo",
        "decisions": [
            {"candidate_id": "WRITE-READY", "decision": "execute_write_sandbox", "method": "PATCH", "path": "/orders/{order_id}"},
        ],
        "write_observations": [
            {
                "candidate_id": "WRITE-READY",
                "method": "PATCH",
                "path": "/orders/srv_ready",
                "request": {
                    "path": "/orders/srv_ready?mode=strict",
                    "body_runtime_binding": {"bound": True, "source": "runtime_target_request_body"},
                },
                "responses": [
                    {"status_code": 200, "runtime_binding": {"bound": True, "source": "flow_step_response"}},
                ],
                "snapshots": {"before": [{"status_code": 200}], "after": [{"status_code": 200}]},
                "verification": {"verdict": "validated_candidate", "confidence": 0.96, "reason": "business invariant failed"},
                "fixture_receipts": [
                    {"status": "executed", "accepted": True, "body_runtime_binding": {"bound": True, "source": "fixture_body"}},
                ],
                "cleanup_receipts": [{"status": "executed", "accepted": True}],
            }
        ],
    }

    ledger = _build_runtime_evidence_probe_ledger(report)

    assert ledger["customer_ready_probe_count"] == 1
    assert ledger["readiness_counts"] == {"customer_ready_candidate": 1}
    entry = ledger["entries"][0]
    assert entry["customer_ready"] is True
    assert entry["gap_types"] == []
    assert entry["target_http_statuses"] == [200]
    assert "Package the reproduction trace" in entry["next_action"]

    markdown = _render_runtime_evidence_probe_ledger_markdown(ledger)
    assert "Runtime Evidence Probe Ledger" in markdown
    assert "WRITE-READY" in markdown
    assert "customer_ready_candidate" in markdown
    assert "Package the reproduction trace" in markdown
