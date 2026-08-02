"""WRP semantics after H12 step-binding: snapshot fields, compensator direction, recreate."""

from __future__ import annotations

from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.experiment_compiler_obligation_core import (
    _entity_for_operation,
)
from ai_test_asset_center.write_reversibility_contract import (
    _validate_exact_recreate,
    _validate_field_snapshot_restore,
)


def _actor(actor_id: str = "actor-buyer", role: str = "buyer") -> dict:
    return {
        "id": actor_id,
        "role": role,
        "credential_secret_ref": f"secret_ref:{role}",
        "account_status": "active",
    }


def test_patch_snapshot_uses_sibling_collection_post_writable_fields() -> None:
    """Empty PATCH example still restores when unique collection POST declares fields."""
    result = _validate_field_snapshot_restore(
        primary_method="PATCH",
        primary_path="/api/cart/items/{id}",
        primary_operation_ref="op-patch",
        cleanup_op={},
        cleanup_method="PATCH",
        cleanup_path="/api/cart/items/{id}",
        experiment={},
        ops={
            "op-patch": {
                "id": "op-patch",
                "method": "PATCH",
                "path": "/api/cart/items/{id}",
                "request_example": {},
            },
            "op-create": {
                "id": "op-create",
                "method": "POST",
                "path": "/api/cart/items",
                "request_example": {"sku": "SKU-1", "qty": 2},
            },
        },
    )
    assert result["kind"] == "field_snapshot_restore", result
    assert set(result["cleanup_request_contract"]["allowed_fields"]) == {"sku", "qty"}


def test_cancel_does_not_bind_create_as_its_own_cleanup() -> None:
    """compensates(from=cancel,to=create) cleans create — not the reverse."""
    ir = {
        "operations": [
            {
                "id": "op-create-order",
                "method": "POST",
                "path": "/api/orders",
                "read_write": "write",
                "request_example": {"items": [{"sku": "S1", "qty": 1}]},
                "source_refs": [{"source_id": "api", "locator": "POST orders"}],
            },
            {
                "id": "op-cancel",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
                "read_write": "write",
                "request_example": {},
                "source_refs": [{"source_id": "api", "locator": "POST cancel"}],
            },
            {
                "id": "op-get-order",
                "method": "GET",
                "path": "/api/orders/{id}",
                "read_write": "read",
                "source_refs": [{"source_id": "api", "locator": "GET order"}],
            },
        ],
        "actors": [_actor()],
        "entities": [
            {
                "id": "ent-order",
                "name": "order",
                "kind": "resource",
                "identity_fields": ["id"],
                "fields": [
                    {
                        "name": "id",
                        "semantic_type": "IDENTITY",
                        "database_bindings": [{"table": "orders", "column": "id"}],
                    },
                    {
                        "name": "status",
                        "semantic_type": "STATE",
                        "database_bindings": [{"table": "orders", "column": "status"}],
                    },
                ],
            }
        ],
        "relations": [
            {
                "id": "rel-cancel-compensates-create",
                "relation_type": "compensates",
                "from_ref": "op-cancel",
                "to_ref": "op-create-order",
                "operation_ref": "op-cancel",
                "effects": [{"cleanup_target_operation_ref": "op-create-order"}],
                "source_refs": [{"source_id": "api"}],
            },
            {
                "id": "rel-cancel-transitions-order",
                "relation_type": "transitions",
                "from_ref": "op-cancel",
                "to_ref": "ent-order",
                "operation_ref": "op-cancel",
                "source_refs": [{"source_id": "api"}],
            },
        ],
    }
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "probe-cancel",
            "risk_family": "state",
            "property": {
                "operation_ref": "op-cancel",
                "actor_ref": "actor-buyer",
                "from_state_ref": "state-open",
                "to_state_ref": "state-cancelled",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-cancel"],
            "required_observers": ["http_response", "before_state", "after_state"],
            "cleanup_requirement": {"required": True},
            "source_refs": [{"source_id": "api", "locator": "POST cancel"}],
        },
        behavior_ir=ir,
        environment_type="test",
        available_adapters={"http_api", "db_sql", "process_ledger"},
    )
    receipt = experiment.get("compile_receipt") or {}
    detail = str(receipt.get("detail") or "")
    assert "explicit_compensator_no_source_relation" not in detail, receipt
    cleanup = experiment.get("cleanup_plan") or []
    for row in cleanup:
        # Must not treat create-order as cleanup for cancel.
        assert row.get("operation_ref") != "op-create-order", cleanup
    if receipt.get("status") == "COMPILED":
        assert cleanup, cleanup
        assert any(
            row.get("action") == "declared_adapter_cleanup"
            or row.get("mode") in {"field_restore", "adapter_field_restore", "adapter_row_delete"}
            for row in cleanup
            if isinstance(row, dict)
        ), cleanup
    else:
        # Adapter may still block for missing observer/state binding — never the
        # inverted-compensator WRP code.
        assert receipt.get("reason_code") != "BLOCKED_NON_REVERSIBLE_WRITE" or (
            "explicit_compensator" not in detail
        ), receipt


