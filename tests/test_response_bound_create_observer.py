from __future__ import annotations

from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_runtime_support import (
    _declared_observation_path,
    _has_response_bound_create_observers,
)
from ai_test_asset_center.runtime_binding_graph import (
    declared_action_recreate_primaries,
    declared_effect_observers,
)


def test_has_response_bound_create_observers_for_identity_get() -> None:
    ops = {
        "op-create": {
            "id": "op-create",
            "method": "POST",
            "path": "/api/refunds",
            "request_example": {"orderId": "o1"},
            "source_refs": [{"kind": "endpoint_contract"}],
        },
        "op-get": {
            "id": "op-get",
            "method": "GET",
            "path": "/api/refunds/{id}",
            "source_refs": [{"kind": "endpoint_contract"}],
        },
    }
    assert _has_response_bound_create_observers(ops["op-create"], ops) is True
    # Pre-write identity GET cannot materialize; governance falls back to create path.
    assert _declared_observation_path("/api/refunds", ops) == ""


def test_register_observes_same_parent_concrete_get() -> None:
    observers = declared_effect_observers(
        {
            "id": "op-register",
            "method": "POST",
            "path": "/api/auth/register",
            "request_example": {"email": "a@b.com", "password": "x"},
            "source_refs": [{"kind": "endpoint_contract"}],
        },
        behavior_ir={
            "operations": [
                {
                    "id": "op-register",
                    "method": "POST",
                    "path": "/api/auth/register",
                    "request_example": {"email": "a@b.com", "password": "x"},
                    "source_refs": [{"kind": "endpoint_contract"}],
                },
                {
                    "id": "op-me",
                    "method": "GET",
                    "path": "/api/auth/me",
                    "source_refs": [{"kind": "endpoint_contract"}],
                },
            ],
        },
    )
    assert any(row.get("path") == "/api/auth/me" for row in observers)


def test_ephemeral_login_strips_write_only_observers() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-login-state",
            "risk_family": "validation",
            "property": {
                "operation_ref": "op-login",
                "actor_ref": "actor-buyer",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-login"],
            "required_observers": ["http_response", "entity_state", "business_effect"],
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [{
                "id": "op-login",
                "method": "POST",
                "path": "/api/auth/login",
                "read_write": "write",
                "request_example": {"email": "a@b.com", "password": "x"},
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
            "actors": [{
                "id": "actor-buyer",
                "role": "buyer",
                "credential_secret_ref": "secret_ref:test_accounts:buyer",
            }],
            "relations": [],
        },
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    kinds = {
        str(row.get("observer_id") or "")
        for row in (experiment.get("observers") or [])
        if isinstance(row, dict)
    }
    assert "entity_state" not in kinds
    assert "business_effect" not in kinds
    assert "http_response" in kinds


def test_read_state_obligation_strips_write_only_protocol_observers() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-read-state",
            "risk_family": "state",
            "property": {
                "operation_ref": "op-get-order",
                "actor_ref": "actor-buyer",
                "expression": {"kind": "postcondition", "operands": []},
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-get-order"],
            "required_observers": ["http_response", "before_state", "after_state"],
            "cleanup_requirement": {"required": False},
        },
        behavior_ir={
            "operations": [{
                "id": "op-get-order",
                "method": "GET",
                "path": "/api/orders/{id}",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-list-orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
            "actors": [{
                "id": "actor-buyer",
                "role": "buyer",
                "credential_secret_ref": "secret_ref:test_accounts:buyer",
            }],
            "relations": [],
        },
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    kinds = {
        str(row.get("observer_id") or "")
        for row in (experiment.get("observers") or [])
        if isinstance(row, dict)
    }
    assert "before_state" not in kinds
    assert "after_state" not in kinds
    assert "entity_state" not in kinds
    assert "http_response" in kinds


def test_release_recreates_via_unique_sibling_reserve() -> None:
    request_example = {"sku": "SKU-1", "qty": 1}
    ir = {
        "operations": [
            {
                "id": "reserve_stock",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "read_write": "write",
                "summary": "Reserve stock",
                "request_example": request_example,
                "source_refs": [{"source_id": "api", "locator": "POST /api/inventory/reserve"}],
            },
            {
                "id": "release_stock",
                "method": "POST",
                "path": "/api/inventory/release",
                "read_write": "write",
                "summary": "Release reserved stock",
                "request_example": request_example,
                "source_refs": [{"source_id": "api", "locator": "POST /api/inventory/release"}],
            },
            {
                "id": "consume_stock",
                "method": "POST",
                "path": "/api/inventory/consume",
                "read_write": "write",
                "summary": "Consume stock",
                "request_example": request_example,
                "source_refs": [{"source_id": "api", "locator": "POST /api/inventory/consume"}],
            },
            {
                "id": "read_stock",
                "method": "GET",
                "path": "/api/inventory/{sku}",
                "read_write": "read",
                "source_refs": [{"source_id": "api", "locator": "GET /api/inventory/{sku}"}],
            },
        ],
        "actors": [{"id": "operator", "role": "public"}],
        "relations": [],
    }
    recreate = declared_action_recreate_primaries(
        ir["operations"][1],
        behavior_ir=ir,
    )
    assert [row["operation_ref"] for row in recreate] == ["reserve_stock"]

    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl_release",
            "risk_family": "validation",
            "property": {
                "template": "schema_constraint",
                "operation_ref": "release_stock",
                "actor_ref": "operator",
            },
            "required_actors": ["operator"],
            "required_operations": ["release_stock"],
            "required_fixtures": [],
            "required_observers": ["http_response"],
            "cleanup_requirement": {"required": True},
            "source_refs": [{"source_id": "rule", "locator": "inventory release"}],
        },
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    assert experiment["cleanup_plan"][0]["operation_ref"] == "reserve_stock"
    assert experiment["cleanup_plan"][0]["mode"] == "recreate_compensated_resource"
