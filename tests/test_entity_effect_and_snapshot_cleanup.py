"""Entity-mediated effect observers and snapshot-restore cleanup scheduling."""
from __future__ import annotations

from ai_test_asset_center.obligation_compiler_base import (
    _cleanup_is_schedulable,
    _cleanup_requirement,
)
from ai_test_asset_center.runtime_binding_graph import declared_effect_observers


def test_cleanup_marks_put_as_snapshot_restore_when_no_compensator() -> None:
    op = {
        "id": "op-patch",
        "method": "PATCH",
        "path": "/api/resources/{id}",
        "read_write": "write",
    }
    req = _cleanup_requirement(op, [op], [])
    assert req["required"] is True
    assert req["mode"] == "snapshot_restore"
    assert not req.get("operation_ref")
    assert _cleanup_is_schedulable(req) is True


def test_cleanup_keeps_post_action_with_terminal_body_field_unschedulable() -> None:
    op = {
        "id": "op-status",
        "method": "POST",
        "path": "/api/users/{id}/status",
        "read_write": "write",
        "request_example": {"status": "DISABLED", "reason": "policy"},
    }
    req = _cleanup_requirement(op, [op], [])
    assert req == {"required": True, "mode": "reverse_order"}
    assert _cleanup_is_schedulable(req) is False


def test_cleanup_keeps_uncompensated_action_post_unschedulable() -> None:
    op = {
        "id": "op-pay",
        "method": "POST",
        "path": "/api/payments/pay",
        "read_write": "write",
        "request_example": {"orderId": "o1", "amount": 10},
    }
    req = _cleanup_requirement(op, [op], [])
    assert req["mode"] == "reverse_order"
    assert _cleanup_is_schedulable(req) is False


def test_cleanup_does_not_treat_permits_relation_as_no_entity_effect() -> None:
    op = {
        "id": "op-login",
        "method": "POST",
        "path": "/api/auth/login",
        "read_write": "write",
        "request_example": {"email": "a@b.c", "password": "x"},
    }
    relations = [
        {
            "relation_type": "permits",
            "from_ref": "actor-buyer",
            "to_ref": "op-login",
            "operation_ref": "op-login",
        }
    ]
    req = _cleanup_requirement(op, [op], relations)
    assert req == {"required": True, "mode": "reverse_order"}
    assert _cleanup_is_schedulable(req) is False


def test_cleanup_infers_only_identity_bound_delete_for_created_resource() -> None:
    create = {
        "id": "op-create",
        "method": "POST",
        "path": "/api/resources",
        "read_write": "write",
    }
    delete = {
        "id": "op-delete",
        "method": "DELETE",
        "path": "/api/resources/{resourceId}",
        "read_write": "write",
    }

    req = _cleanup_requirement(create, [create, delete], [])

    assert req == {
        "required": True,
        "mode": "reverse_order",
        "operation_ref": "op-delete",
    }
    assert _cleanup_is_schedulable(req) is True


def test_cleanup_rejects_collection_delete_as_create_compensator() -> None:
    create = {
        "id": "op-create",
        "method": "POST",
        "path": "/api/resources",
        "read_write": "write",
    }
    collection_delete = {
        "id": "op-archive",
        "method": "DELETE",
        "path": "/api/resources/archive",
        "read_write": "write",
    }

    req = _cleanup_requirement(create, [create, collection_delete], [])

    assert req == {"required": True, "mode": "reverse_order"}
    assert _cleanup_is_schedulable(req) is False


def test_cleanup_does_not_infer_recreation_for_delete_without_source_relation() -> None:
    delete = {
        "id": "op-delete",
        "method": "DELETE",
        "path": "/api/resources/{resourceId}",
        "read_write": "write",
    }
    create = {
        "id": "op-create",
        "method": "POST",
        "path": "/api/resources",
        "read_write": "write",
    }

    req = _cleanup_requirement(delete, [delete, create], [])

    assert req == {"required": True, "mode": "reverse_order"}
    assert _cleanup_is_schedulable(req) is False