def test_delete_recreate_compiles_when_create_has_source_body() -> None:
    ir = {
        "operations": [
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/api/cart/items/demo",
                "read_write": "write",
                "source_refs": [{"source_id": "api"}],
            },
            {
                "id": "op-create",
                "method": "POST",
                "path": "/api/cart/items",
                "read_write": "write",
                "request_example": {"sku": "SKU-1", "qty": 1},
                "source_refs": [{"source_id": "api"}],
            },
            {
                "id": "op-get",
                "method": "GET",
                "path": "/api/cart/items/demo",
                "read_write": "read",
                "source_refs": [{"source_id": "api"}],
            },
        ],
        "actors": [_actor()],
        "entities": [
            {
                "id": "ent-cart-item",
                "name": "cart_items",
                "identity_fields": ["id"],
                "fields": [
                    {
                        "name": "id",
                        "semantic_type": "IDENTITY",
                        "database_bindings": [{"table": "cart_items", "column": "id"}],
                    }
                ],
            }
        ],
        "relations": [],
    }
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-delete-recreate",
            "risk_family": "validation",
            "property": {
                "operation_ref": "op-delete",
                "actor_ref": "actor-buyer",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-delete"],
            "required_observers": ["http_response"],
            "cleanup_requirement": {
                "required": True,
                "operation_ref": "op-create",
                "mode": "recreate_compensated_resource",
            },
            "source_refs": [{"source_id": "api"}],
        },
        behavior_ir=ir,
        environment_type="test",
        available_adapters={"http_api", "db_sql", "process_ledger"},
    )
    receipt = experiment.get("compile_receipt") or {}
    assert receipt.get("status") == "COMPILED", receipt
    assert experiment["safety_contract"]["business_equivalence_allows_new_identity"] is True
    cleanup = experiment["cleanup_plan"]
    assert cleanup[0]["mode"] == "recreate_compensated_resource"
    assert cleanup[0]["compensates_operation_ref"] == "op-delete"


def test_exact_recreate_admits_source_recreate_body_without_safety_flag() -> None:
    result = _validate_exact_recreate(
        primary_method="DELETE",
        primary_path="/api/cart/items/{id}",
        cleanup_op_ref="op-create",
        cleanup_op={
            "id": "op-create",
            "method": "POST",
            "path": "/api/cart/items",
            "request_example": {"sku": "SKU-1", "qty": 1},
        },
        experiment={"safety_contract": {}},
        ops={},
    )
    assert result["kind"] == "exact_recreate", result


def test_entity_for_operation_follows_transitions_relation() -> None:
    ir = {
        "operations": [
            {
                "id": "op-register",
                "method": "POST",
                "path": "/api/auth/register",
                "request_example": {
                    "email": "a@b.c",
                    "password": "x",
                    "name": "n",
                    "phone": "1",
                },
            }
        ],
        "entities": [
            {
                "id": "ent-users",
                "name": "users",
                "kind": "resource",
                "identity_fields": ["id"],
                "fields": [
                    {
                        "name": "id",
                        "semantic_type": "IDENTITY",
                        "database_bindings": [{"table": "users", "column": "id"}],
                    },
                    {
                        "name": "email",
                        "database_bindings": [{"table": "users", "column": "email"}],
                    },
                    {
                        "name": "name",
                        "database_bindings": [{"table": "users", "column": "name"}],
                    },
                    {
                        "name": "phone",
                        "database_bindings": [{"table": "users", "column": "phone"}],
                    },
                ],
            }
        ],
        "relations": [],
    }
    # No path segment "users"; field↔column overlap (≥2) must bind.
    entity = _entity_for_operation(ir["operations"][0], ir)
    assert entity.get("name") == "users", entity
    assert "id" in entity.get("identity_fields", []), entity
