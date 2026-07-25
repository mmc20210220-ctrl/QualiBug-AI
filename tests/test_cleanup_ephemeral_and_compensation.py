from __future__ import annotations

from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation


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
    assert experiment["safety_contract"]["cleanup_not_required"] is True


def test_collection_create_uses_unique_cancel_compensation() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-create-order",
            "risk_family": "validation",
            "property": {
                "operation_ref": "op-create-order",
                "actor_ref": "actor-buyer",
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
                "request_example": {},
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
            "relations": [],
        },
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED", experiment[
        "compile_receipt"
    ]
    assert experiment["cleanup_plan"][0]["operation_ref"] == "op-create-order"
    assert experiment["cleanup_plan"][0]["mode"] == "recreate_compensated_resource"


def test_identity_bound_status_uses_snapshot_restore_with_effect_read() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-status",
            "risk_family": "validation",
            "property": {
                "operation_ref": "op-status",
                "actor_ref": "actor-buyer",
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
    assert experiment["cleanup_plan"][0]["mode"] == "snapshot_restore"


def test_identity_bound_ship_uses_unique_sibling_cancel() -> None:
    experiment = compile_experiment_for_obligation(
        {
            "obligation_id": "obl-ship",
            "risk_family": "state",
            "property": {
                "operation_ref": "op-ship",
                "actor_ref": "actor-buyer",
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
    assert experiment["cleanup_plan"][0]["operation_ref"] == "op-cancel"
