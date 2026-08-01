from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.process_graph_cleanup_executor import (
    GRAPH_CLEANUP_BINDING_UNRESOLVED,
    execute_process_graph_cleanup,
)
from ai_test_asset_center.process_graph_runtime import (
    GRAPH_TARGET_NOT_APPROVED,
    prepare_graph_runtime,
)
from ai_test_asset_center.process_graph_write_contract import (
    GRAPH_WRITE_COMPENSATION_UNRESOLVED,
    GRAPH_WRITE_OBSERVER_UNRESOLVED,
    finalize_process_graph_write_contract,
)


def _source_ref(name: str) -> list[dict]:
    return [{"source_id": "api", "kind": "api_operation", "locator": name}]


def _ir(
    *,
    include_observer: bool = True,
    include_compensation_relation: bool = True,
) -> dict:
    operations = [
        {
            "id": "op-read-seed",
            "method": "GET",
            "path": "/seed",
            "read_write": "read",
            "source_refs": _source_ref("GET /seed"),
        },
        {
            "id": "op-create-order",
            "method": "POST",
            "path": "/orders",
            "read_write": "write",
            "request_example": {"sku": "SKU-1"},
            "source_refs": _source_ref("POST /orders"),
        },
        {
            "id": "op-delete-order",
            "method": "DELETE",
            "path": "/orders/{order_id}",
            "read_write": "write",
            "source_refs": _source_ref("DELETE /orders/{order_id}"),
        },
    ]
    if include_observer:
        operations.append(
            {
                "id": "op-list-orders",
                "method": "GET",
                "path": "/orders",
                "read_write": "read",
                "source_refs": _source_ref("GET /orders"),
            }
        )
    relations = []
    if include_compensation_relation:
        relations.append(
            {
                "id": "rel-delete-compensates-create",
                "relation_type": "compensates",
                "from_ref": "op-delete-order",
                "to_ref": "op-create-order",
                "operation_ref": "op-delete-order",
                "source_refs": [
                    {"source_id": "requirements", "locator": "order cleanup"}
                ],
            }
        )
    return {
        "operations": operations,
        "actors": [{"id": "actor-writer", "role": "public"}],
        "relations": relations,
    }


def _graph(*, system_ref: str = "orders") -> dict:
    return {
        "schema_version": "qualibug.process-execution-graph.v1",
        "execution_graph_id": "graph-order-create",
        "process_id": "process-order-create",
        "nodes": [
            {
                "node_id": "read-seed",
                "step_id": "read-seed",
                "operation_ref": "op-read-seed",
                "actor_ref": "actor-writer",
                "system_ref": system_ref,
                "method": "GET",
                "path": "/seed",
                "output_binding_specs": [],
            },
            {
                "node_id": "create-order",
                "step_id": "create-order",
                "operation_ref": "op-create-order",
                "actor_ref": "actor-writer",
                "system_ref": system_ref,
                "method": "POST",
                "path": "/orders",
                "compensation_operation_ref": "op-delete-order",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "order_id",
                        "json_path": "$.order_id",
                    }
                ],
            },
        ],
        "edges": [
            {
                "edge_id": "edge-seed-create",
                "source_node_id": "read-seed",
                "target_node_id": "create-order",
                "relation_type": "THEN",
            }
        ],
        "topological_order": ["read-seed", "create-order"],
        "start_node_ids": ["read-seed"],
        "terminal_node_ids": ["create-order"],
        "wait_contracts": [],
    }


def _experiment(*, system_ref: str = "orders") -> dict:
    graph = _graph(system_ref=system_ref)
    return {
        "experiment_id": "exp-graph-write",
        "obligation_id": "obl-graph-write",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": node["node_id"],
                "operation_ref": node["operation_ref"],
                "actor_ref": node["actor_ref"],
                "system_ref": node["system_ref"],
                "method": node["method"],
                "path": node["path"],
                "_execution_graph": graph,
            }
            for node in graph["nodes"]
        ],
        "cleanup_plan": [],
        "observers": [{"observer_id": "http_response"}],
        "safety_contract": {"governed_write": False},
        "source_refs": [
            {"source_id": "requirements", "locator": "order flow"}
        ],
    }


