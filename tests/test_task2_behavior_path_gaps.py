"""Task-2: behavior-path generation coverage fixes (24 stage-3 FN class).

Unit tests for three generic mechanism fixes that restore behavior-path
generation for identity-scoped reads, credential/session exchanges and
action-verb resource paths:

1. Ephemeral-session classification recognizes credential-exchange markers
   (login/token/otp/captcha/session/refresh) in ANY path segment, so
   /api/auth/login/phone-style operations lose the impossible durable-effect
   observer requirement instead of blocking as BLOCKED_MISSING_OBSERVER.

2. Identity-scoped READS governed by entity-state exposure rules
   (用户端不展示下架商品 → GET /api/products/{sku}) compile a two-arm
   read whose treatment carries a runtime PATH-identity mutation
   (runtime_entity_state_violation + path_param) instead of dying as
   read_side_rule_lacks_decidable_assertion; the privacy facade keeps that
   arm instead of overriding it with non-wire-renderable path-format
   constraints.

3. Action-verb resource paths (POST /api/cart/clone/{id}) resolve their
   owned-resource collection through the module-prefix sibling collection
   (GET/POST /api/cart/items) so isolation/ownership obligations compile
   instead of BLOCKED_MISSING_FIXTURE.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ai_test_asset_center.behavior_ir_core import _is_ephemeral_session_path
from ai_test_asset_center.experiment_protocols_base import (
    _read_side_path_identity_exposure,
    compile_family_protocol,
)
from ai_test_asset_center.experiment_protocols_privacy_base import (
    compile_family_protocol as compile_family_protocol_privacy,
)
from ai_test_asset_center.runtime_binding_graph import (
    _declared_fixture_setup,
    _operation_module_prefix,
    declared_runtime_read_resolvers,
)

ROOT = Path(__file__).resolve().parents[1]


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{id}", path)


# ── 1. Ephemeral-session classification ───────────────────────────────────


def test_ephemeral_session_any_segment_credential_exchange() -> None:
    """Credential exchanges carry the marker in a non-terminal segment."""
    assert _is_ephemeral_session_path("/api/auth/login/phone") is True
    assert _is_ephemeral_session_path("/api/auth/token/impersonate") is True
    assert _is_ephemeral_session_path("/api/auth/otp/send") is True
    assert _is_ephemeral_session_path("/api/auth/login") is True


def test_ephemeral_session_excludes_durable_writes() -> None:
    """Durable writes and notification endpoints stay non-ephemeral."""
    assert _is_ephemeral_session_path("/api/auth/register") is False
    assert _is_ephemeral_session_path("/api/payments/callback/mock") is False
    assert _is_ephemeral_session_path("/api/orders/{id}/cancel") is False
    assert _is_ephemeral_session_path("/api/inventory/release") is False


# ── 2. Read-side path-identity exposure arm ───────────────────────────────


def _product_detail_operation() -> dict:
    return {
        "id": "op_products_sku",
        "method": "GET",
        "path": "/api/products/{sku}",
        "parameters": [
            {
                "name": "sku",
                "in": "path",
                "required": True,
                "description": "商品 SKU",
                "schema": {"type": "string"},
            }
        ],
        "read_write": "read",
        "source_refs": [
            {"source_id": "api", "kind": "api_operation", "locator": "GET /api/products/{sku}"}
        ],
    }


def _behavior_ir() -> dict:
    return {
        "operations": [
            _product_detail_operation(),
            {
                "id": "op_products_list",
                "method": "GET",
                "path": "/api/products",
                "read_write": "read",
            },
        ],
        "actors": [],
        "relations": [],
        "entities": [],
        "states": [],
        "invariants": [],
        "conflicts": [],
        "coverage_gaps": [],
    }


def test_read_side_path_identity_exposure_compiles_two_arm_read() -> None:
    """Exposure rule on a detail read compiles a path-identity treatment."""
    property_spec = {
        "template": "invariant_validation",
        "invariant_ref": "inv_exposure",
        "subject_entity_refs": ["product"],
        "expression": {"raw": "业务约束：逻辑删除后商品不得出现在用户端列表或推荐"},
    }
    result = _read_side_path_identity_exposure(
        operation=_product_detail_operation(),
        operation_ref="op_products_sku",
        property_spec=property_spec,
        control_actor_ref="actor_control",
        treatment_actor_ref="actor_treatment",
        behavior_ir=_behavior_ir(),
    )
    assert result is not None
    assert result["status"] == "COMPILED"
    assert len(result["control_plan"]) == 1
    treatment = result["treatment_plan"][0]
    mutation = treatment["mutation"]
    assert mutation["class"] == "runtime_entity_state_violation"
    assert mutation["path_param"] == "sku"
    assert mutation["json_path"] == "path:sku"
    assert mutation["resolver_operations"][0]["path"] == "/api/products"
    assert result["assertion"]["kind"] == "http_status_class"
    assert result["assertion"]["expected_class"] == 4


def test_read_side_path_identity_exposure_ignores_non_exposure_rule() -> None:
    """A non-exposure rule on the detail read stays None (visible gap)."""
    property_spec = {
        "template": "invariant_validation",
        "invariant_ref": "inv_other",
        "subject_entity_refs": ["product"],
        "expression": {"raw": "业务约束：价格必须为整数"},
    }
    result = _read_side_path_identity_exposure(
        operation=_product_detail_operation(),
        operation_ref="op_products_sku",
        property_spec=property_spec,
        control_actor_ref="actor_control",
        treatment_actor_ref="actor_treatment",
        behavior_ir=_behavior_ir(),
    )
    assert result is None


def test_exposure_rule_compiles_through_family_protocol() -> None:
    """The full validation protocol returns the exposure arm (not BLOCKED)."""
    property_spec = {
        "template": "invariant_validation",
        "invariant_ref": "inv_exposure",
        "subject_entity_refs": ["product"],
        "expression": {"raw": "逻辑删除后商品不得出现在用户端列表或推荐"},
    }
    result = compile_family_protocol(
        risk_family="validation",
        operation=_product_detail_operation(),
        operation_ref="op_products_sku",
        control_actor_ref="actor_control",
        treatment_actor_ref="actor_treatment",
        property_spec=property_spec,
        behavior_ir=_behavior_ir(),
    )
    assert result["status"] == "COMPILED"
    treatment = result["treatment_plan"][0]
    assert treatment["mutation"]["path_param"] == "sku"


def test_privacy_facade_keeps_exposure_arm_over_path_constraint() -> None:
    """Path-format constraint variants do not override the exposure arm."""
    property_spec = {
        "template": "invariant_validation",
        "invariant_ref": "inv_exposure",
        "subject_entity_refs": ["product"],
        "validation_constraint": "required",
        "parameter_location": "path",
        "field_tokens": ["@path", "sku"],
        "expression": {"raw": "逻辑删除后商品不得出现在用户端列表或推荐"},
    }
    result = compile_family_protocol_privacy(
        risk_family="validation",
        operation=_product_detail_operation(),
        operation_ref="op_products_sku",
        control_actor_ref="actor_control",
        treatment_actor_ref="actor_treatment",
        property_spec=property_spec,
        behavior_ir=_behavior_ir(),
    )
    assert result["status"] == "COMPILED"
    treatment = result["treatment_plan"][0]
    mutation = treatment.get("mutation") or {}
    assert mutation.get("class") == "runtime_entity_state_violation"
    assert mutation.get("path_param") == "sku"


# ── 3. Action-path collection resolution ──────────────────────────────────


def _action_path_ir() -> dict:
    operations = [
        {
            "id": "op_cart_clone",
            "method": "POST",
            "path": "/api/cart/clone/{id}",
            "read_write": "write",
            "request_schema": {"type": "object", "properties": {}},
        },
        {
            "id": "op_cart_items_get",
            "method": "GET",
            "path": "/api/cart/items",
            "read_write": "read",
        },
        {
            "id": "op_cart_items_post",
            "method": "POST",
            "path": "/api/cart/items",
            "read_write": "write",
            "request_example": {"sku": "SKU-1", "qty": 1},
            "request_schema": {
                "type": "object",
                "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}},
            },
            "parameters": [],
        },
        {
            "id": "op_cart_items_delete",
            "method": "DELETE",
            "path": "/api/cart/items/{id}",
            "read_write": "write",
        },
        {
            "id": "op_cart_health",
            "method": "GET",
            "path": "/api/cart/health",
            "read_write": "read",
        },
    ]
    return {
        "operations": operations,
        "actors": [
            {"id": "actor_buyer", "role": "buyer", "account_ref": "buyer@example.com"},
        ],
        "relations": [
            {
                "id": "rel_cart_items_permits",
                "relation_type": "permits",
                "from_ref": "actor_buyer",
                "to_ref": "op_cart_items_post",
                "operation_ref": "op_cart_items_post",
                "actor_ref": "actor_buyer",
                "derivation": "explicit",
                "status": "accepted",
            }
        ],
        "entities": [],
        "states": [],
        "invariants": [],
        "conflicts": [],
        "coverage_gaps": [],
    }


def test_operation_module_prefix() -> None:
    assert _operation_module_prefix("/api/cart/clone/{id}") == "/api/cart"
    assert _operation_module_prefix("/api/users/admin/users/{id}/balance") == "/api/users"


def test_action_path_read_resolver_uses_sibling_collection() -> None:
    """clone/{id} resolves its placeholder through GET /api/cart/items."""
    ir = _action_path_ir()
    clone = next(
        op for op in ir["operations"] if str(op.get("path")) == "/api/cart/clone/{id}"
    )
    resolvers = declared_runtime_read_resolvers(clone, behavior_ir=ir)
    assert any(
        str(r.get("path")) == "/api/cart/items" for r in resolvers
    ), f"expected sibling collection resolver, got {resolvers}"


def test_action_path_fixture_setup_uses_sibling_create() -> None:
    """clone/{id} derives its owned-resource fixture from POST /api/cart/items."""
    ir = _action_path_ir()
    clone = next(
        op for op in ir["operations"] if str(op.get("path")) == "/api/cart/clone/{id}"
    )
    setup = _declared_fixture_setup(clone, target="id", behavior_ir=ir)
    assert setup is not None
    assert setup["path"] == "/api/cart/items"
    assert setup["method"] == "POST"


def test_action_path_fixture_setup_absent_without_sibling_collection() -> None:
    """Without a paired collection create the fixture stays empty (no guess)."""
    ir = _action_path_ir()
    ops = [op for op in ir["operations"] if str(op.get("path")) != "/api/cart/items"]
    ir = {**ir, "operations": ops}
    clone = next(
        op for op in ir["operations"] if str(op.get("path")) == "/api/cart/clone/{id}"
    )
    setup = _declared_fixture_setup(clone, target="id", behavior_ir=ir)
    assert setup == {}


def test_structural_collection_fixture_unchanged() -> None:
    """Regular resource collections keep their existing fixture derivation."""
    ir = _action_path_ir()
    delete = next(
        op
        for op in ir["operations"]
        if str(op.get("path")) == "/api/cart/items/{id}"
    )
    # The DELETE derives through the same fallback (GET+POST pair exists).
    setup = _declared_fixture_setup(delete, target="id", behavior_ir=ir)
    assert setup is not None
    assert setup["path"] == "/api/cart/items"
