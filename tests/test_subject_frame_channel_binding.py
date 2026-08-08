"""Lock-in tests for the subject-frame → operation binding channel in
behavior_ir_core (shared channel work in the working tree, protected here).

The channel resolves a rule's grounded semantic-frame subject (and its
constraint vocabulary) to declared business objects → schema tables →
consuming operations, and produces entity-scoped contract operands. These
tests pin that behaviour with a synthetic coupon-shaped asset so the shared
channel cannot regress silently.
"""
import pytest

from ai_test_asset_center import behavior_ir_core as core


def _data():
    return {
        "business_objects": [
            {"object": "coupon", "aliases": ["coupon", "voucher", "优惠券", "券码"]},
            {"object": "order", "aliases": ["order", "订单"]},
        ],
        "data_tables": [
            {"name": "coupons", "foreign_keys": []},
            {"name": "coupon_usage", "foreign_keys": ["coupons"]},
            {"name": "orders", "foreign_keys": []},
        ],
    }


def _model():
    return {
        "entities": [
            {
                "id": "ent_coupon",
                "table": "coupons",
                "fields": [
                    {"name": "expires_at", "field_id": "cf_e"},
                    {"name": "status", "field_id": "cf_st"},
                    {"name": "min_order_amount", "field_id": "cf_m"},
                ],
            },
            {
                "id": "ent_order",
                "table": "orders",
                "fields": [{"name": "status", "field_id": "cf_os"}],
            },
        ],
        "operations": [
            {"id": "op_validate", "method": "POST", "path": "/api/coupons/validate",
             "summary": "校验优惠券并试算优惠"},
            {"id": "op_claim", "method": "POST", "path": "/api/coupons/claim",
             "summary": "领取优惠券"},
            {"id": "op_order", "method": "POST", "path": "/api/orders",
             "summary": "创建订单"},
            {"id": "op_health", "method": "GET", "path": "/api/coupons/health",
             "summary": "健康检查"},
        ],
    }


def test_subject_frame_resolves_coupon_surface():
    frame = {
        "modality": "REQUIRED", "polarity": "positive", "condition": "",
        "subject": "优惠券", "behavior": "在有效期内",
        "source_anchors": [], "source_grounded": True,
    }
    result = core._subject_channel_resolution(
        {"tokens": [], "statement": "优惠券必须在有效期内"},
        frame,
        "优惠券必须在有效期内",
        _data(),
        _model(),
    )
    assert "coupon" in result["subject_objects"]
    assert "coupons" in result["entity_tables"]
    assert "op_validate" in result["op_ids"]
    assert "op_claim" in result["op_ids"]
    # Decision operations are separated out; health probes never governed.
    assert "op_validate" in result["decision_op_ids"]
    assert "op_health" not in result["op_ids"]
    # Entity-scoped operands: the coupon validity field only.
    fields = [op.get("field") for op in result["field_operands"]]
    assert "expires_at" in fields
    assert all(op.get("entity_ref") == "ent_coupon" for op in result["field_operands"])


def test_constraint_field_channel_without_subject_alias():
    # 必须满足最低订单金额 has no frame subject; the constraint vocabulary
    # (最低金额) must still resolve the coupon object through its fields.
    frame = {
        "modality": "REQUIRED", "polarity": "positive", "condition": "",
        "subject": "", "behavior": "必须满足最低订单金额",
        "source_anchors": [], "source_grounded": True,
    }
    result = core._subject_channel_resolution(
        {"tokens": [], "statement": "必须满足最低订单金额"},
        frame,
        "必须满足最低订单金额",
        _data(),
        _model(),
    )
    assert result["basis"] in {"constraint_field", "constraint_field_dominant"}
    assert "coupon" in result["subject_objects"]
    fields = [op.get("field") for op in result["field_operands"]]
    assert "min_order_amount" in fields


def test_no_frame_no_channel():
    result = core._subject_channel_resolution(
        {"tokens": []},
        None,
        "并发下单不得超卖",
        _data(),
        _model(),
    )
    assert result["op_ids"] == []
    assert result["field_operands"] == []
