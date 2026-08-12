from __future__ import annotations

from ai_test_asset_center.experiment_runtime_materialization import _resolve_cleanup_authority


def _obligation(operation_ref: str) -> dict:
    return {
        "obligation_id": "obl-cleanup",
        "property": {"operation_ref": operation_ref},
        "required_operations": [operation_ref],
        "cleanup_requirement": {"required": True},
    }


def test_unrelated_global_delete_is_never_used_as_cleanup_compensator() -> None:
    write = {
        "id": "payment-replay",
        "operation_id": "payment-replay",
        "method": "POST",
        "path": "/api/payments/admin/replay-success",
    }
    unrelated = {
        "id": "delete-address",
        "operation_id": "delete-address",
        "method": "DELETE",
        "path": "/api/users/addresses/{id}",
    }

    plan, unresolved = _resolve_cleanup_authority(
        obligation=_obligation("payment-replay"),
        ops={"payment-replay": write, "delete-address": unrelated},
        available_adapters={"http_api"},
        reason="",
    )

    assert plan["authority_resolved"] is False
    assert plan["reason_code"] == "CLEANUP_OPERATION_MISSING"
    assert plan.get("cleanup_operation_refs") == []
    assert "delete-address" not in str(plan)
    assert unresolved[0]["reason"] == "CLEANUP_OPERATION_MISSING"


def test_unique_same_collection_identity_delete_remains_valid_cleanup() -> None:
    create = {
        "id": "create-resource",
        "operation_id": "create-resource",
        "method": "POST",
        "path": "/api/resources",
    }
    delete = {
        "id": "delete-resource",
        "operation_id": "delete-resource",
        "method": "DELETE",
        "path": "/api/resources/{id}",
    }

    plan, unresolved = _resolve_cleanup_authority(
        obligation=_obligation("create-resource"),
        ops={"create-resource": create, "delete-resource": delete},
        available_adapters={"http_api"},
        reason="",
    )

    assert unresolved == []
    assert plan["authority_resolved"] is True
    assert plan["tier"] == "source_declared_api_compensator"
    assert plan["plan"]["operation_ref"] == "delete-resource"
    assert plan["plan"]["path"] == "/api/resources/{id}"
    assert plan["cleanup_operation_refs"] == ["delete-resource"]


def test_ambiguous_same_collection_deletes_stay_fail_closed() -> None:
    create = {"id": "create-resource", "method": "POST", "path": "/api/resources"}
    delete_a = {"id": "delete-a", "method": "DELETE", "path": "/api/resources/{id}"}
    delete_b = {"id": "delete-b", "method": "DELETE", "path": "/api/resources/{uuid}"}

    plan, unresolved = _resolve_cleanup_authority(
        obligation=_obligation("create-resource"),
        ops={"create-resource": create, "delete-a": delete_a, "delete-b": delete_b},
        available_adapters={"http_api"},
        reason="",
    )

    assert plan["authority_resolved"] is False
    assert plan["reason_code"] == "CLEANUP_DELETE_ROUTE_AMBIGUOUS"
    assert set(plan["cleanup_operation_refs"]) == {"delete-a", "delete-b"}
    assert unresolved[0]["reason"] == "CLEANUP_DELETE_ROUTE_AMBIGUOUS"
