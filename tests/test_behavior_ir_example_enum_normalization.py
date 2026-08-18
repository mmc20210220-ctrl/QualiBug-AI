"""Regression: source-declared example-enum normalization in Behavior IR.

The openapi example for product status said ``ACTIVE`` while the target's own
schema component declares ``DRAFT/ON_SALE/OFF_SALE/DELETED``; governed fixture
writes sent the illegal value and the target rejected every write with
``products_status_check`` (500), blocking whole obligation families. The
single-segment ``property_path`` (``['status']``) also dropped the model enum
from the data-model enum index, so no legal set ever reached the IR.

These tests pin the fix: DB CHECK / component-schema enums reach entity field
``enum_values``, and operation request examples are normalized to a legal
value when the documented value is absent from the target's own model. Values
the model declares legal are preserved verbatim.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import build_behavior_ir_from_knowledge_asset


def _build_product_asset() -> dict:
    """Knowledge asset with a DB CHECK enum + a single-segment component enum."""
    return {
        "business_objects": [
            {"name": "products", "kind": "business_object"},
        ],
        "data_tables": [
            {
                "name": "products",
                "table_id": "table:products",
                "kind": "resource",
                "identity_fields": ["id"],
                "columns": ["id", "sku", "status", "price"],
                "fields": ["id", "sku", "status", "price"],
                "check_constraints": [
                    {
                        "column": "status",
                        "values": ["DRAFT", "ON_SALE", "OFF_SALE", "DELETED"],
                    }
                ],
            }
        ],
        "interfaces": [
            {
                "method": "POST",
                "path": "/api/products/admin",
                "source_id": "src_openapi",
                "source_kind": "openapi",
                "operation_id": "createProduct",
                "technical_declarations": [
                    {
                        "property_path": ["status"],
                        "constraints": {
                            "enum": ["DRAFT", "ON_SALE", "OFF_SALE", "DELETED"]
                        },
                        "source_locator": (
                            "openapi.yaml#block=json-pointer:"
                            "/components/schemas/Product/properties/status"
                        ),
                    }
                ],
            },
            {
                "method": "POST",
                "path": "/api/coupons/admin",
                "source_id": "src_openapi",
                "source_kind": "openapi",
                "operation_id": "createCoupon",
                "technical_declarations": [
                    {
                        "property_path": ["status"],
                        "constraints": {"enum": ["ACTIVE", "DISABLED"]},
                        "source_locator": (
                            "openapi.yaml#block=json-pointer:"
                            "/components/schemas/Coupon/properties/status"
                        ),
                    }
                ],
            },
        ],
    }


def _product_operations() -> list[dict]:
    return [
        {
            "method": "POST",
            "path": "/api/products/admin",
            "operation_id": "createProduct",
            "request_example": {
                "sku": "SKU-001",
                "price": 100,
                "status": "ACTIVE",
                "title": "旗舰手机",
            },
            "request_schema": {},
            "security": [],
        },
        {
            "method": "POST",
            "path": "/api/products/admin/{sku}/status",
            "operation_id": "updateProductStatus",
            "request_example": {"status": "ACTIVE"},
            "request_schema": {},
            "security": [],
        },
        {
            "method": "POST",
            "path": "/api/coupons/admin",
            "operation_id": "createCoupon",
            "request_example": {"code": "NEW100", "status": "ACTIVE"},
            "request_schema": {},
            "security": [],
        },
    ]


def test_db_check_enum_reaches_entity_field() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _build_product_asset(),
        api_operations=_product_operations(),
    )
    products = next(
        e
        for e in ir.get("entities") or []
        if str(e.get("name") or "").lower() == "products"
    )
    status = next(
        f
        for f in products.get("fields") or []
        if str(f.get("name") or "").lower() == "status"
    )
    assert status.get("enum_values") == [
        "DRAFT", "ON_SALE", "OFF_SALE", "DELETED",
    ]


def test_illegal_example_status_is_normalized_to_legal_value() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _build_product_asset(),
        api_operations=_product_operations(),
    )
    ops = {
        (str(o.get("method") or "").upper(), str(o.get("path") or "")): o
        for o in ir.get("operations") or []
    }
    create = ops[("POST", "/api/products/admin")]
    assert create["request_example"]["status"] == "DRAFT"
    receipt = create.get("example_enum_normalization_receipt")
    assert receipt is not None
    assert receipt["status"] == "NORMALIZED"
    assert receipt["normalized_fields"] == ["status"]
    assert receipt["entity_ref"] == "products"

    update = ops[("POST", "/api/products/admin/{sku}/status")]
    assert update["request_example"]["status"] == "DRAFT"


def test_legal_example_status_is_preserved_verbatim() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _build_product_asset(),
        api_operations=_product_operations(),
    )
    create_coupon = next(
        o
        for o in ir.get("operations") or []
        if str(o.get("path") or "") == "/api/coupons/admin"
    )
    # ACTIVE is declared legal for coupons — never rewritten.
    assert create_coupon["request_example"]["status"] == "ACTIVE"
    assert create_coupon.get("example_enum_normalization_receipt") is None


def test_plural_model_enum_matches_table_entity() -> None:
    """inventory_locks (table) resolves the InventoryLock model enum."""
    asset = _build_product_asset()
    asset["business_objects"].append(
        {"name": "inventory_locks", "kind": "business_object"}
    )
    asset["data_tables"].append(
        {
            "name": "inventory_locks",
            "table_id": "table:inventory_locks",
            "kind": "resource",
            "identity_fields": ["id"],
            "columns": ["id", "order_id", "sku", "qty", "status"],
            "fields": ["id", "order_id", "sku", "qty", "status"],
        }
    )
    asset["interfaces"].append(
        {
            "method": "POST",
            "path": "/api/inventory/locks/{id}/status",
            "source_id": "src_openapi",
            "source_kind": "openapi",
            "operation_id": "setLockStatus",
            "technical_declarations": [
                {
                    "property_path": ["status"],
                    "constraints": {"enum": ["LOCKED", "RELEASED", "CONSUMED"]},
                    "source_locator": (
                        "openapi.yaml#block=json-pointer:"
                        "/components/schemas/InventoryLock/properties/status"
                    ),
                }
            ],
        }
    )
    operations = _product_operations() + [
        {
            "method": "POST",
            "path": "/api/inventory/locks/{id}/status",
            "operation_id": "setLockStatus",
            "request_example": {"status": "ACTIVE"},
            "request_schema": {},
            "security": [],
        }
    ]
    ir = build_behavior_ir_from_knowledge_asset(asset, api_operations=operations)
    lock_op = next(
        o
        for o in ir.get("operations") or []
        if str(o.get("path") or "") == "/api/inventory/locks/{id}/status"
    )
    assert lock_op["request_example"]["status"] == "LOCKED"
