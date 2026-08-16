"""Multi-level dependency chain planning (task 25).

Real enterprise systems are multi-interface topologies: a write's body
carries reference fields whose referenced entities are created by other
writes carrying further references (user -> address -> order -> payment).
These tests pin the planner's behavior:

* the full dependency DAG is planned leaves-first (each created identity is
  captured before the consuming create materializes);
* every level carries observe-first resolvers (real environment rows win over
  creation) and a skip-if-observed marker;
* dependency cycles fail closed with a NAMED reason and never recurse;
* depth is bounded by an explicit receipted cap;
* unestablishable nested dependencies degrade visibly (never invented), while
  unestablishable top-level subjects block;
* diamonds (one entity consumed through several parent reference fields)
  produce one create step binding every parent field;
* everything is structural / IR-driven — no industry vocabulary.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_test_asset_center.multi_level_dependency_chain import (
    BLOCKED,
    MAX_DEPENDENCY_DEPTH,
    NOT_APPLICABLE,
    PLANNED,
    REASON_CYCLE,
    REASON_NO_CLEANUP,
    REASON_NO_CREATE,
    REASON_TOO_DEEP,
    plan_multi_level_dependency_chain,
)
from ai_test_asset_center.money_precondition_chain import (
    plan_money_family_precondition,
)


def _entity(
    entity_id: str,
    name: str,
    *,
    collection: str = "",
) -> dict[str, Any]:
    entity: dict[str, Any] = {
        "id": entity_id,
        "name": name,
        "source_entity_names": [name + "s"],
        "identity_fields": ["id"],
    }
    if collection:
        entity["collection_path"] = collection
    return entity


def _post(op_id: str, path: str, example: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": op_id,
        "method": "POST",
        "path": path,
        "read_write": "write",
        "request_example": example,
        "source_refs": [{"kind": "api_operation", "locator": f"POST {path}"}],
    }


def _delete(op_id: str, path: str) -> dict[str, Any]:
    return {
        "id": op_id,
        "method": "DELETE",
        "path": path,
        "read_write": "write",
        "source_refs": [{"kind": "api_operation", "locator": f"DELETE {path}"}],
    }


def _get(op_id: str, path: str) -> dict[str, Any]:
    return {
        "id": op_id,
        "method": "GET",
        "path": path,
        "read_write": "read",
        "source_refs": [{"kind": "api_operation", "locator": f"GET {path}"}],
    }


def _body_ref(op_id: str, field: str, entity_ref: str) -> dict[str, Any]:
    """Explicit source-declared body-reference relation.

    A request field name (``userId``) is not entity authority — the source
    must declare the FK target. These rows match the shape the
    ``database_body_reference_projection`` emits from an operator-approved
    API/DB mapping or an exact database foreign key.
    """
    return {
        "operation_ref": op_id,
        "body_path": field,
        "target_entity_ref": entity_ref,
        "status": "RESOLVED",
        "source_refs": [
            {
                "kind": "database_foreign_key",
                "locator": f"{op_id}.{field} -> {entity_ref}.id",
            }
        ],
    }


def _three_level_ir() -> dict[str, Any]:
    """user -> address -> order: order create needs addressId; address create
    needs userId; user create has no references."""
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "entities": [
            _entity("ent_user", "user"),
            # "address" pluralizes irregularly (addresses), so the entity
            # declares its collection path explicitly — structural +s
            # derivation cannot invent the correct surface.
            _entity("ent_address", "address", collection="/api/addresses"),
            _entity("ent_order", "order"),
        ],
        "operations": [
            _post("op_create_user", "/api/users", {"email": "x@y.z", "name": "u"}),
            _post(
                "op_create_address",
                "/api/addresses",
                {"userId": "<user_id>", "city": "C", "street": "S"},
            ),
            _post(
                "op_create_order",
                "/api/orders",
                {"addressId": "<address_id>", "items": [{"sku": "A", "qty": 1}]},
            ),
            _post(
                "op_pay",
                "/api/payments/pay",
                {"orderId": "<order_id>", "amount": 1, "channel": "BALANCE"},
            ),
            _get("op_list_users", "/api/users"),
            _get("op_list_addresses", "/api/addresses"),
            _get("op_list_orders", "/api/orders"),
            _delete("op_del_user", "/api/users/{id}"),
            _delete("op_del_address", "/api/addresses/{id}"),
            _delete("op_del_order", "/api/orders/{id}"),
        ],
        "actors": [
            {
                "id": "actor_buyer",
                "name": "buyer01",
                "role": "buyer",
                "runtime_bound": True,
                "credential_secret_ref": "secret_ref:actor:buyer01",
            },
        ],
        "states": [],
        "relations": [
            {
                "relation_type": "permits",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_create_user",
                "status": "accepted",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
            {
                "relation_type": "permits",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_create_address",
                "status": "accepted",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
            {
                "relation_type": "permits",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_create_order",
                "status": "accepted",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
            {
                "relation_type": "permits",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_pay",
                "status": "accepted",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            },
        ],
        # Explicit source-backed body-reference relations. A field name like
        # ``userId`` is not entity authority (no structural name inference);
        # the source must declare the FK target for each reference field the
        # dependency chain traverses.
        "body_reference_relations": [
            _body_ref("op_create_address", "userId", "ent_user"),
            _body_ref("op_create_order", "addressId", "ent_address"),
            _body_ref("op_pay", "orderId", "ent_order"),
        ],
        "invariants": [],
    }


def _ir() -> dict[str, Any]:
    return _three_level_ir()


def _pay_operation(ir: dict[str, Any]) -> dict[str, Any]:
    return next(op for op in ir["operations"] if op["id"] == "op_pay")


# ── multi-level DAG planning ────────────────────────────────────────────────


def test_full_chain_is_planned_leaves_first() -> None:
    ir = _ir()
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    steps = result["steps"]
    # user -> address -> order: user created first, order (subject) last.
    assert [step["creates_entity_ref"] for step in steps] == [
        "ent_user",
        "ent_address",
        "ent_order",
    ]
    assert steps[0]["identity_binding_target"] == "userId"
    assert steps[1]["identity_binding_target"] == "addressId"
    assert steps[2]["identity_binding_target"] == "orderId"
    assert steps[0]["step_ordinal"] < steps[1]["step_ordinal"] < steps[2]["step_ordinal"]
    assert result["identity_binding_target"] == "orderId"
    assert result["create_operation_ref"] == "op_create_order"
    assert result["entity_ref"] == "ent_order"
    assert result["detail"]["entity_count"] == 3


def test_every_step_carries_observe_first_resolvers() -> None:
    ir = _ir()
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    for step in result["steps"]:
        assert step["skip_if_observed_target"]
        resolvers = step["observation_resolvers"]
        assert resolvers, f"no observation resolvers on {step['step_id']}"
        for resolver in resolvers:
            assert resolver["method"] in {"GET", "HEAD"}
            assert resolver["path"].startswith("/")
    # The subject's resolvers are the collection reads of the subject entity.
    subject_paths = {
        resolver["path"] for resolver in result["observation_resolvers"]
    }
    assert "/api/orders" in subject_paths


def test_chain_resolves_consuming_body_placeholders() -> None:
    """Each created identity feeds the NEXT step's body placeholder."""
    ir = _ir()
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    steps = result["steps"]
    # A step's identity_binding_target is the reference field under which the
    # CONSUMING create materializes the captured identity: user -> userId
    # (address create body), address -> addressId (order create body), order
    # -> orderId (pay body). Leaves-first execution binds each before use.
    assert steps[0]["identity_binding_target"] == "userId"
    assert steps[1]["identity_binding_target"] == "addressId"
    assert steps[2]["identity_binding_target"] == "orderId"


