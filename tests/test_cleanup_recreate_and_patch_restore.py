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
    assert experiment["cleanup_plan"] == [{
        "action": "restore_before_snapshot",
        "mode": "snapshot_restore",
        "operation_ref": "op-patch",
        "path": "/api/resources/demo",
        "method": "PATCH",
        "runtime_response_binding_required": False,
    }]
