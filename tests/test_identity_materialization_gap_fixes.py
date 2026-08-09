"""Regression tests for run9 identity/parameter materialization gaps (task 18).

run9 facts (BLOCKED_MISSING_BINDING):
  * 18 x ``unresolved_path_placeholders:userId`` — query/path-located
    ownership identity params (GET /api/cart/items?userId=…,
    DELETE /api/cart/selected?userId=…, GET /api/orders?userId=…,
    GET /api/users/addresses?userId=…) had NO binding-plan target, so the
    plan-step query spec ``{userId}`` stayed unresolved and the experiment
    died pre-transport even though the identity is runtime-observed material
    (the arm actor's login-observed account id).
  * 13 x ``missing_required_body_fields:items/products`` — batch interfaces
    (POST /api/cart/checkout-preview, /api/cart/items/bulk,
    /api/inventory/bulk-reserve, /api/products/admin/import) whose schema
    fallback body generation produced an EMPTY array ``[]`` for the required
    detail field; the required-field gate treats ``[]`` as missing.
  * 6 x ``missing_required_body_fields:userId/addressId/delta`` — validation
    compile stripped schema-REQUIRED ownership identity fields from the
    control body, so the required-field gate blocked pre-transport.
  * 68 x ``BLOCKED_CONTROL_ARM_NOT_PROVEN`` — control arms shipped with
    zero/nil-UUID path-example bindings (00000000-0000-0000-0000-000000000001)
    as if they were real identities; the target 404s and the control arm is
    never proven.
  * Root enabler: the runtime actor catalog never surfaced ``account_id``
    from the login JWT, so every compile-time identity concretization and the
    runtime ``ownership_identity_param`` channel had no identity to bind.
"""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.discovery_runtime_planning_actors import (
    _jwt_claim_identity,
    _runtime_actors,
)
from ai_test_asset_center.experiment_compiler_obligation_core import (
    _source_declared_path_example_bindings,
)
from ai_test_asset_center.experiment_protocols_base import (
    _generate_minimal_body_from_schema,
    _minimal_body_from_schema,
    _minimal_array_rows,
)
from ai_test_asset_center.experiment_runtime_support import (
    _missing_required_body_fields,
)
from ai_test_asset_center.runtime_binding_graph import build_binding_plan

REPO = Path(__file__).resolve().parents[1]


# ── 1. query/path-located ownership identity params get a binding target ────
def _op_with_user_id_query(
    *,
    path: str,
    method: str,
    description: str,
) -> dict:
    return {
        "id": f"op-{method.lower()}-{path}",
        "method": method,
        "path": path,
        "operation_id": f"{method.lower()}_{path.replace('/', '_')}",
        "parameters": ["userId"],
        "request_schema": {},
        "request_example": {},
        "summary": path,
        "description": description,
    }


def test_query_located_user_id_gets_ownership_binding():
    op = _op_with_user_id_query(
        path="/api/cart/items",
        method="GET",
        description="权限：登录用户，仅限本人数据。业务约束：普通用户只能查询自己的购物车。",
    )
    plan = build_binding_plan(
        operation=op,
        obligation={"risk_family": "validation", "property": {}},
        behavior_ir={"operations": []},
    )
    targets = [entry.get("target") for entry in plan]
    assert "userId" in targets, f"userId binding target missing: {plan}"
    binding = next(entry for entry in plan if entry.get("target") == "userId")
    assert binding.get("source_priority") == "ownership_identity_param"
    assert binding.get("status") == "runtime_resolvable"


def test_delete_selected_user_id_gets_ownership_binding():
    op = _op_with_user_id_query(
        path="/api/cart/selected",
        method="DELETE",
        description="权限：登录用户，仅限本人数据。",
    )
    plan = build_binding_plan(
        operation=op,
        obligation={"risk_family": "validation", "property": {}},
        behavior_ir={"operations": []},
    )
    targets = [entry.get("target") for entry in plan]
    assert "userId" in targets, f"userId binding target missing: {plan}"


def test_non_ownership_path_param_keeps_resolver_path():
    # Regression: a plain resource path param (sku) must NOT become an
    # ownership identity binding — it keeps the resolver/blocked path.
    op = {
        "id": "op-get-inventory-sku",
        "method": "GET",
        "path": "/api/inventory/{sku}",
        "operation_id": "get_inventory_sku",
        "parameters": ["sku"],
        "request_schema": {},
        "request_example": {},
        "summary": "",
        "description": "",
    }
    plan = build_binding_plan(
        operation=op,
        obligation={"risk_family": "validation", "property": {}},
        behavior_ir={"operations": []},
    )
    binding = next(
        (entry for entry in plan if entry.get("target") == "sku"),
        None,
    )
    assert binding is not None
    assert binding.get("source_priority") != "ownership_identity_param"