def test_subject_operation_planner_uses_same_chain() -> None:
    """The money chain compiles the same multi-level DAG for a pay write."""
    ir = _ir()
    result = plan_money_family_precondition(
        behavior_ir=ir,
        operation=_pay_operation(ir),
        actor_refs=["actor_buyer"],
        property_spec={"template": "idempotent_effect_cardinality"},
        family="idempotency",
    )
    assert result["status"] == PLANNED
    steps = result["steps"]
    assert [step["creates_entity_ref"] for step in steps] == [
        "ent_user",
        "ent_address",
        "ent_order",
    ]
    # Subject step keeps the historical identity; nested steps stay distinct.
    assert steps[-1]["step_id"] == "money_precondition_create"
    assert steps[-1]["intent"] == "money_subject_establishment"
    assert steps[0]["intent"] == "multi_level_dependency_establishment"
    assert steps[0]["identity_binding_target"] == "userId"
    assert steps[1]["identity_binding_target"] == "addressId"
    assert result["identity_binding_target"] == "orderId"


# ── cycle detection ─────────────────────────────────────────────────────────


def test_dependency_cycle_blocks_with_named_reason() -> None:
    ir = _ir()
    # order -> address -> user -> order (user create now references orderId).
    user_op = next(op for op in ir["operations"] if op["id"] == "op_create_user")
    user_op["request_example"] = {"orderId": "<order_id>"}
    # The user create now references the order entity, closing the cycle.
    ir["body_reference_relations"].append(
        _body_ref("op_create_user", "orderId", "ent_order")
    )
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == BLOCKED
    assert result["reason_code"] == REASON_CYCLE
    chain = result["detail"]["chain"]
    assert "ent_order" in chain and "ent_user" in chain and "ent_address" in chain


