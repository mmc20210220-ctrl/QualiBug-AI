from __future__ import annotations

from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.sandbox_write_executor_base import (
    _protected_runtime_identity_write_block_reason,
)


def _actor() -> dict:
    return {
        "id": "actor-buyer",
        "role": "buyer",
        "credential_secret_ref": "secret_ref:test_accounts:buyer",
    }


def test_ephemeral_login_compiles_even_when_cleanup_marked_required() -> None:
    """Matrix writes force cleanup=required; session posts must still waive it."""
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-login",
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
            "required_observers": ["http_response"],
            "cleanup_requirement": {"required": True},
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
            "actors": [_actor()],
            "relations": [],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    assert experiment["cleanup_plan"] == []
    assert experiment["safety_contract"]["governed_write"] is False
    assert experiment["safety_contract"]["cleanup_not_required"] is True
    assert experiment["safety_contract"]["business_effect_requirement"] == (
        "NOT_APPLICABLE"
    )
    validation_assertion = experiment["assertions"][0]
    assert validation_assertion["business_effect_requirement"] == "NOT_APPLICABLE"
    assert "expected_control_effect_min" not in validation_assertion


def test_session_exchange_is_not_mistaken_for_protected_identity_mutation(
    tmp_path,
) -> None:
    assert _protected_runtime_identity_write_block_reason(
        root=tmp_path,
        project="project",
        scenario=None,
        method="POST",
        path="/api/auth/login",
        body={"email": "declared@example.test", "password": "<REDACTED>"},
    ) == ""


def test_password_reset_remains_protected_identity_mutation(tmp_path) -> None:
    assert _protected_runtime_identity_write_block_reason(
        root=tmp_path,
        project="project",
        scenario=None,
        method="POST",
        path="/api/auth/password/reset",
        body={"email": "declared@example.test", "newPassword": "<REDACTED>"},
    ) == "identity_mutation_requires_disposable_fixture"


def test_collection_create_uses_unique_cancel_compensation() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-create-order",
            "risk_family": "validation",
            "property": {
                "operation_ref": "op-create-order",
                "actor_ref": "actor-buyer",
                "field": "addressId",
                "validation_constraint": "required",
                "validation_constraint_source": "request_schema",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-create-order"],
            "required_observers": ["http_response"],
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [{
                "id": "op-create-order",
                "method": "POST",
                "path": "/api/orders",
                "read_write": "write",
                "request_example": {"addressId": "addr-1"},
                "request_schema": {
                    "type": "object",
                    "required": ["addressId"],
                    "properties": {
                        "addressId": {"type": "string"},
                    },
                },
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-list-orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-get-order",
                "method": "GET",
                "path": "/api/orders/{id}",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-cancel",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
                "read_write": "write",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
            "actors": [_actor()],
            "relations": [{
                "id": "rel-create-cancel",
                "kind": "compensates",
                "source": "op-create-order",
                "target": "op-cancel",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    assert experiment["cleanup_plan"]
    assert experiment["cleanup_plan"][0]["operation_ref"] == "op-cancel"


def test_cancel_recreates_via_unique_collection_create() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-cancel",
            "risk_family": "validation",
            "property": {
                "operation_ref": "op-cancel",
                "actor_ref": "actor-buyer",
                "field": "reason",
                "validation_constraint": "type:string",
                "validation_constraint_source": "request_schema",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-cancel"],
            "required_observers": ["http_response"],
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [{
                "id": "op-cancel",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
                "read_write": "write",
                "request_example": {"reason": "user_cancel"},
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                    },
                },
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-create-order",
                "method": "POST",
                "path": "/api/orders",
                "read_write": "write",
                "request_example": {"addressId": "addr-1"},
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-list-orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-get-order",
                "method": "GET",
                "path": "/api/orders/{id}",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
            "actors": [_actor()],
            "relations": [{
                "id": "rel-cancel-recreate",
                "kind": "compensates",
                "source": "op-cancel",
                "target": "op-create-order",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
        },
        environment_type="test",
    )

    # SPEC v1.1 §12.2: explicit compensator relation produces a valid plan.
    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    assert experiment["cleanup_plan"][0]["operation_ref"] == "op-create-order"
    assert experiment["cleanup_plan"][0]["mode"] == "compensating_transition"


def test_identity_bound_status_uses_snapshot_restore_with_effect_read() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-status",
            "risk_family": "validation",
            "property": {
                "operation_ref": "op-status",
                "actor_ref": "actor-buyer",
                "field": "status",
                "validation_constraint": "enum",
                "validation_constraint_value": ["enabled", "disabled"],
                "validation_constraint_source": "request_schema",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-status"],
            "required_observers": ["http_response"],
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [{
                "id": "op-status",
                "method": "POST",
                "path": "/api/auth/admin/users/{id}/status",
                "read_write": "write",
                "request_example": {"status": "disabled"},
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["enabled", "disabled"]},
                    },
                },
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-list-users",
                "method": "GET",
                "path": "/api/auth/admin/users",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-get-user",
                "method": "GET",
                "path": "/api/auth/admin/users/{id}",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
            "actors": [_actor()],
            "relations": [],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    assert experiment["cleanup_plan"][0]["mode"] == "restore_snapshot"


def test_identity_bound_ship_degrades_to_residue_when_not_restorable() -> None:
    """Sibling cancel does not reverse ship; empty-body snapshot is also fake.

    On a declared non-production target there is still no real compensator, so
    per the degradation ladder the write is allowed and the leftover residue is
    accepted (marked for later environment reset) rather than blocking the
    experiment. Production stays fail-closed (see the production guard test)."""
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-ship",
            "risk_family": "state",
            "property": {
                "operation_ref": "op-ship",
                "actor_ref": "actor-buyer",
                "from_state_ref": "state-created",
                "to_state_ref": "state-shipped",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-ship"],
            "required_observers": ["http_response", "before_state", "after_state"],
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [{
                "id": "op-ship",
                "method": "POST",
                "path": "/api/orders/{id}/ship",
                "read_write": "write",
                "request_example": {},
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-list-orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-get-order",
                "method": "GET",
                "path": "/api/orders/{id}",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-cancel",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
                "read_write": "write",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
            "actors": [_actor()],
            "relations": [],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    assert experiment["cleanup_plan"][0]["action"] == "accepted_residue"
    assert experiment["cleanup_plan"][0]["mode"] == "accepted_residue_no_cleanup"


def test_identity_bound_ship_still_blocked_on_production() -> None:
    """Production is a hard write boundary; the accepted-residue degradation
    must never apply there."""
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-ship",
            "risk_family": "state",
            "property": {
                "operation_ref": "op-ship",
                "actor_ref": "actor-buyer",
                "from_state_ref": "state-created",
                "to_state_ref": "state-shipped",
            },
            "required_actors": ["actor-buyer"],
            "required_operations": ["op-ship"],
            "required_observers": ["http_response", "before_state", "after_state"],
            "cleanup_requirement": {"required": True},
        },
        behavior_ir={
            "operations": [{
                "id": "op-ship",
                "method": "POST",
                "path": "/api/orders/{id}/ship",
                "read_write": "write",
                "request_example": {},
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-list-orders",
                "method": "GET",
                "path": "/api/orders",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }, {
                "id": "op-get-order",
                "method": "GET",
                "path": "/api/orders/{id}",
                "read_write": "read",
                "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
            }],
            "actors": [_actor()],
            "relations": [],
        },
        environment_type="production",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED", experiment[
        "compile_receipt"
    ]
