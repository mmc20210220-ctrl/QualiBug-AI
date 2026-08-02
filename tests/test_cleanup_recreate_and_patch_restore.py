"""DELETE→recreate restoration proof and PATCH→snapshot cleanup preference."""

from __future__ import annotations

from ai_test_asset_center.experiment_cleanup import _cleanup_restores_governed_write
from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)


def test_cleanup_restores_delete_via_accepted_recreate() -> None:
    original = {
        "accepted": True,
        "method": "DELETE",
        "path": "/api/cart/items/item-abc-001",
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"operation_phase": "experiment_treatment"},
        "before": {
            "status": 200,
            "body": {"items": [{"id": "item-abc-001", "sku": "SKU-1", "qty": 2}]},
        },
        "write": {"status": 200, "body": {"deleted": True}},
        "after": {"status": 200, "body": {"items": []}},
    }
    cleanup = {
        "accepted": True,
        "method": "POST",
        "path": "/api/cart/items",
        "audit_path": "sandbox_write_audit.jsonl",
        "audit_record": {"operation_phase": "experiment_cleanup"},
        "before": {"status": 200, "body": {"items": []}},
        "write": {
            "status": 201,
            "body": {"id": "item-new-999", "sku": "SKU-1", "qty": 2},
        },
        "after": {
            "status": 200,
            "body": {
                "items": [{"id": "item-new-999", "sku": "SKU-1", "qty": 2}],
            },
        },
    }
    assert _cleanup_restores_governed_write(original, cleanup) is True


def test_patch_prefers_restore_before_snapshot_over_delete_compensator() -> None:
    ir = {
        "operations": [
            {
                "id": "op-patch",
                "method": "PATCH",
                "path": "/api/resources/demo",
                "read_write": "write",
                "request_example": {"price": 9.9},
                "request_schema": {
                    "type": "object",
                    "properties": {"price": {"type": "number"}},
                },
                "source_refs": [{"source_id": "api", "locator": "PATCH resource"}],
            },
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/api/resources/demo",
                "read_write": "write",
                "source_refs": [{"source_id": "api", "locator": "DELETE resource"}],
            },
            {
                "id": "op-read",
                "method": "GET",
                "path": "/api/resources/demo",
                "read_write": "read",
                "source_refs": [{"source_id": "api", "locator": "GET resource"}],
            },
        ],
        "actors": [{
            "id": "actor-admin",
            "role": "admin",
            "credential_secret_ref": "secret_ref:admin",
            "account_status": "active",
        }],
        "relations": [{
            "id": "rel-compensate",
            "relation_type": "compensates",
            "from": "op-delete",
            "to": "op-patch",
            "from_ref": "op-delete",
            "to_ref": "op-patch",
            "operation_ref": "op-delete",
        }],
    }
    obligation = {
        "obligation_id": "obl-patch-restore",
        "risk_family": "validation",
        "property": {
            "template": "schema_constraint",
            "operation_ref": "op-patch",
            "actor_ref": "actor-admin",
        },
        "required_actors": ["actor-admin"],
        "required_operations": ["op-patch"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
        "source_refs": [{"source_id": "api", "locator": "PATCH resource"}],
    }
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment["compile_receipt"]
    cleanup = experiment["cleanup_plan"]
    # Dual control+treatment writes require reverse-order step-scoped cleanup.
    assert [row.get("source_step_id") for row in cleanup] == [
        "treatment_1",
        "control_1",
    ]
    for row in cleanup:
        assert row["action"] == "restore_before_snapshot"
        assert row["mode"] == "snapshot_restore"
        assert row["operation_ref"] == "op-patch"
        assert row["compensates_operation_ref"] == "op-patch"
        assert row["path"] == "/api/resources/demo"
        assert row["method"] == "PATCH"


def test_authz_dual_write_recreate_cleanup_binds_source_step_ids() -> None:
    """Multi-write control+treatment must scope recreate cleanup per write step.

    Observed residual after H11: reverse_order_compensation / recreate plans set
    operation_ref to the create compensator but omitted compensates_operation_ref,
    so expansion never attached source_step_id → missing_cleanup_for_steps.
    """
    ir = {
        "operations": [
            {
                "id": "op-delete-item",
                "method": "DELETE",
                "path": "/api/cart/items/demo",
                "read_write": "write",
                "source_refs": [{"source_id": "api", "locator": "DELETE cart item"}],
            },
            {
                "id": "op-create-item",
                "method": "POST",
                "path": "/api/cart/items",
                "read_write": "write",
                "request_example": {"sku": "SKU-1", "qty": 1},
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "integer"},
                    },
                },
                "source_refs": [{"source_id": "api", "locator": "POST cart item"}],
            },
            {
                "id": "op-read-item",
                "method": "GET",
                "path": "/api/cart/items/demo",
                "read_write": "read",
                "source_refs": [{"source_id": "api", "locator": "GET cart item"}],
            },
        ],
        "actors": [
            {
                "id": "actor-owner",
                "role": "buyer",
                "credential_secret_ref": "secret_ref:owner",
                "account_status": "active",
            },
            {
                "id": "actor-other",
                "role": "buyer",
                "credential_secret_ref": "secret_ref:other",
                "account_status": "active",
            },
        ],
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
    obligation = {
        "obligation_id": "obl-authz-delete-recreate",
        "risk_family": "authorization",
        "property": {
            "template": "permitted_operation_invocation",
            "operation_ref": "op-delete-item",
            "control_actor_ref": "actor-owner",
            "treatment_actor_ref": "actor-other",
            "require_same_resource": True,
        },
        "required_actors": ["actor-owner", "actor-other"],
        "required_operations": ["op-delete-item"],
        "required_fixtures": [],
        "required_observers": ["http_response", "actor_identity", "entity_state"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-create-item",
            "mode": "recreate_compensated_resource",
        },
        "source_refs": [{"source_id": "api", "locator": "DELETE cart item"}],
    }
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
        available_adapters={"http_api", "db_sql", "process_ledger"},
    )
    receipt = experiment.get("compile_receipt") or {}
    detail = str(receipt.get("detail") or "")
    # Multi-write coverage binds both write steps; recreate is admitted when the
    # create compensator carries a source request body (new identity allowed).
    assert "missing_cleanup_for_steps" not in detail, receipt
    assert receipt.get("status") == "COMPILED", receipt
    assert experiment.get("safety_contract", {}).get(
        "business_equivalence_allows_new_identity"
    ) is True
    cleanup = experiment.get("cleanup_plan") or []
    # permitted_operation_invocation emits treatment only (no control write).
    assert [row.get("source_step_id") for row in cleanup] == ["treatment_1"], cleanup
    for row in cleanup:
        assert row.get("compensates_operation_ref") == "op-delete-item"
        assert row.get("operation_ref") == "op-create-item"
        assert row.get("mode") == "recreate_compensated_resource"
