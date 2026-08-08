"""Read-side allowed-states fallback: exposure rule + declared state enum.

Root cause (session F, product module): validation-family read-side rules
(用户端不展示下架商品、草稿商品、内部商品) bound to public GET surfaces whose
operation declaration omits the allowed-state set (权限：公开。only) were
BLOCKED with ``read_side_rule_lacks_decidable_assertion`` — PRODUCT-001/002/
008-style defects stayed unreachable even with the execution budget fixed.

The fallback in ``_read_side_allowed_states`` makes the rule decidable from
source-declared material only:
1. the rule text names non-public states (generic violation vocabulary);
2. the operation is a PUBLIC surface (no declared roles) — restricted
   surfaces (卖家目录) legitimately show non-public states to their owner;
3. the rule's subject entity carries a state enum in the IR model (schema
   CHECK / OpenAPI enum);
→ allowed rows = the enum literals whose own literal meaning is public
(ON_SALE/ACTIVE/ENABLED/…). Literal semantics, never a translation table.
"""
import pytest

from ai_test_asset_center.experiment_protocols_base import (
    _read_side_allowed_states,
)


def _rule_property(raw: str, subject: str = "product") -> dict:
    return {
        "template": "invariant_validation",
        "expression": {"kind": "business_rule", "operator": "must_hold", "raw": raw},
        "subject_entity_refs": [subject],
    }


def _public_op() -> dict:
    return {
        "id": "op_rec",
        "method": "GET",
        "path": "/api/products/recommendations/home",
        "description": "权限：公开。",
        "required_roles": [],
    }


def _restricted_op() -> dict:
    return {
        "id": "op_catalog",
        "method": "GET",
        "path": "/api/products/seller/{sellerId}/catalog",
        "description": "权限：商家本人或管理员。",
        "required_roles": ["admin", "seller"],
    }


def _product_ir() -> dict:
    return {
        "entities": [
            {
                "id": "ent_product",
                "name": "product",
                "fields": [
                    {
                        "field_id": "cf_status",
                        "name": "status",
                        "semantic_type": "STATE",
                        "enum_values": ["DRAFT", "ON_SALE", "OFF_SALE", "DELETED"],
                    }
                ],
            }
        ],
        "operations": [],
    }


class TestReadSideAllowedStatesFallback:
    def test_declared_states_still_win(self):
        """The declared allowed set (仅返回 ON_SALE) must be used as-is."""
        op = _public_op()
        op["description"] = "权限：公开。\n\n业务约束：用户端默认仅返回 ON_SALE 商品。"
        assert _read_side_allowed_states(
            _rule_property("用户端不展示下架商品"), op, behavior_ir=_product_ir()
        ) == {"ON_SALE"}

    def test_fallback_public_surface_product(self):
        """Exposure rule + public surface + product enum → {ON_SALE}."""
        allowed = _read_side_allowed_states(
            _rule_property("用户端不展示下架商品、草稿商品、内部商品"),
            _public_op(),
            behavior_ir=_product_ir(),
        )
        assert allowed == {"ON_SALE"}

    def test_fallback_rule_naming_logical_delete(self):
        """逻辑删除后商品不得出现在用户端列表或推荐 also triggers the fallback."""
        allowed = _read_side_allowed_states(
            _rule_property("业务约束：逻辑删除后商品不得出现在用户端列表或推荐"),
            _public_op(),
            behavior_ir=_product_ir(),
        )
        assert allowed == {"ON_SALE"}

    def test_restricted_surface_no_fallback(self):
        """Seller catalog (商家本人或管理员) must NOT get a state filter."""
        assert _read_side_allowed_states(
            _rule_property("用户端不展示下架商品、草稿商品、内部商品"),
            _restricted_op(),
            behavior_ir=_product_ir(),
        ) == set()

    def test_non_exposure_rule_no_fallback(self):
        """A rule without exposure modality stays without allowed states."""
        assert _read_side_allowed_states(
            _rule_property("价格必须以商品服务当前价格为准，不信任客户端 price"),
            _public_op(),
            behavior_ir=_product_ir(),
        ) == set()

    def test_missing_entity_enum_no_fallback(self):
        """No state enum in the IR model → empty set (visible BLOCKED stays)."""
        ir = {"entities": [{"id": "e1", "name": "product", "fields": []}]}
        assert _read_side_allowed_states(
            _rule_property("用户端不展示下架商品"), _public_op(), behavior_ir=ir
        ) == set()

    def test_no_behavior_ir_no_fallback(self):
        assert _read_side_allowed_states(
            _rule_property("用户端不展示下架商品"), _public_op(), behavior_ir=None
        ) == set()

    def test_fallback_respects_generic_enum(self):
        """A different industry enum resolves through the same literal rule."""
        ir = {
            "entities": [
                {
                    "id": "e_doc",
                    "name": "document",
                    "fields": [
                        {
                            "field_id": "cf_st",
                            "name": "status",
                            "semantic_type": "STATE",
                            "enum_values": ["DRAFT", "PUBLISHED", "ARCHIVED"],
                        }
                    ],
                }
            ],
            "operations": [],
        }
        allowed = _read_side_allowed_states(
            _rule_property("用户端不展示草稿、归档文档", subject="document"),
            _public_op(),
            behavior_ir=ir,
        )
        assert allowed == {"PUBLISHED"}
