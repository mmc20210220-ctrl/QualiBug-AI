from __future__ import annotations

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_customer_reproduction_pack,
    _render_runtime_customer_reproduction_pack_markdown,
)


def test_runtime_reproduction_pack_does_not_overclaim_customer_ready_without_trace() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "repro-readiness-gap-demo",
        "findings": [
            {
                "finding_id": "GPF-STALE",
                "candidate_id": "STALE-VALIDATED",
                "title": "stale finding should not be customer ready without runtime trace",
                "risk_type": "auth_boundary",
                "method": "GET",
                "path": "/orders/{order_id}",
                "confidence": 0.95,
            }
        ],
        "runtime_evidence_probe_ledger": {
            "entries": [
                {
                    "candidate_id": "STALE-VALIDATED",
                    "customer_ready": True,
                    "readiness_level": "customer_ready_candidate",
                    "gap_types": [],
                }
            ]
        },
    }

    pack = _build_runtime_customer_reproduction_pack(report)

    assert pack["finding_count"] == 1
    assert pack["customer_ready_reproduction_count"] == 0
    assert pack["blocked_reproduction_count"] == 1
    assert pack["status"] == "blocked_reproduction_evidence_gap"
    item = pack["packages"][0]
    assert item["customer_ready"] is False
    assert item["readiness_level"] == "not_validated_runtime_finding"
    blockers = item["reproduction_readiness_gate"]["blockers"]
    assert "missing_runtime_observation" in blockers
    assert "missing_reproduction_trace" in blockers
    assert "missing_target_reproduction_step" in blockers


def test_runtime_reproduction_readiness_gate_blocks_validated_findings_with_ledger_gaps() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "repro-readiness-binding-gap-demo",
        "findings": [
            {
                "finding_id": "GPF-GAP",
                "candidate_id": "WRITE-GAP",
                "title": "validated finding still has binding evidence gap",
                "risk_type": "state_transition",
                "method": "PATCH",
                "path": "/orders/{order_id}",
                "confidence": 0.96,
            }
        ],
        "runtime_evidence_probe_ledger": {
            "entries": [
                {
                    "candidate_id": "WRITE-GAP",
                    "customer_ready": False,
                    "readiness_level": "evidence_gap",
                    "gap_types": ["runtime_binding_not_fully_bound"],
                    "fixture_setup": {"accepted_count": 1},
                    "snapshots": {"accepted_count": 2},
                    "cleanup": {"accepted_count": 1},
                }
            ]
        },
        "write_observations": [
            {
                "candidate_id": "WRITE-GAP",
                "method": "PATCH",
                "path": "/orders/srv_gap",
                "request": {
                    "path": "/orders/srv_gap",
                    "body": {"order_id": "qb_auto_stale"},
                    "body_runtime_binding": {"bound": False, "source": "runtime_target_request_body"},
                },
                "response": {"status_code": 200, "payload": {"order_id": "srv_gap"}},
                "verification": {"verdict": "validated_candidate", "reason": "terminal state accepted mutation"},
            }
        ],
    }

    pack = _build_runtime_customer_reproduction_pack(report)

    assert pack["customer_ready_reproduction_count"] == 0
    assert pack["reproduction_readiness_blocker_counts"]["probe_ledger_has_evidence_gaps"] == 1
    assert pack["reproduction_readiness_blocker_counts"]["runtime_binding_not_fully_bound"] == 1
    item = pack["packages"][0]
    assert item["readiness_level"] == "validated_but_reproduction_gap"
    assert item["reproduction_readiness_gate"]["checks"]["target_http_statuses"] == [200]
    assert item["reproduction_readiness_gate"]["checks"]["runtime_binding_unbound_count"] == 1

    markdown = _render_runtime_customer_reproduction_pack_markdown(pack)
    assert "blocked reproductions: 1" in markdown
    assert "runtime_binding_not_fully_bound" in markdown
