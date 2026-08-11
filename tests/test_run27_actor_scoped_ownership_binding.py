from __future__ import annotations

import base64
import json


def _jwt(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"e30.{raw}.sig"


def _query_operation() -> dict:
    return {
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


def test_query_scope_is_sealed_per_step_and_removed_from_global_binding() -> None:
    from ai_test_asset_center.ownership_binding_scope_authority import (
        seal_ownership_binding_scopes,
    )

    exp = {
        "binding_plan": [
            {
                "target": "userId",
                "status": "runtime_resolvable",
                "source_priority": "ownership_identity_param",
            }
        ],
        "control_plan": [
            {
                "step_id": "control_1",
                "operation_ref": "list-orders",
                "actor_ref": "actor-a",
                "query": {"userId": "{userId}"},
            }
        ],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "operation_ref": "list-orders",
                "actor_ref": "actor-b",
                "query": {"userId": "{userId}"},
            }
        ],
        "fixture_dag": {
            "status": "READY",
            "nodes": [
                {
                    "node_id": "bind-user",
                    "kind": "runtime_read_binding",
                    "target": "userId",
                },
                {"node_id": "actor-a", "kind": "actor_context"},
            ],
            "setup_order": ["actor-a", "bind-user"],
            "edges": [{"from": "actor-a", "to": "bind-user"}],
        },
        "fixture_dependency_dag": {
            "nodes": [
                {
                    "node_id": "bind-user",
                    "kind": "runtime_read_binding",
                    "target": "userId",
                }
            ],
            "execution_order": ["bind-user"],
        },
    }
    sealed, receipt = seal_ownership_binding_scopes(
        exp,
        obligation={"risk_family": "visibility", "property": {}},
        behavior_ir={"operations": [_query_operation()]},
    )

    assert sealed["binding_plan"] == []
    assert sealed["control_plan"][0]["query"]["userId"] == (
        "actor_identity_ref:actor-a:userId"
    )
    assert sealed["treatment_plan"][0]["query"]["userId"] == (
        "actor_identity_ref:actor-b:userId"
    )
    assert receipt["removed_query_targets"] == ["userId"]
    assert sealed["fixture_dag"]["setup_order"] == ["actor-a"]
    assert sealed["fixture_dag"]["edges"] == []
    assert sealed["fixture_dependency_dag"]["execution_order"] == []


def test_runtime_resolves_control_and_treatment_query_to_different_actor_ids() -> None:
    from ai_test_asset_center.actor_scoped_query_binding import (
        project_actor_scoped_query_bindings,
    )

    control, treatment, receipt = project_actor_scoped_query_bindings(
        control_plan=[
            {
                "step_id": "control_1",
                "operation_ref": "list-orders",
                "actor_ref": "actor-a",
                "query": {"userId": "actor_identity_ref:actor-a:userId"},
            }
        ],
        treatment_plan=[
            {
                "step_id": "treatment_1",
                "operation_ref": "list-orders",
                "actor_ref": "actor-b",
                "query": {"userId": "actor_identity_ref:actor-b:userId"},
            }
        ],
        ops={"list-orders": _query_operation()},
        actors={
            "actor-a": {"id": "actor-a", "account_id": "U-A"},
            "actor-b": {"id": "actor-b", "account_id": "U-B"},
        },
        tokens={},
    )

    assert control[0]["query"]["userId"] == "U-A"
    assert treatment[0]["query"]["userId"] == "U-B"
    assert receipt["status"] == "PROJECTED"
    assert receipt["global_binding_fallback_allowed"] is False
    assert "U-A" not in repr(receipt)
    assert "U-B" not in repr(receipt)


def test_sealed_actor_ref_cannot_be_consumed_by_different_step_actor() -> None:
    from ai_test_asset_center.actor_scoped_query_binding import (
        UNRESOLVED_PLACEHOLDER,
        project_actor_scoped_query_bindings,
    )

    control, _treatment, receipt = project_actor_scoped_query_bindings(
        control_plan=[
            {
                "step_id": "control_1",
                "operation_ref": "list-orders",
                "actor_ref": "actor-b",
                "query": {"userId": "actor_identity_ref:actor-a:userId"},
            }
        ],
        treatment_plan=[],
        ops={"list-orders": _query_operation()},
        actors={
            "actor-a": {"id": "actor-a", "account_id": "U-A"},
            "actor-b": {"id": "actor-b", "account_id": "U-B"},
        },
        tokens={},
    )

    assert control[0]["query"]["userId"] == UNRESOLVED_PLACEHOLDER
    assert receipt["status"] == "BLOCKED"
    assert receipt["rows"][0]["target_receipts"][0]["reason_code"] == (
        "ACTOR_IDENTITY_REF_STEP_ACTOR_MISMATCH"
    )


def test_typed_jwt_identity_is_used_and_conflicting_typed_claims_block() -> None:
    from ai_test_asset_center.actor_scoped_query_binding import (
        resolve_actor_runtime_identity,
    )

    resolved = resolve_actor_runtime_identity(
        "actor-a",
        actors={"actor-a": {"id": "actor-a", "token": _jwt({"user_id": "U-A"})}},
        tokens={"actor-a": _jwt({"user_id": "U-A"})},
    )
    assert resolved["status"] == "RESOLVED"
    assert resolved["identity_value"] == "U-A"
    assert resolved["authority"] == "jwt_typed_identity_claim"

    conflict_token = _jwt({"user_id": "U-A", "account_id": "A-OTHER"})
    conflict = resolve_actor_runtime_identity(
        "actor-a",
        actors={"actor-a": {"id": "actor-a"}},
        tokens={"actor-a": conflict_token},
    )
    assert conflict["status"] == "UNRESOLVED"
    assert conflict["reason_code"] == "ACTOR_IDENTITY_JWT_TYPED_CLAIM_CONFLICT"