def test_self_reference_is_not_a_cycle() -> None:
    """A create example echoing its own id (parentId of the same entity) is a
    data-passing convention, never a creation dependency."""
    ir = _ir()
    order_op = next(op for op in ir["operations"] if op["id"] == "op_create_order")
    order_op["request_example"] = {
        "orderId": "<order_id>",
        "addressId": "<address_id>",
    }
    # orderId is the create's own identity echoed back: declare the explicit
    # FK target so the planner resolves it as a self-reference and skips it.
    ir["body_reference_relations"].append(
        _body_ref("op_create_order", "orderId", "ent_order")
    )
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    assert result["detail"]["entity_count"] == 3


# ── depth budget ────────────────────────────────────────────────────────────


def test_too_deep_chain_blocks_with_named_reason() -> None:
    """A chain longer than the explicit budget fails closed instead of
    recursing without bound."""
    ir = _ir()
    # Build a linear chain of MAX_DEPENDENCY_DEPTH + 2 entities: each create
    # references the next entity's id.
    chain_entities = [
        _entity(f"ent_e{i}", f"entity_{i}") for i in range(MAX_DEPENDENCY_DEPTH + 2)
    ]
    ops: list[dict[str, Any]] = []
    body_refs: list[dict[str, Any]] = []
    for i, entity in enumerate(chain_entities):
        example: dict[str, Any] = {"name": f"n{i}"}
        if i + 1 < len(chain_entities):
            next_name = chain_entities[i + 1]["name"]
            example[f"{next_name}Id"] = f"<{next_name}_id>"
            body_refs.append(
                _body_ref(
                    f"op_create_{entity['name']}",
                    f"{next_name}Id",
                    chain_entities[i + 1]["id"],
                )
            )
        ops.append(_post(f"op_create_{entity['name']}", f"/api/{entity['name']}s", example))
        ops.append(_get(f"op_list_{entity['name']}", f"/api/{entity['name']}s"))
        ops.append(_delete(f"op_del_{entity['name']}", f"/api/{entity['name']}s/{{id}}"))
    deep_ir = {
        "schema_version": "qualibug.behavior-ir.v2",
        "entities": chain_entities,
        "operations": ops,
        "actors": [
            {
                "id": "actor_a",
                "name": "a",
                "role": "buyer",
                "runtime_bound": True,
                "credential_secret_ref": "secret_ref:actor:a",
            },
        ],
        "states": [],
        "relations": [
            {
                "relation_type": "permits",
                "actor_ref": "actor_a",
                "operation_ref": op["id"],
                "status": "accepted",
                "source_refs": [{"kind": "document", "locator": "prd"}],
            }
            for op in ops
            if op["method"] == "POST"
        ],
        "body_reference_relations": body_refs,
        "invariants": [],
    }
    result = plan_multi_level_dependency_chain(
        behavior_ir=deep_ir,
        entity_id=chain_entities[0]["id"],
        reference_field=f"{chain_entities[0]['name']}Id",
        actor_refs=["actor_a"],
    )
    assert result["status"] == BLOCKED
    assert result["reason_code"] == REASON_TOO_DEEP


