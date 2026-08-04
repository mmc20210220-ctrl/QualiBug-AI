"""Same-role owned-collection isolation with source-grounded ownership binders."""
from __future__ import annotations

from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.experiment_protocols_base import compile_family_protocol
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


def _relation(
    relation_id: str,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    *,
    operation_ref: str = "",
    actor_ref: str = "",
) -> dict:
    return {
        "id": relation_id,
        "relation_type": relation_type,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "operation_ref": operation_ref or to_ref,
        "actor_ref": actor_ref or from_ref,
        "preconditions": [],
        "effects": [],
        "status": "accepted",
        "confidence": 0.9,
        "source_refs": [{"source_id": "test", "locator": relation_id, "kind": "relation"}],
    }


def _collection_ir() -> dict:
    ir = empty_behavior_ir(project_id="collection-isolation")
    ir.update({
        "operations": [
            {
                "id": "op-cart-get",
                "method": "GET",
                "path": "/api/cart/items",
                "read_write": "read",
                "summary": "list cart items",
                "tags": ["api"],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "GET /api/cart/items", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-cart-post",
                "method": "POST",
                "path": "/api/cart/items",
                "read_write": "write",
                "request_example": {"sku": "SKU-1", "qty": 1},
                "request_schema": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "sku": {"type": "string"},
                                    "qty": {"type": "integer"},
                                },
                            }
                        }
                    }
                },
                "tags": ["api"],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "POST /api/cart/items", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-cart-delete",
                "method": "DELETE",
                "path": "/api/cart/items/{id}",
                "read_write": "write",
                "tags": ["api"],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "DELETE /api/cart/items/{id}", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-addresses",
                "method": "GET",
                "path": "/api/users/addresses",
                "read_write": "read",
                "summary": "查询用户地址，应校验归属 userId",
                "tags": ["api", "userId"],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "GET /api/users/addresses", "kind": "api_operation"}
                ],
            },
            {
                "id": "op-me",
                "method": "GET",
                "path": "/api/auth/me",
                "read_write": "read",
                "tags": ["api"],
                "source_refs": [
                    {"source_id": "api_spec", "locator": "GET /api/auth/me", "kind": "api_operation"}
                ],
            },
        ],
        "entities": [
            {
                "id": "ent-cart-items",
                "name": "cart_items",
                "fields": ["id", "sku", "qty", "user_id"],
                "status": "accepted",
            },
        ],
        "actors": [
            {
                "id": "actor-buyer-a",
                "role": "buyer",
                "account_ref": "buyer_a",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:buyer_a",
            },
            {
                "id": "actor-buyer-b",
                "role": "buyer",
                "account_ref": "buyer_b",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:buyer_b",
            },
        ],
        "relations": [
            _relation("owns-a-get", "owns", "actor-buyer-a", "op-cart-get", operation_ref="op-cart-get", actor_ref="actor-buyer-a"),
            _relation("owns-b-get", "owns", "actor-buyer-b", "op-cart-get", operation_ref="op-cart-get", actor_ref="actor-buyer-b"),
            _relation("owns-a-post", "owns", "actor-buyer-a", "op-cart-post", operation_ref="op-cart-post", actor_ref="actor-buyer-a"),
            _relation("owns-b-post", "owns", "actor-buyer-b", "op-cart-post", operation_ref="op-cart-post", actor_ref="actor-buyer-b"),
            _relation("obs-get", "observes", "op-cart-get", "ent-cart-items", operation_ref="op-cart-get"),
            _relation("prod-post", "produces", "op-cart-post", "ent-cart-items", operation_ref="op-cart-post"),
            _relation("comp-del", "compensates", "op-cart-delete", "op-cart-post", operation_ref="op-cart-delete"),
        ],
    })
    return ir


def test_collection_read_isolation_uses_corpus_ownership_binder() -> None:
    compiled = compile_obligations_from_behavior_ir(_collection_ir())
    isolation = [
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-cart-get"
    ]
    assert isolation, "owned collection GET must emit isolation when corpus declares userId"
    prop = isolation[0]["property"]
    assert prop["ownership_param"] == "userId"
    assert prop["ownership_param_location"] == "query"
    assert prop["identity_binding_target"] == "user_id"
    assert "owned_resource" not in isolation[0]["required_fixtures"]


def test_collection_write_isolation_binds_ownership_in_body() -> None:
    compiled = compile_obligations_from_behavior_ir(_collection_ir())
    isolation = [
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-cart-post"
    ]
    assert isolation
    prop = isolation[0]["property"]
    assert prop["ownership_param"] == "userId"
    assert prop["ownership_param_location"] == "body"

    protocol = compile_family_protocol(
        risk_family="isolation",
        operation={"id": "op-cart-post", "method": "POST", "path": "/api/cart/items", "request_example": {"sku": "SKU-1", "qty": 1}},
        operation_ref="op-cart-post",
        control_actor_ref="actor-buyer-a",
        treatment_actor_ref="actor-buyer-b",
        property_spec=prop,
    )
    assert protocol["status"] == "COMPILED"
    treatment = protocol["treatment_plan"][0]
    assert treatment["body"]["userId"] == "{user_id}"
    assert "userId" not in (protocol["control_plan"][0].get("body") or {})


def test_collection_isolation_experiment_binds_owner_identity_and_query() -> None:
    ir = _collection_ir()
    obligation = next(
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-cart-get"
    )
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    treatment = experiment["treatment_plan"][0]
    assert treatment.get("query") == {"userId": "{user_id}"}
    targets = {
        row.get("target")
        for row in experiment.get("binding_plan") or []
        if isinstance(row, dict)
    }
    assert "user_id" in targets


def test_post_isolation_carries_delete_cleanup_requirement() -> None:
    compiled = compile_obligations_from_behavior_ir(_collection_ir())
    isolation = next(
        row
        for row in compiled["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-cart-post"
    )
    cleanup = isolation.get("cleanup_requirement") or {}
    assert cleanup.get("required") is True
    assert cleanup.get("operation_ref") == "op-cart-delete"

    experiment = compile_experiment_for_obligation(
        isolation,
        behavior_ir=_collection_ir(),
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    cleanup_ops = {
        row.get("operation_ref")
        for row in experiment.get("cleanup_plan") or []
        if isinstance(row, dict)
    }
    assert "op-cart-delete" in cleanup_ops


def test_uncompensated_owned_write_isolation_stays_nrw() -> None:
    ir = _collection_ir()
    ir["operations"] = [
        op for op in ir["operations"] if op["id"] != "op-cart-delete"
    ]
    ir["relations"] = [
        row for row in ir["relations"] if row["id"] != "comp-del"
    ]
    isolation = next(
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-cart-post"
    )
    cleanup = isolation.get("cleanup_requirement") or {}
    assert cleanup.get("required") is True
    assert not cleanup.get("operation_ref")

    experiment = compile_experiment_for_obligation(
        isolation,
        behavior_ir=ir,
        environment_type="test",
    )
    # Non-production targets compile with an honest accepted-residue plan
    # instead of blocking: residue is declared, never disguised as cleanup.
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    cleanup_actions = {
        row.get("action")
        for row in experiment.get("cleanup_plan") or []
        if isinstance(row, dict)
    }
    assert cleanup_actions == {"accepted_residue"}
    residue_notices = {
        row.get("residue_notice")
        for row in experiment.get("cleanup_plan") or []
        if isinstance(row, dict)
    }
    assert residue_notices == {"no_source_compensator:op-cart-post"}
