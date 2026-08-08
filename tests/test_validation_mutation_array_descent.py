"""Validation mutation array-item descent.

Root cause (session F, product module): batch-create bodies
(products: [{...}]) carry the governed fields inside the array element
schema, but the validation mutation strategies only walked TOP-LEVEL
properties. The import experiment compiled with a "remove the required
array" mutation (a missing-array test) instead of an abnormal-value test —
a negative stock/price inside products[0] stayed unreachable (PRODUCT-011
class: 批量导入接受异常库存/价格).

The fix descends into the first array element in Strategy 1 (semantic
invalid value): the same industry-neutral heuristics run over the element's
own declared properties, explicit rule targets first, and the mutation path
addresses the first element ($.products[0].stock). Generic for any
batch-create/detail body in any industry.
"""
import pytest

from ai_test_asset_center.experiment_protocols_base import compile_family_protocol


def _property_spec(raw: str = "价格必须非负") -> dict:
    return {
        "template": "invariant_validation",
        "invariant_ref": "inv_x",
        "expression": {
            "kind": "business_rule",
            "operator": "must_hold",
            "raw": raw,
        },
    }


def _import_operation(with_schema: bool = True) -> dict:
    op = {
        "id": "op_import",
        "method": "POST",
        "path": "/api/products/admin/import",
        "summary": "批量导入",
    }
    if with_schema:
        op["request_schema"] = {
            "required": ["products"],
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "products": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "category": {"type": "string"},
                                        "price": {"type": "integer"},
                                        "stock": {"type": "integer"},
                                    },
                                },
                            }
                        },
                    }
                }
            },
        }
        op["request_example"] = {
            "products": [
                {"category": "数码", "price": 4999, "stock": 50}
            ]
        }
    return op


class TestArrayItemDescent:
    def test_negative_value_inside_array_item(self):
        """A numeric item field must receive the negative mutation."""
        protocol = compile_family_protocol(
            risk_family="validation",
            operation=_import_operation(),
            operation_ref="op_import",
            control_actor_ref="actor_admin",
            treatment_actor_ref="actor_buyer",
            property_spec=_property_spec(),
        )
        assert protocol["status"] == "COMPILED"
        step = protocol["treatment_plan"][0]
        mutation = step["mutation"]
        # Schema order: category (string) first, then price — price is the
        # first negative-capable item field.
        assert mutation["json_path"] == "$.products[0].price"
        assert mutation["constraint"] == "semantic:negative_value"
        products = step["body"]["products"]
        assert products[0]["price"] == -1
        assert products[0]["stock"] == 50  # untouched

    def test_explicit_rule_target_wins(self):
        """A rule naming the item field must target that field first."""
        protocol = compile_family_protocol(
            risk_family="validation",
            operation=_import_operation(),
            operation_ref="op_import",
            control_actor_ref="actor_admin",
            treatment_actor_ref="actor_buyer",
            property_spec=_property_spec("库存不能为负，stock 必须非负"),
        )
        assert protocol["status"] == "COMPILED"
        mutation = protocol["treatment_plan"][0]["mutation"]
        assert mutation["json_path"] == "$.products[0].stock"
        assert protocol["treatment_plan"][0]["body"]["products"][0]["stock"] == -1

    def test_array_without_numeric_fields_falls_through(self):
        """An array whose items carry no mutatable fields must fall through
        to the required-removal strategy instead of crashing."""
        op = _import_operation()
        op["request_schema"] = {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["products"],
                        "properties": {
                            "products": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "sku": {"type": "string"},
                                        "title": {"type": "string"},
                                    },
                                },
                            }
                        },
                    }
                }
            },
        }
        op["request_example"] = {"products": [{"sku": "SKU-1", "title": "T"}]}
        protocol = compile_family_protocol(
            risk_family="validation",
            operation=op,
            operation_ref="op_import",
            control_actor_ref="actor_admin",
            treatment_actor_ref="actor_buyer",
            property_spec=_property_spec(),
        )
        assert protocol["status"] == "COMPILED"
        mutation = protocol["treatment_plan"][0]["mutation"]
        assert mutation["constraint"] == "required"
        assert mutation["json_path"] == "$.products"
