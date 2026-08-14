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
            "response_schema": {
                "201": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Refund"}
                        }
                    }
                }
            },
            "source_refs": [{"kind": "endpoint_contract"}],
        },
        "op-get": {
            "id": "op-get",
            "method": "GET",
            "path": "/api/refunds/{id}",
            "response_schema": {
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Refund"}
                        }
                    }
                }
            },
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
            "entities": [{"id": "entity-session"}],
            "relations": [
                {
                    "relation_type": "produces",
                    "from_ref": "op-register",
                    "to_ref": "entity-session",
                    "source_refs": [{"source_id": "api-doc"}],
                    "status": "accepted",
                },
                {
                    "relation_type": "observes",
                    "from_ref": "op-me",
                    "to_ref": "entity-session",
                    "source_refs": [{"source_id": "api-doc"}],
                    "status": "accepted",
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
                "field": "email",
                "validation_constraint": "type:string",
                "validation_constraint_source": "request_schema",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-login"],
            "required_observers": ["http_response", "entity_state", "business_effect"],
            # SPEC v1.1 §9: Ephemeral operations need exemption contract
            "cleanup_requirement": {"required": False},
            "cleanup_exemption_contract": {
                "kind": "ephemeral_session",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                "persistent_effect_absent": True,
                "verification_basis": "source_declared",
            },
        },
        behavior_ir={
            "operations": [{
                "id": "op-login",
                "method": "POST",
                "path": "/api/auth/login",
                "read_write": "write",
                "request_example": {"email": "a@b.com", "password": "x"},
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "password": {"type": "string"},
                    },
                },
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


def test_read_state_obligation_without_source_assertion_stays_blocked() -> None:
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
    assert experiment["compile_receipt"] == {
        "status": "BLOCKED",
        "reason_code": "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
        "detail": "postcondition_missing_bound_field_or_expected_value",
    }
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
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "integer"},
                    },
                },
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
    assert recreate == []

    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl_release",
            "risk_family": "validation",
            "property": {
                "template": "schema_constraint",
                "operation_ref": "release_stock",
                "actor_ref": "operator",
                "field": "sku",
                "validation_constraint": "type:string",
                "validation_constraint_source": "request_schema",
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
    # SPEC v1.1 §12.2: Cancel/Reject → Collection Create forbidden without explicit proof.
    # Without explicit compensates relation, the write degrades to accepted residue on
    # a declared non-production target (the compiler no longer blocks such writes).
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    assert experiment["cleanup_plan"]
    assert experiment["cleanup_plan"][0]["action"] == "accepted_residue"
    assert experiment["cleanup_plan"][0]["mode"] == "accepted_residue_no_cleanup"