# ── fail-closed ─────────────────────────────────────────────────────────────


def test_unestablishable_nested_dependency_degrades_visibly() -> None:
    """A nested entity without a create op leaves the parent's placeholder and
    is REPORTED — never invented, never silently dropped."""
    ir = _ir()
    ir["operations"] = [
        op
        for op in ir["operations"]
        if op["id"] != "op_create_address"
    ]
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    unresolved = result["detail"]["unresolved_nested_references"]
    assert any(row.get("entity_ref") == "ent_address" for row in unresolved)
    # The order create still plans (its addressId placeholder stays for the
    # binding gate) — but the address is never fabricated.
    assert result["steps"][-1]["creates_entity_ref"] == "ent_order"


def test_unestablishable_subject_blocks() -> None:
    ir = _ir()
    ir["operations"] = [
        op
        for op in ir["operations"]
        if op["id"] != "op_create_order"
    ]
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == BLOCKED
    assert result["reason_code"] == REASON_NO_CREATE


def test_missing_cleanup_blocks_with_named_reason() -> None:
    ir = _ir()
    ir["operations"] = [
        op
        for op in ir["operations"]
        if op["id"] != "op_del_order"
    ]
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == BLOCKED
    assert result["reason_code"] == REASON_NO_CLEANUP


def test_unknown_subject_entity_blocks() -> None:
    ir = _ir()
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_missing",
        reference_field="missingId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == BLOCKED
    assert "ENTITY_UNRESOLVED" in result["reason_code"]


# ── diamond dependencies ────────────────────────────────────────────────────


def test_diamond_binds_every_parent_reference_field() -> None:
    """Two parents referencing the same entity create it ONCE and bind the
    captured identity under BOTH parent field names."""
    ir = _ir()
    order_op = next(op for op in ir["operations"] if op["id"] == "op_create_order")
    order_op["request_example"] = {
        "addressId": "<address_id>",
        "billingAddressId": "<address_id>",
    }
    # Both parent fields are explicit FK targets of the same entity.
    ir["body_reference_relations"].append(
        _body_ref("op_create_order", "billingAddressId", "ent_address")
    )
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    address_steps = [
        step
        for step in result["steps"]
        if step["creates_entity_ref"] == "ent_address"
    ]
    assert len(address_steps) == 1
    assert sorted(address_steps[0]["identity_binding_targets"]) == [
        "addressId",
        "billingAddressId",
    ]


# ── cross-industry generality ───────────────────────────────────────────────