def _primary_runtime_contract(*, write: bool = True) -> dict:
    return {
        "status": "approved",
        "system_ref": "orders",
        "requested_base_url": "https://orders.test.example",
        "approved_base_url": "https://orders.test.example",
        "environment_type": "test",
        "environment_ref": "orders-test",
        "execution_mode": (
            "approved_sandbox_write" if write else "safe_read_only"
        ),
    }


def _secondary_runtime_contract() -> dict:
    return {
        "status": "approved",
        "system_ref": "erp",
        "requested_base_url": "https://erp.test.example",
        "approved_base_url": "https://erp.test.example",
        "environment_type": "test",
        "environment_ref": "erp-test",
        "execution_mode": "approved_sandbox_write",
        "approved_targets": {
            "orders": {
                "status": "approved",
                "system_ref": "orders",
                "requested_base_url": "https://orders.test.example",
                "approved_base_url": "https://orders.test.example",
                "environment_type": "test",
                "environment_ref": "orders-test",
                "execution_mode": "approved_sandbox_write",
                "actor_token_keys": {
                    "actor-writer": "orders:actor-writer"
                },
            }
        },
    }


def _accepted_source_step(*, system_ref: str = "orders") -> dict:
    return {
        "phase": "treatment",
        "step_id": "create-order",
        "operation_ref": "op-create-order",
        "actor_ref": "actor-writer",
        "system_ref": system_ref,
        "method": "POST",
        "path": "/orders",
        "status_code": 201,
        "body": {"order_id": "ORD-9"},
        "governance_receipt": {
            "accepted": True,
            "before": {"status": 200, "body": []},
            "write": {"status": 201, "body": {"order_id": "ORD-9"}},
            "after": {"status": 200, "body": [{"order_id": "ORD-9"}]},
            "audit_path": "audit.jsonl",
            "audit_record": {"phase": "write", "order_id": "ORD-9"},
        },
    }


def _cleanup_result(**kwargs):
    return {
        "accepted": True,
        "before": {"status": 200, "body": [{"order_id": "ORD-9"}]},
        "write": {"status": 204, "body": {}},
        "after": {"status": 200, "body": []},
        "audit_path": "audit.jsonl",
        "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
    }


def test_final_compiler_promotes_read_first_graph_to_governed_write() -> None:
    result = finalize_process_graph_write_contract(_experiment(), _ir())

    assert result["compile_receipt"]["status"] == "COMPILED", result["compile_receipt"]
    assert result["safety_contract"]["governed_write"] is True
    assert result["safety_contract"]["cleanup_authority"] == (
        "process_graph_write_contract"
    )
    contract = result["process_graph_write_contract"]
    assert contract["write_step_ids"] == ["create-order"]
    assert contract["cleanup_order"] == ["create-order"]
    cleanup = result["cleanup_plan"][0]
    assert cleanup["source_step_id"] == "create-order"
    assert cleanup["operation_ref"] == "op-delete-order"
    assert cleanup["system_ref"] == "orders"
    assert cleanup["action"] == "source_declared_compensation"
    assert cleanup["mode"] == "compensating_transition"
    assert cleanup["authority_relation_ref"] == (
        "rel-delete-compensates-create"
    )
    assert cleanup["binding_specs"] == [
        {
            "target": "order_id",
            "source": "write_response",
            "canonical_field_id": "order_id",
            "source_path": "$.order_id",
        }
    ]
    after_state = next(
        row for row in result["observers"] if row["observer_id"] == "after_state"
    )
    assert after_state["resolver_operations"] == [
        {
            "operation_ref": "op-list-orders",
            "method": "GET",
            "path": "/orders",
            "source_declared": True,
        }
    ]
    assert result["write_reversibility_proof"]["proof_status"] == "PROVEN"


def test_graph_write_without_unique_effect_observer_is_blocked() -> None:
    result = finalize_process_graph_write_contract(
        _experiment(), _ir(include_observer=False)
    )
    assert result["compile_receipt"]["status"] == "BLOCKED"
    assert result["compile_receipt"]["reason_code"] == (
        GRAPH_WRITE_OBSERVER_UNRESOLVED
    )
    assert result["treatment_plan"] == []