# ── 2. batch array detail fields are generated non-empty from schema ───────
def test_minimal_body_array_field_generates_row():
    op = {
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": True,
                                    "properties": {
                                        "sku": {"type": "string", "example": "SKU-PHONE-001"},
                                        "qty": {"type": "integer", "example": 1},
                                    },
                                },
                                "description": "明细数组",
                            }
                        },
                        "required": ["items"],
                    }
                }
            }
        }
    }
    body = _minimal_body_from_schema(op)
    assert isinstance(body.get("items"), list) and len(body["items"]) == 1
    row = body["items"][0]
    assert row.get("sku") == "SKU-PHONE-001"
    assert row.get("qty") == 1
    # the required-field gate must no longer flag items
    missing = _missing_required_body_fields(
        body,
        {"request_schema": {"content": {"application/json": {"schema": op["request_schema"]["content"]["application/json"]["schema"]}}}},
    )
    assert "items" not in missing


def test_generate_minimal_body_array_field_generates_row():
    schema = {
        "type": "object",
        "properties": {
            "products": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string", "example": "SKU-PHONE-002"},
                        "price": {"type": "number", "example": 4999},
                    },
                },
            }
        },
        "required": ["products"],
    }
    body = _generate_minimal_body_from_schema(schema)
    assert isinstance(body.get("products"), list) and len(body["products"]) == 1
    row = body["products"][0]
    assert row.get("sku") == "SKU-PHONE-002"
    assert row.get("price") == 4999


def test_minimal_array_rows_recursive_and_empty_schema():
    # item schema without properties -> one empty object row (satisfies the
    # required-array gate without inventing field names)
    assert _minimal_array_rows({}) == [{}]
    assert _minimal_array_rows({"type": "object"}) == [{}]
    nested = _minimal_array_rows(
        {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"sku": {"type": "string", "example": "A"}}},
                }
            },
        }
    )
    assert nested == [{"lines": [{"sku": "A"}]}]


# ── 3. schema-REQUIRED ownership fields survive validation strip ───────────
def test_required_ownership_body_fields_not_stripped():
    from ai_test_asset_center.experiment_protocols_base import (
        _strip_ownership_identity_fields,
    )
    control = {"amount": 6899, "orderId": "X", "userId": "U"}
    required = {"userId"}
    keep = set(required)
    stripped = _strip_ownership_identity_fields(control, keep=keep)
    assert "userId" in stripped
    assert "amount" in stripped
    assert "orderId" in stripped


# ── 4. zero/nil-UUID path examples never become real bindings ──────────────
def test_zero_uuid_path_example_is_rejected():
    op = {
        "parameters": [
            {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "format": "uuid"},
                "example": "00000000-0000-0000-0000-000000000001",
            }
        ]
    }
    bindings = _source_declared_path_example_bindings(op, ["id"])
    assert bindings is None, f"zero-UUID example must not bind: {bindings}"


def test_real_path_example_still_binds():
    op = {
        "parameters": [
            {
                "name": "sku",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
                "example": "SKU-PHONE-001",
            }
        ]
    }
    bindings = _source_declared_path_example_bindings(op, ["sku"])
    assert bindings is not None
    assert bindings["sku"]["materialized_value"] == "SKU-PHONE-001"


# ── 5. runtime actor catalog surfaces account_id from the login JWT ────────
def test_jwt_claim_identity_extracts_id():
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpZCI6IjAyMmNiZTE0LTQ1ZmEtNDAzNS04ZjBkLTM3MjMzMTlkOGFiMiIsImVtYWlsIjoiYnV5ZXIwMUBleGFtcGxlLmNvbSJ9."
        "signature"
    )
    assert _jwt_claim_identity(token) == "022cbe14-45fa-4035-8f0d-3723319d8ab2"


def test_jwt_claim_identity_rejects_non_jwt():
    assert _jwt_claim_identity("opaque-token") == ""
    assert _jwt_claim_identity("") == ""


def test_runtime_actors_carry_account_id():
    accounts = REPO / "platform_inputs" / "benchmark_mall" / "test_accounts.json"
    if not accounts.exists():
        return  # catalog absent in this checkout — nothing to assert
    actors = _runtime_actors(REPO, "benchmark_mall", {})
    buyer = next(
        (a for a in actors if "buyer01" in (a.get("account_ref") or "")),
        None,
    )
    assert buyer is not None
    assert buyer.get("account_id"), "buyer actor must surface account_id from JWT"