def test_warehouse_terms_plan_identically() -> None:
    """A warehouse topology (location -> shipment -> dispatch) plans through
    the same structural mechanism as the e-commerce one."""
    ir = _ir()
    ir["entities"] = [
        _entity("ent_location", "location"),
        _entity("ent_shipment", "shipment"),
        _entity("ent_dispatch", "dispatch"),
    ]
    ir["operations"] = [
        _post("op_create_location", "/api/locations", {"code": "WH-01"}),
        _post(
            "op_create_shipment",
            "/api/shipments",
            {"locationId": "<location_id>", "items": [{"sku": "A", "qty": 1}]},
        ),
        _post(
            "op_dispatch",
            "/api/dispatch",
            {"shipmentId": "<shipment_id>", "vehicle": "T1"},
        ),
        _get("op_list_locations", "/api/locations"),
        _get("op_list_shipments", "/api/shipments"),
        _get("op_list_dispatch", "/api/dispatch"),
        _delete("op_del_location", "/api/locations/{id}"),
        _delete("op_del_shipment", "/api/shipments/{id}"),
        _delete("op_del_dispatch", "/api/dispatch/{id}"),
    ]
    ir["relations"] = [
        {
            "relation_type": "permits",
            "actor_ref": "actor_buyer",
            "operation_ref": op["id"],
            "status": "accepted",
            "source_refs": [{"kind": "document", "locator": "prd"}],
        }
        for op in ir["operations"]
        if op["method"] == "POST"
    ]
    ir["body_reference_relations"] = [
        _body_ref("op_create_shipment", "locationId", "ent_location"),
        _body_ref("op_dispatch", "shipmentId", "ent_shipment"),
    ]
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_dispatch",
        reference_field="shipmentId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    assert [step["creates_entity_ref"] for step in result["steps"]] == [
        "ent_location",
        "ent_shipment",
        "ent_dispatch",
    ]
    assert result["steps"][0]["identity_binding_target"] == "locationId"
    assert result["steps"][1]["identity_binding_target"] == "shipmentId"
    assert result["steps"][2]["identity_binding_target"] == "shipmentId"


# ── real-corpus shapes ──────────────────────────────────────────────────────


def test_plural_entity_names_and_nested_collection_resolve() -> None:
    """Real-corpus shapes: entities named in plural (``addresses``), creates
    on nested collections (``/api/users/addresses``), and ``es``-singular
    reference resolution (``addressId`` -> ``address`` -> ``addresses``)."""
    ir = _ir()
    ir["entities"] = [
        _entity("ent_users", "users"),
        _entity("ent_addresses", "addresses"),
        _entity("ent_orders", "orders"),
    ]
    ir["operations"] = [
        _post("op_create_users", "/api/users", {"email": "x@y.z", "name": "u"}),
        _post(
            "op_create_users_addresses",
            "/api/users/addresses",
            {"userId": "<user_id>", "city": "C", "street": "S"},
        ),
        _post(
            "op_create_orders",
            "/api/orders",
            {"addressId": "<address_id>", "items": [{"sku": "A", "qty": 1}]},
        ),
        _post(
            "op_pay",
            "/api/payments/pay",
            {"orderId": "<order_id>", "amount": 1, "channel": "BALANCE"},
        ),
        _get("op_list_users", "/api/users"),
        _get("op_list_users_addresses", "/api/users/addresses"),
        _get("op_list_orders", "/api/orders"),
        _delete("op_del_users", "/api/users/{id}"),
        _delete("op_del_users_addresses", "/api/users/addresses/{id}"),
        _delete("op_del_orders", "/api/orders/{id}"),
    ]
    ir["relations"] = [
        {
            "relation_type": "permits",
            "actor_ref": "actor_buyer",
            "operation_ref": op["id"],
            "status": "accepted",
            "source_refs": [{"kind": "document", "locator": "prd"}],
        }
        for op in ir["operations"]
        if op["method"] == "POST"
    ]
    ir["body_reference_relations"] = [
        _body_ref("op_create_users_addresses", "userId", "ent_users"),
        _body_ref("op_create_orders", "addressId", "ent_addresses"),
        _body_ref("op_pay", "orderId", "ent_orders"),
    ]
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_orders",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    assert [step["creates_entity_ref"] for step in result["steps"]] == [
        "ent_users",
        "ent_addresses",
        "ent_orders",
    ]
    assert result["steps"][1]["operation_ref"] == "op_create_users_addresses"
    assert result["steps"][1]["identity_binding_target"] == "addressId"
    assert result["steps"][0]["identity_binding_target"] == "userId"


