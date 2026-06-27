from __future__ import annotations

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_customer_reproduction_pack,
    _render_runtime_customer_reproduction_pack_markdown,
)


def test_runtime_customer_reproduction_pack_packages_validated_finding_trace() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "repro-pack-demo",
        "findings": [
            {
                "finding_id": "GPF-0001",
                "candidate_id": "WRITE-READY",
                "title": "terminal state accepted for PATCH /orders/{order_id}",
                "risk_type": "terminal_state_transition_probe",
                "method": "PATCH",
                "path": "/orders/{order_id}",
                "confidence": 0.96,
                "evidence_grade": "A",
                "evidence_strength_score": 93,
                "reason": "cancelled order accepted payment",
                "violated_invariants": [{"kind": "terminal_state"}],
                "customer_triage": {"priority": "P0", "severity": "high"},
            }
        ],
        "runtime_evidence_probe_ledger": {
            "entries": [
                {
                    "candidate_id": "WRITE-READY",
                    "customer_ready": True,
                    "readiness_level": "customer_ready_candidate",
                    "fixture_setup": {"accepted_count": 1},
                    "snapshots": {"accepted_count": 2},
                    "cleanup": {"accepted_count": 1},
                    "gap_types": [],
                }
            ]
        },
        "write_observations": [
            {
                "candidate_id": "WRITE-READY",
                "method": "PATCH",
                "path": "/orders/srv_ready",
                "request": {
                    "path": "/orders/srv_ready?mode=strict",
                    "body": {"order_id": "srv_ready", "status": "paid"},
                    "body_runtime_binding": {"bound": True, "source": "runtime_target_request_body"},
                },
                "fixture_receipts": [
                    {
                        "status": "executed",
                        "purpose": "create_order",
                        "accepted": True,
                        "method": "POST",
                        "path": "/orders",
                        "body_runtime_binding": {"bound": True, "source": "fixture_body"},
                        "runtime_binding": {"bound": True, "path_params": ["order_id"]},
                        "response": {"status_code": 201, "payload": {"order_id": "srv_ready"}},
                    }
                ],
                "snapshots": {
                    "before": [
                        {
                            "method": "GET",
                            "path": "/orders/srv_ready",
                            "observer_kind": "resource_detail",
                            "response": {"status_code": 200, "payload": {"status": "cancelled"}},
                        }
                    ],
                    "after": [
                        {
                            "method": "GET",
                            "path": "/orders/srv_ready",
                            "observer_kind": "resource_detail",
                            "response": {"status_code": 200, "payload": {"status": "paid"}},
                        }
                    ],
                },
                "responses": [
                    {
                        "attempt": 1,
                        "status_code": 200,
                        "payload": {"order_id": "srv_ready", "status": "paid"},
                        "runtime_binding": {"bound": True, "source": "flow_step_response"},
                    }
                ],
                "cleanup_receipts": [
                    {
                        "status": "executed",
                        "purpose": "delete_order",
                        "accepted": True,
                        "method": "DELETE",
                        "path": "/orders/srv_ready",
                        "response": {"status_code": 204, "payload": {}},
                    }
                ],
                "verification": {"verdict": "validated_candidate", "reason": "cancelled order accepted payment"},
            }
        ],
    }

    pack = _build_runtime_customer_reproduction_pack(report)

    assert pack["engine"] == "runtime_customer_reproduction_pack_v1_phase95"
    assert pack["status"] == "ready"
    assert pack["finding_count"] == 1
    assert pack["customer_ready_reproduction_count"] == 1
    item = pack["packages"][0]
    assert item["finding_id"] == "GPF-0001"
    assert item["customer_ready"] is True
    assert item["runtime_evidence"]["runtime_binding_bound_count"] == 4
    phases = [step["phase"] for step in item["reproduction_trace"]]
    assert phases == ["setup", "snapshot_before", "snapshot_after", "target_flow_step", "cleanup"]
    assert item["reproduction_trace"][0]["curl_template"].startswith("curl -X POST")
    assert "$BASE_URL/orders/srv_ready" in item["reproduction_trace"][-1]["curl_template"]


def test_runtime_customer_reproduction_pack_empty_and_markdown_are_customer_readable() -> None:
    pack = _build_runtime_customer_reproduction_pack({"project_id": "empty-demo", "findings": []})

    assert pack["status"] == "empty_no_validated_runtime_findings"
    assert pack["finding_count"] == 0

    markdown = _render_runtime_customer_reproduction_pack_markdown(pack)
    assert "Runtime Customer Reproduction Pack" in markdown
    assert "No validated runtime findings" in markdown