def test_cleanup_keeps_entity_producing_write_required_without_compensator() -> None:
    op = {
        "id": "op-pay",
        "method": "POST",
        "path": "/api/payments/pay",
        "read_write": "write",
        "request_example": {"orderId": "o1", "amount": 10},
    }
    relations = [
        {
            "relation_type": "produces",
            "from_ref": "op-pay",
            "to_ref": "ent-payment",
            "operation_ref": "op-pay",
        },
        {
            "relation_type": "permits",
            "from_ref": "actor-buyer",
            "to_ref": "op-pay",
            "operation_ref": "op-pay",
        },
    ]
    req = _cleanup_requirement(op, [op], relations)
    assert req["required"] is True
    assert _cleanup_is_schedulable(req) is False


def test_identity_action_write_prefers_entity_get_over_collection() -> None:
    """POST /resources/{id}/confirm must observe GET /resources/{id}, not list."""
    from ai_test_asset_center.experiment_runtime_support import (
        _declared_observation_path,
    )

    ops_list = [
        {
            "id": "op-confirm",
            "method": "POST",
            "path": "/api/orders/{id}/confirm",
            "read_write": "write",
        },
        {
            "id": "op-get",
            "method": "GET",
            "path": "/api/orders/{id}",
            "read_write": "read",
        },
        {
            "id": "op-list",
            "method": "GET",
            "path": "/api/orders",
            "read_write": "read",
        },
    ]
    behavior_ir = {"operations": ops_list}
    resolvers = declared_effect_observers(
        ops_list[0],
        behavior_ir=behavior_ir,
        max_candidates=5,
    )
    assert resolvers[0]["path"] == "/api/orders/{id}"
    # Fail-closed observer authority: only the source-declared entity GET is
    # authoritative; the collection GET is no longer auto-included.
    assert [row["path"] for row in resolvers] == ["/api/orders/{id}"]

    ops = {row["id"]: row for row in ops_list}
    assert (
        _declared_observation_path(
            "/api/orders/{id}/confirm",
            ops,
            runtime_bindings={"id": "ord-1"},
        )
        == "/api/orders/ord-1"
    )
    # Without identity binding, the entity observer cannot materialize and the
    # collection GET is no longer an authoritative fallback (fail-closed).
    assert _declared_observation_path("/api/orders/{id}/confirm", ops) == ""


def test_entity_join_finds_payment_order_read() -> None:
    behavior_ir = {
        "entities": [{"id": "ent-pay", "name": "payment", "kind": "business_object"}],
        "operations": [
            {
                "id": "op-manual",
                "method": "POST",
                "path": "/api/payments/admin/manual-success",
                "read_write": "write",
            },
            {
                "id": "op-read",
                "method": "GET",
                "path": "/api/payments/order/{orderId}",
                "read_write": "read",
            },
        ],
        "relations": [
            {
                "relation_type": "produces",
                "from_ref": "op-manual",
                "to_ref": "ent-pay",
                "operation_ref": "op-manual",
                "source_refs": [{"source_id": "api-doc"}],
                "status": "accepted",
            },
            {
                "relation_type": "observes",
                "from_ref": "op-read",
                "to_ref": "ent-pay",
                "operation_ref": "op-read",
                "source_refs": [{"source_id": "api-doc"}],
                "status": "accepted",
            },
        ],
    }
    resolvers = declared_effect_observers(
        behavior_ir["operations"][0],
        behavior_ir=behavior_ir,
    )
    assert any(row["operation_ref"] == "op-read" for row in resolvers)


def test_redacted_password_binds_via_actor_credential_secret() -> None:
    from ai_test_asset_center.runtime_binding_graph import build_binding_plan

    op = {
        "id": "op-login",
        "method": "POST",
        "path": "/api/auth/login",
        "read_write": "write",
        "request_example": {
            "email": "buyer01@example.com",
            "password": "<REDACTED>",
        },
    }
    actor = {
        "id": "actor-buyer",
        "role": "buyer",
        "account_status": "active",
        "account_ref": "buyer01@example.com",
        "credential_secret_ref": "secret_ref:test_accounts:buyer01@example.com",
    }
    plan = build_binding_plan(
        operation=op,
        obligation={
            "obligation_id": "obl-login",
            "required_actors": ["actor-buyer"],
            "property": {"template": "permitted_operation_invocation"},
        },
        actors=[actor],
        behavior_ir={"operations": [op], "actors": [actor], "relations": []},
    )
    password_binding = next(row for row in plan if row.get("target") == "password")
    assert password_binding["status"] == "runtime_resolvable"
    assert password_binding["source_priority"] == "actor_credential_secret"
    assert password_binding["credential_secret_ref"].endswith("buyer01@example.com")