def test_unresolvable_nested_reference_is_reported_not_invented() -> None:
    """A nested reference whose entity has NO create op (e.g. a system with
    no user-registration endpoint) stays a placeholder and is REPORTED —
    the bind gate stays the visible witness."""
    ir = _ir()
    ir["operations"] = [
        op
        for op in ir["operations"]
        if op["id"] not in {"op_create_user", "op_del_user"}
    ]
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    assert [step["creates_entity_ref"] for step in result["steps"]] == [
        "ent_address",
        "ent_order",
    ]
    unresolved = result["detail"]["unresolved_nested_references"]
    assert any(row.get("entity_ref") == "ent_user" for row in unresolved)
    assert any(
        row.get("reason_code") == "MULTI_LEVEL_DEPENDENCY_CREATE_MISSING"
        for row in unresolved
    )


def test_qualifier_prefixed_reference_resolves_to_base_entity() -> None:
    """billingAddressId / shippingAddressId resolve to the address entity
    through structural qualifier stripping."""
    from ai_test_asset_center.multi_level_dependency_chain import (
        _entity_candidates,
    )

    assert "address" in _entity_candidates("billingAddressId")
    assert "address" in _entity_candidates("shippingAddressId")
    assert "user" in _entity_candidates("ownerUserId")
    assert _entity_candidates("orderId") == ["order"]


def test_plan_is_unchanged_by_industry_terms_inside_descriptions() -> None:
    ir = _ir()
    ir["operations"][0]["summary"] = "创建订单（电商行业）"
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    assert result["create_operation_ref"] == "op_create_order"


def test_produces_relation_create_is_used_when_collection_missing() -> None:
    """A registration endpoint that PRODUCES the user entity (IR data-flow
    edge) is the user create when no collection create exists — provided it
    carries an example and a declared compensator on its own prefix."""
    ir = _ir()
    # Remove the collection user create/list; keep DELETE /api/users/{id}.
    ir["operations"] = [
        op
        for op in ir["operations"]
        if op["id"] not in {"op_create_user", "op_list_users"}
    ]
    ir["operations"].append(
        _post(
            "op_register_user",
            "/api/auth/register",
            {"email": "x@y.z", "name": "u", "password": "p"},
        )
    )
    # Compensator on the registration prefix (account deletion).
    ir["operations"].append(
        _delete("op_del_register", "/api/auth/register/{id}")
    )
    ir["relations"].append(
        {
            "relation_type": "produces",
            "from_ref": "op_register_user",
            "to_ref": "ent_user",
            "operation_ref": "op_register_user",
            "status": "accepted",
            "source_refs": [{"kind": "document", "locator": "prd"}],
        }
    )
    # The produced create still needs a declared fixture actor (the create
    # authority refuses caller-order inference), so declare the permit.
    ir["relations"].append(
        {
            "relation_type": "permits",
            "actor_ref": "actor_buyer",
            "operation_ref": "op_register_user",
            "status": "accepted",
            "source_refs": [{"kind": "document", "locator": "prd"}],
        }
    )
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    assert [step["creates_entity_ref"] for step in result["steps"]] == [
        "ent_user",
        "ent_address",
        "ent_order",
    ]
    assert result["steps"][0]["operation_ref"] == "op_register_user"


