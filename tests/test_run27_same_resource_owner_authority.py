from __future__ import annotations


def _body_operation() -> dict:
    return {
        "id": "transfer",
        "method": "POST",
        "path": "/api/transfers",
        "request_schema": {
            "type": "object",
            "properties": {
                "ownerId": {"type": "string"},
                "amount": {"type": "number"},
            },
        },
        "request_example": {"ownerId": "{ownerId}", "amount": 10},
    }


def _experiment() -> dict:
    return {
        "binding_plan": [
            {
                "target": "ownerId",
                "status": "runtime_resolvable",
                "source_priority": "ownership_identity_param",
                "body_template_paths": ["ownerId"],
            }
        ],
        "control_plan": [
            {
                "step_id": "control_1",
                "operation_ref": "transfer",
                "actor_ref": "owner-actor",
                "body": {"ownerId": "{ownerId}", "amount": 10},
            }
        ],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "operation_ref": "transfer",
                "actor_ref": "other-actor",
                "body": {"ownerId": "{ownerId}", "amount": 10},
            }
        ],
    }


def test_authorization_same_resource_binding_seals_unique_control_owner() -> None:
    from ai_test_asset_center.ownership_binding_scope_authority import (
        seal_ownership_binding_scopes,
    )

    sealed, receipt = seal_ownership_binding_scopes(
        _experiment(),
        obligation={
            "risk_family": "authorization",
            "property": {"require_same_resource": True},
        },
        behavior_ir={"operations": [_body_operation()]},
    )

    binding = sealed["binding_plan"][0]
    assert binding["ownership_binding_scope"] == "shared_control_resource_owner"
    assert binding["owner_actor_ref"] == "owner-actor"
    assert binding["ownership_actor_authority"] == "compiled_control_step_actor"
    assert binding["source_order_selection_allowed"] is False
    assert receipt["status"] == "SEALED"


def test_runtime_materializer_accepts_only_core_alignment_with_sealed_owner() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _ownership_runtime_preflight,
    )
    from ai_test_asset_center.ownership_binding_scope_authority import (
        seal_ownership_binding_scopes,
    )

    sealed, _receipt = seal_ownership_binding_scopes(
        _experiment(),
        obligation={
            "risk_family": "authorization",
            "property": {"require_same_resource": True},
        },
        behavior_ir={"operations": [_body_operation()]},
    )
    governed_actors, runtime_receipt = _ownership_runtime_preflight(
        exp=sealed,
        actors={
            "owner-actor": {"id": "owner-actor", "user_id": "U-OWNER"},
            "other-actor": {"id": "other-actor", "account_id": "U-OTHER"},
        },
        tokens={},
    )

    assert runtime_receipt["status"] == "READY"
    assert runtime_receipt["rows"][0]["owner_actor_ref"] == "owner-actor"
    assert runtime_receipt["rows"][0]["core_consumption_aligned"] is True
    assert governed_actors["owner-actor"]["account_id"] == "U-OWNER"
    assert "U-OWNER" not in repr(runtime_receipt)


def test_unrelated_first_account_actor_never_becomes_owner() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _ownership_runtime_preflight,
    )
    from ai_test_asset_center.ownership_binding_scope_authority import (
        seal_ownership_binding_scopes,
    )

    exp = _experiment()
    exp["control_plan"] = [
        {
            "step_id": "setup_read",
            "operation_ref": "other-op",
            "actor_ref": "unrelated-actor",
            "body": {},
        },
        *exp["control_plan"],
    ]
    sealed, _receipt = seal_ownership_binding_scopes(
        exp,
        obligation={
            "risk_family": "authorization",
            "property": {"require_same_resource": True},
        },
        behavior_ir={
            "operations": [
                _body_operation(),
                {"id": "other-op", "method": "GET", "path": "/api/other"},
            ]
        },
    )

    _actors, runtime_receipt = _ownership_runtime_preflight(
        exp=sealed,
        actors={
            "unrelated-actor": {
                "id": "unrelated-actor",
                "account_id": "U-UNRELATED",
            },
            "owner-actor": {"id": "owner-actor", "account_id": "U-OWNER"},
            "other-actor": {"id": "other-actor", "account_id": "U-OTHER"},
        },
        tokens={},
    )

    # The core materializer consumes the compile-sealed owner_actor_ref
    # directly — actor/plan/dict order never decides the owner. The unrelated
    # actor appearing first in the plan neither becomes the owner nor blocks
    # the legitimate ownership experiment.
    assert runtime_receipt["status"] == "READY"
    row = runtime_receipt["rows"][0]
    assert row["owner_actor_ref"] == "owner-actor"
    assert row["core_consumption_aligned"] is True
    assert row["reason_code"] == ""


def test_legacy_unsealed_ownership_binding_is_blocked_before_fixture_transport() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _ownership_runtime_preflight,
    )

    _actors, receipt = _ownership_runtime_preflight(
        exp=_experiment(),
        actors={
            "owner-actor": {"id": "owner-actor", "account_id": "U-OWNER"},
            "other-actor": {"id": "other-actor", "account_id": "U-OTHER"},
        },
        tokens={},
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["issues"][0]["reason_code"] == "OWNERSHIP_RUNTIME_SCOPE_UNSEALED"