def test_graph_write_without_explicit_compensation_relation_is_blocked() -> None:
    result = finalize_process_graph_write_contract(
        _experiment(),
        _ir(include_compensation_relation=False),
    )
    assert result["compile_receipt"]["status"] == "BLOCKED"
    assert result["compile_receipt"]["reason_code"] == (
        GRAPH_WRITE_COMPENSATION_UNRESOLVED
    )
    assert "explicit_compensator_relation_unresolved" in (
        result["compile_receipt"]["detail"]
    )


def test_runtime_allows_only_write_approved_graph_target() -> None:
    compiled = finalize_process_graph_write_contract(_experiment(), _ir())
    ops = {row["id"]: row for row in _ir()["operations"]}

    ready = prepare_graph_runtime(
        graph=compiled["execution_graph"],
        treatment_plan=compiled["treatment_plan"],
        ops=ops,
        base_url="https://orders.test.example",
        runtime_contract=_primary_runtime_contract(write=True),
    )
    assert ready["status"] == "READY", ready
    assert ready["target_contexts"]["create-order"]["write_allowed"] is True

    blocked = prepare_graph_runtime(
        graph=compiled["execution_graph"],
        treatment_plan=compiled["treatment_plan"],
        ops=ops,
        base_url="https://orders.test.example",
        runtime_contract=_primary_runtime_contract(write=False),
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason_code"] == GRAPH_TARGET_NOT_APPROVED


def test_graph_cleanup_uses_exact_path_and_restoration_proof(tmp_path: Path) -> None:
    exp = finalize_process_graph_write_contract(_experiment(), _ir())
    calls: list[dict] = []

    result = execute_process_graph_cleanup(
        exp=exp,
        steps_out=[_accepted_source_step()],
        observations={},
        contract_evidence_receipts=[],
        request_bodies_for_cleanup={"create-order": {"sku": "SKU-1"}},
        runtime_bindings={},
        cleanup_failures=0,
        actors={"actor-writer": {"id": "actor-writer", "role": "public"}},
        tokens={},
        eid="exp-graph-write",
        oid="obl-graph-write",
        resolved_campaign_id="campaign",
        resolved_execution_id="execution",
        campaign_id="campaign",
        root=tmp_path,
        project="project",
        base_url="https://orders.test.example",
        runtime_contract=_primary_runtime_contract(write=True),
        execute_governed_control_write=lambda **kwargs: (
            calls.append(kwargs) or _cleanup_result(**kwargs)
        ),
        sandbox_write_allowed=lambda **kwargs: (True, ""),
    )

    assert result["cleanup_failures"] == 0
    assert len(calls) == 1
    assert calls[0]["base_url"] == "https://orders.test.example"
    assert calls[0]["path"] == "/orders/ORD-9"
    assert calls[0]["observation_path"] == "/orders"
    receipt = result["process_graph_cleanup_receipts"][0]
    assert receipt["status"] == "COMPLETED"
    assert receipt["evidence"]["source_step_id"] == "create-order"
    assert receipt["evidence"]["restoration_verified"] is True


def test_graph_cleanup_uses_secondary_target_and_isolated_token(tmp_path: Path) -> None:
    exp = finalize_process_graph_write_contract(
        _experiment(system_ref="orders"),
        _ir(),
    )
    calls: list[dict] = []

    result = execute_process_graph_cleanup(
        exp=exp,
        steps_out=[_accepted_source_step(system_ref="orders")],
        observations={},
        contract_evidence_receipts=[],
        request_bodies_for_cleanup={"create-order": {"sku": "SKU-1"}},
        runtime_bindings={},
        cleanup_failures=0,
        actors={
            "actor-writer": {
                "id": "actor-writer",
                "role": "writer",
                "credential_secret_ref": "primary:actor-writer",
            }
        },
        tokens={
            "primary:actor-writer": "erp-token",
            "orders:actor-writer": "orders-token",
        },
        eid="exp-graph-write",
        oid="obl-graph-write",
        resolved_campaign_id="campaign",
        resolved_execution_id="execution",
        campaign_id="campaign",
        root=tmp_path,
        project="project",
        base_url="https://erp.test.example",
        runtime_contract=_secondary_runtime_contract(),
        execute_governed_control_write=lambda **kwargs: (
            calls.append(kwargs) or _cleanup_result(**kwargs)
        ),
        sandbox_write_allowed=lambda **kwargs: (True, ""),
    )

    assert result["cleanup_failures"] == 0
    assert len(calls) == 1
    assert calls[0]["base_url"] == "https://orders.test.example"
    assert calls[0]["actor_token"] == "orders-token"
    assert calls[0]["actor_token"] != "erp-token"


def test_graph_cleanup_never_guesses_missing_identity(tmp_path: Path) -> None:
    exp = finalize_process_graph_write_contract(_experiment(), _ir())
    source = _accepted_source_step()
    source["body"] = {"id": "WRONG-ID"}
    source["governance_receipt"]["write"]["body"] = {"id": "WRONG-ID"}
    calls: list[dict] = []

    result = execute_process_graph_cleanup(
        exp=exp,
        steps_out=[source],
        observations={},
        contract_evidence_receipts=[],
        request_bodies_for_cleanup={"create-order": {"sku": "SKU-1"}},
        runtime_bindings={},
        cleanup_failures=0,
        actors={"actor-writer": {"id": "actor-writer", "role": "public"}},
        tokens={},
        eid="exp-graph-write",
        oid="obl-graph-write",
        resolved_campaign_id="campaign",
        resolved_execution_id="execution",
        campaign_id="campaign",
        root=tmp_path,
        project="project",
        base_url="https://orders.test.example",
        runtime_contract=_primary_runtime_contract(write=True),
        execute_governed_control_write=lambda **kwargs: calls.append(kwargs),
        sandbox_write_allowed=lambda **kwargs: (True, ""),
    )

    assert calls == []
    assert result["cleanup_failures"] == 1
    receipt = result["process_graph_cleanup_receipts"][0]
    assert receipt["status"] == "FAILED"
    assert receipt["evidence"]["reason_code"] == (
        GRAPH_CLEANUP_BINDING_UNRESOLVED
    )
    assert receipt["evidence"]["request_reached_transport"] is False


def test_graph_cleanup_prefers_step_scoped_response_over_global_binding_collision(
    tmp_path: Path,
) -> None:
    exp = finalize_process_graph_write_contract(
        _experiment(system_ref="orders"),
        _ir(),
    )
    calls: list[dict] = []

    result = execute_process_graph_cleanup(
        exp=exp,
        steps_out=[_accepted_source_step(system_ref="orders")],
        observations={},
        contract_evidence_receipts=[],
        request_bodies_for_cleanup={"create-order": {"sku": "SKU-1"}},
        # A different system may already have published the same canonical
        # field name. The cleanup contract for create-order is step-scoped and
        # must use its own governed write response (ORD-9), not this value.
        runtime_bindings={"order_id": "PAYMENT-SYSTEM-ORDER-ID"},
        cleanup_failures=0,
        actors={
            "actor-writer": {
                "id": "actor-writer",
                "role": "writer",
                "credential_secret_ref": "primary:actor-writer",
            }
        },
        tokens={
            "primary:actor-writer": "erp-token",
            "orders:actor-writer": "orders-token",
        },
        eid="exp-graph-write",
        oid="obl-graph-write",
        resolved_campaign_id="campaign",
        resolved_execution_id="execution",
        campaign_id="campaign",
        root=tmp_path,
        project="project",
        base_url="https://erp.test.example",
        runtime_contract=_secondary_runtime_contract(),
        execute_governed_control_write=lambda **kwargs: (
            calls.append(kwargs) or _cleanup_result(**kwargs)
        ),
        sandbox_write_allowed=lambda **kwargs: (True, ""),
    )

    assert result["cleanup_failures"] == 0
    assert len(calls) == 1
    assert calls[0]["base_url"] == "https://orders.test.example"
    assert calls[0]["path"] == "/orders/ORD-9"