def test_produces_create_without_compensator_is_reported_not_invented() -> None:
    """A produces create whose source surface declares NO compensator (no
    account-deletion endpoint) is refused at the cleanup authority — the gap
    is reported, never papered over with a guessed delete."""
    ir = _ir()
    ir["operations"] = [
        op
        for op in ir["operations"]
        if op["id"] not in {"op_create_user", "op_list_users"}
    ]
    ir["operations"].append(
        _post(
            "op_register_user",
            "/api/auth/register",
            {"email": "x@y.z", "name": "u", "password": "p"},
        )
    )
    ir["relations"].append(
        {
            "relation_type": "produces",
            "from_ref": "op_register_user",
            "to_ref": "ent_user",
            "operation_ref": "op_register_user",
            "status": "accepted",
            "source_refs": [{"kind": "document", "locator": "prd"}],
        }
    )
    # Declare the fixture actor so the create authority reaches the cleanup
    # gate (the missing compensator is what this test must surface).
    ir["relations"].append(
        {
            "relation_type": "permits",
            "actor_ref": "actor_buyer",
            "operation_ref": "op_register_user",
            "status": "accepted",
            "source_refs": [{"kind": "document", "locator": "prd"}],
        }
    )
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    unresolved = result["detail"]["unresolved_nested_references"]
    assert any(
        row.get("entity_ref") == "ent_user"
        and row.get("reason_code") == "MULTI_LEVEL_DEPENDENCY_CLEANUP_MISSING"
        for row in unresolved
    )


def test_primary_key_wins_over_business_keys_in_identity_resolution():
    """orders declares identity_fields ['id', 'order_no'] (schema PRIMARY KEY
    + UNIQUE). The dependency chain must pick the structural primary key, not
    block the whole chain as AMBIGUOUS — run26b: callback idempotency
    obligations died at compile with MULTI_LEVEL_DEPENDENCY_IDENTITY_SOURCE_
    AMBIGUOUS because of exactly this multi-field declaration."""
    from ai_test_asset_center.multi_level_dependency_chain import (
        _declared_entity_identity_fields,
    )

    entity = {"identity_fields": ["id", "order_no"]}
    fields = _declared_entity_identity_fields(entity)
    assert fields == ["id", "order_no"]
    import re

    structural = [
        field
        for field in fields
        if re.sub(r"[^a-z0-9]+", "", field.lower())
        in {"id", "uuid", "pk", "key", "uid", "guid"}
    ]
    assert structural == ["id"]

    # Pure business keys (no structural key) stay ambiguous — fail closed.
    entity2 = {"identity_fields": ["order_no", "payment_no"]}
    structural2 = [
        field
        for field in _declared_entity_identity_fields(entity2)
        if re.sub(r"[^a-z0-9]+", "", field.lower())
        in {"id", "uuid", "pk", "key", "uid", "guid"}
    ]
    assert structural2 == []


def test_caller_scoped_ownership_field_is_not_a_dependency() -> None:
    """A parent create whose documentation declares caller scope defaults its
    ownership identity field (userId) to the caller — it is NOT a referenced
    collection row. The chain must not plan a user-registration dependency for
    it (that would mint a brand-new account); the runtime executor binds the
    value from the step actor's login-observed account_id instead.

    The signal is the OPERATION-level own-scope declaration (只能为自己/本人/
    own), never the field name alone — a bare ``userId`` without that
    declaration stays a real FK dependency (the existing user→address→order
    chain tests pin that side).
    """
    ir = _ir()
    # Address create declares caller scope on its own documentation: the target
    # service defaults userId to the token's subject (targetUserId = userId ||
    # req.user.id), so it is not an establishment dependency.
    for op in ir["operations"]:
        if op["id"] == "op_create_address":
            op["description"] = "普通用户只能为自己创建地址；管理员代建须审计。"
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    # No user-registration step: the address create's userId is caller-scoped.
    creates = [step["creates_entity_ref"] for step in result["steps"]]
    assert "ent_user" not in creates
    assert creates == ["ent_address", "ent_order"]


def test_bare_ownership_field_without_scope_declaration_is_a_dependency() -> None:
    """The counter-case: no own-scope declaration on the parent create, so the
    ``userId`` remains a real foreign-key dependency and the user entity IS
    established (leaves-first: user -> address -> order)."""
    ir = _ir()
    result = plan_multi_level_dependency_chain(
        behavior_ir=ir,
        entity_id="ent_order",
        reference_field="orderId",
        actor_refs=["actor_buyer"],
    )
    assert result["status"] == PLANNED
    assert [step["creates_entity_ref"] for step in result["steps"]] == [
        "ent_user",
        "ent_address",
        "ent_order",
    ]
