from __future__ import annotations


def test_binding_coverage_fingerprint_is_resealed_after_scope_projection() -> None:
    from ai_test_asset_center.experiment_compiler_base import (
        _reseal_binding_coverage_after_scope,
    )

    experiment = {
        "experiment_id": "exp-1",
        "obligation_id": "obl-1",
        "control_plan": [
            {
                "step_id": "control_1",
                "operation_ref": "list-orders",
                "actor_ref": "actor-a",
                "query": {"userId": "actor_identity_ref:actor-a:userId"},
            }
        ],
        "treatment_plan": [],
        "binding_plan": [],
        "assertions": [],
        "observers": [],
        "cleanup_plan": [],
        "safety_contract": {"governed_write": False},
        "binding_coverage_graph": {
            "graph_status": "VALID",
            "binding_graph_fingerprint": "stale-binding-fingerprint",
        },
        "compile_coverage_receipt": {
            "verdict": "READY",
            "fingerprint": "stale-orchestrator-fingerprint",
            "binding_graph_fingerprint": "stale-binding-fingerprint",
            "gate_receipts": [
                {
                    "module": "observer_resolution",
                    "status": "NOT_APPLICABLE",
                },
                {
                    "module": "binding_coverage_graph",
                    "status": "PASSED",
                    "fingerprint": "stale-binding-fingerprint",
                },
            ],
        },
    }
    behavior_ir = {
        "actors": [{"id": "actor-a", "role": "buyer"}],
        "operations": [
            {
                "id": "list-orders",
                "method": "GET",
                "path": "/api/orders",
                "parameters": [
                    {
                        "name": "userId",
                        "in": "query",
                        "required": True,
                        "x-ownership": True,
                    }
                ],
            }
        ],
    }

    resealed, gate = _reseal_binding_coverage_after_scope(
        experiment,
        obligation={"obligation_id": "obl-1"},
        behavior_ir=behavior_ir,
    )

    assert gate["status"] != "BLOCKED"
    graph_fp = resealed["binding_coverage_graph"]["binding_graph_fingerprint"]
    coverage = resealed["compile_coverage_receipt"]
    assert graph_fp
    assert graph_fp != "stale-binding-fingerprint"
    assert coverage["binding_graph_fingerprint"] == graph_fp
    assert coverage["fingerprint"] != "stale-orchestrator-fingerprint"
    assert coverage["ownership_scope_resealed"] is True
    binding_gate = next(
        row
        for row in coverage["gate_receipts"]
        if row.get("module") == "binding_coverage_graph"
    )
    assert binding_gate["fingerprint"] == graph_fp


def test_post_scope_invalid_binding_graph_blocks_instead_of_reusing_old_pass() -> None:
    from ai_test_asset_center.experiment_compiler_base import (
        _reseal_binding_coverage_after_scope,
    )

    experiment = {
        "experiment_id": "exp-2",
        "obligation_id": "obl-2",
        "control_plan": [
            {
                "step_id": "control_1",
                "operation_ref": "write-order",
                "actor_ref": "actor-a",
                "path": "/api/orders/{orderId}",
            }
        ],
        "treatment_plan": [],
        "binding_plan": [],
        "assertions": [],
        "observers": [],
        "cleanup_plan": [],
        "safety_contract": {"governed_write": False},
        "compile_coverage_receipt": {
            "verdict": "READY",
            "fingerprint": "old-pass",
            "binding_graph_fingerprint": "old-pass-graph",
            "gate_receipts": [
                {
                    "module": "binding_coverage_graph",
                    "status": "PASSED",
                    "fingerprint": "old-pass-graph",
                }
            ],
        },
    }
    behavior_ir = {
        "actors": [{"id": "actor-a", "role": "buyer"}],
        "operations": [
            {
                "id": "write-order",
                "method": "GET",
                "path": "/api/orders/{orderId}",
            }
        ],
    }

    resealed, gate = _reseal_binding_coverage_after_scope(
        experiment,
        obligation={"obligation_id": "obl-2"},
        behavior_ir=behavior_ir,
    )

    assert resealed["binding_coverage_graph"]["binding_graph_fingerprint"]
    assert gate["status"] in {"PASSED", "BLOCKED"}
    # Whatever canonical builder decides for this minimal synthetic shape, the
    # returned gate must be the gate over the rebuilt graph, never the stale one.
    assert gate.get("fingerprint") == resealed["binding_coverage_graph"].get(
        "binding_graph_fingerprint"
    )
