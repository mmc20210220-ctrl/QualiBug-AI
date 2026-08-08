"""Unit tests for rule_contract_validation_binding (coupon semantic layer).

Locks in: entity-scoped field resolution without business_objects, constraint
operators, rule→interface binding to consuming/decision operations, the
explicit-field-name money channel, the no-duplication guard against already
bound rules, and the honesty receipts. Synthetic assets only — no LLM, no
benchmark material, no GT.
"""
import json

import pytest

from ai_test_asset_center import rule_contract_validation_binding as m


def _synthetic_ir():
    return {
        "entities": [
            {
                "id": "ent_coupon",
                "table": "coupons",
                "fields": [
                    {"name": "expires_at", "field_id": "cf_e", "semantic_type": "TIME"},
                    {"name": "starts_at", "field_id": "cf_s", "semantic_type": "TIME"},
                    {"name": "status", "field_id": "cf_st", "semantic_type": "STATE"},
                    {"name": "min_order_amount", "field_id": "cf_m", "semantic_type": "AMOUNT"},
                    {"name": "max_discount", "field_id": "cf_md", "semantic_type": "AMOUNT"},
                    {"name": "user_limit", "field_id": "cf_ul", "semantic_type": "QUANTITY"},
                    {"name": "global_limit", "field_id": "cf_gl", "semantic_type": "QUANTITY"},
                    {"name": "category_scope", "field_id": "cf_cs", "semantic_type": "SCOPE"},
                    {"name": "amount", "field_id": "cf_a", "semantic_type": "AMOUNT"},
                ],
            },
            {
                "id": "ent_order",
                "table": "orders",
                "fields": [
                    {"name": "total_amount", "field_id": "cf_ta", "semantic_type": "AMOUNT"},
                    {"name": "discount_amount", "field_id": "cf_da", "semantic_type": "AMOUNT"},
                    {"name": "payable_amount", "field_id": "cf_pa", "semantic_type": "AMOUNT"},
                ],
            },
        ],
        "operations": [
            {"id": "op_validate", "method": "POST", "path": "/api/coupons/validate",
             "summary": "校验优惠券并试算优惠"},
            {"id": "op_use", "method": "POST", "path": "/api/coupons/use", "summary": "核销优惠券"},
            {"id": "op_order", "method": "POST", "path": "/api/orders", "summary": "创建订单"},
            {"id": "op_health", "method": "GET", "path": "/api/coupons/health", "summary": "健康检查"},
        ],
        "invariants": [],
    }


def _synthetic_asset():
    return {
        "field_dictionary": [
            {"table": "coupons", "field": "expires_at", "description": "优惠券过期时间"},
        ],
        "rule_library": [
            {
                "rule_id": "rule:coupon:validity",
                "statement": "优惠券必须在有效期内",
                "semantic_frame": {
                    "modality": "REQUIRED", "polarity": "positive", "condition": "",
                    "subject": "优惠券", "behavior": "在有效期内",
                    "source_anchors": [], "source_grounded": True,
                },
                "confidence": 0.7,
                "source_id": "runtime:prd:synthetic",
            },
            {
                "rule_id": "rule:coupon:status",
                "statement": "优惠券状态必须为 ACTIVE",
                "semantic_frame": {
                    "modality": "REQUIRED", "polarity": "positive", "condition": "",
                    "subject": "优惠券状态", "behavior": "为 ACTIVE",
                    "source_anchors": ["ACTIVE"], "source_grounded": True,
                },
                "confidence": 0.7,
                "source_id": "runtime:prd:synthetic",
            },
            {
                "rule_id": "rule:coupon:min_amount",
                "statement": "必须满足最低订单金额",
                "semantic_frame": {
                    "modality": "REQUIRED", "polarity": "positive", "condition": "",
                    "subject": "", "behavior": "必须满足最低订单金额",
                    "source_anchors": [], "source_grounded": True,
                },
                "confidence": 0.7,
                "source_id": "runtime:prd:synthetic",
            },
            {
                "rule_id": "rule:money:discount_non_negative",
                "statement": "discount_amount 不能小于 0",
                "semantic_frame": {},
                "confidence": 0.6,
                "source_id": "runtime:business_rules:synthetic",
            },
            {
                "rule_id": "rule:money:bare_amount",
                "statement": "金额不能为负",
                "semantic_frame": {},
                "confidence": 0.6,
                "source_id": "runtime:business_rules:synthetic",
            },
        ],
    }


def _ir_with_unbound_invariant(ir, rule_id, description):
    ir = dict(ir)
    ir["invariants"] = [
        *(ir.get("invariants") or []),
        {
            "id": "bir_unbound_1",
            "description": description,
            "expression": {"kind": "business_rule", "operator": "must_hold",
                           "operands": [], "raw": description},
            "operation_refs": [],
            "source_rule_refs": [rule_id],
        },
    ]
    return ir


def test_validity_rule_derives_validation_invariant():
    ir = _ir_with_unbound_invariant(_synthetic_ir(), "rule:coupon:validity", "优惠券必须在有效期内")
    derived, receipt = m._derive_validation_invariants(ir, _synthetic_asset())
    assert len(derived) >= 1
    inv = next(i for i in derived if "rule:coupon:validity" in i["source_rule_refs"])
    expr = inv["expression"]
    assert expr["kind"] == "validation"
    assert expr["operator"] == "within_time_window"
    fields = {op.get("field") for op in expr["operands"]}
    assert "expires_at" in fields
    # Decision operations (validate/use) bound; health probe never governed.
    assert "op_validate" in inv["operation_refs"]
    assert "op_use" in inv["operation_refs"]
    assert "op_health" not in inv["operation_refs"]
    # Entity-scoped: never the orders money fields.
    assert "total_amount" not in fields
    assert receipt["status"] == "OK"


def test_status_rule_scoped_to_coupon_status_only():
    ir = _ir_with_unbound_invariant(_synthetic_ir(), "rule:coupon:status", "优惠券状态必须为 ACTIVE")
    derived, _ = m._derive_validation_invariants(ir, _synthetic_asset())
    inv = next(i for i in derived if "rule:coupon:status" in i["source_rule_refs"])
    operands = inv["expression"]["operands"]
    assert [op.get("field") for op in operands] == ["status"]
    assert inv["expression"].get("operator") == "must_equal"
    assert operands[0].get("expected_value") == "ACTIVE"


def test_min_amount_rule_binds_validate_with_constraint_field_only():
    ir = _ir_with_unbound_invariant(_synthetic_ir(), "rule:coupon:min_amount", "必须满足最低订单金额")
    derived, _ = m._derive_validation_invariants(ir, _synthetic_asset())
    inv = next(i for i in derived if "rule:coupon:min_amount" in i["source_rule_refs"])
    fields = [op.get("field") for op in inv["expression"]["operands"]]
    # Only the top-scored constraint field — not every amount-ish field.
    assert fields == ["min_order_amount"]
    assert inv["expression"]["operator"] == "minimum"
    assert "op_validate" in inv["operation_refs"]


def test_exact_field_name_money_rule_binds_unique_entity():
    ir = _ir_with_unbound_invariant(
        _synthetic_ir(), "rule:money:discount_non_negative", "discount_amount 不能小于 0"
    )
    derived, _ = m._derive_validation_invariants(ir, _synthetic_asset())
    inv = next(
        i for i in derived
        if "rule:money:discount_non_negative" in i["source_rule_refs"]
    )
    assert [op.get("field") for op in inv["expression"]["operands"]] == ["discount_amount"]
    assert inv.get("subject_entity_refs") == ["ent_order"]


def test_bare_amount_rule_stays_unbound_honestly():
    ir = _ir_with_unbound_invariant(_synthetic_ir(), "rule:money:bare_amount", "金额不能为负")
    derived, receipt = m._derive_validation_invariants(ir, _synthetic_asset())
    assert not any(
        "rule:money:bare_amount" in i["source_rule_refs"] for i in derived
    )
    disposition = next(
        d for d in receipt["dispositions"]
        if d.get("rule_id") == "rule:money:bare_amount"
    )
    assert disposition["disposition"] in {"MULTI_ENTITY_MONEY_AMBIGUOUS",
                                          "NO_ENTITY_FIELD_MATCH"}


def test_already_bound_rule_is_not_duplicated():
    ir = _synthetic_ir()
    ir["invariants"] = [
        {
            "id": "bir_bound_1",
            "description": "优惠券必须在有效期内",
            "expression": {"kind": "validation", "operator": "must_hold",
                           "operands": [], "raw": "优惠券必须在有效期内"},
            "operation_refs": ["op_validate"],
            "source_rule_refs": ["rule:coupon:validity"],
        }
    ]
    derived, receipt = m._derive_validation_invariants(ir, _synthetic_asset())
    assert not any("rule:coupon:validity" in i["source_rule_refs"] for i in derived)
    # The rule is excluded from the scanned set (guard) — no disposition.
    assert not any(
        d.get("rule_id") == "rule:coupon:validity" for d in receipt["dispositions"]
    )


def test_derived_invariant_never_carries_resolved_business_values():
    ir = _ir_with_unbound_invariant(_synthetic_ir(), "rule:coupon:validity", "优惠券必须在有效期内")
    derived, _ = m._derive_validation_invariants(ir, _synthetic_asset())
    inv = next(i for i in derived if "rule:coupon:validity" in i["source_rule_refs"])
    dumped = json.dumps(inv, ensure_ascii=False)
    # Field identities only — never a concrete instance value or expected
    # business payload (e.g. a specific coupon code or a money amount).
    assert "EXPIRED" not in dumped
    assert "6999" not in dumped
    for op in inv["expression"]["operands"]:
        assert op.get("field") in {
            "expires_at", "starts_at", "status", "min_order_amount",
            "max_discount", "user_limit", "global_limit", "category_scope",
            "amount", "total_amount", "discount_amount", "payable_amount",
        }


def test_public_stage_attaches_receipt():
    ir = _ir_with_unbound_invariant(_synthetic_ir(), "rule:coupon:validity", "优惠券必须在有效期内")
    out, receipt = m.bind_rule_contract_validation_invariants(ir, _synthetic_asset())
    assert out["rule_contract_validation_receipt"] == receipt
    assert receipt["schema_version"] == "qualibug.rule-contract-validation-binding.v1"
    assert receipt["invariants_derived"] >= 1
    assert receipt["bound_count"] + receipt["skipped_count"] == receipt["rules_scanned"]


def test_receipt_lists_every_skip_with_reason():
    ir = _ir_with_unbound_invariant(_synthetic_ir(), "rule:coupon:validity", "优惠券必须在有效期内")
    asset = _synthetic_asset()
    asset["rule_library"].append({
        "rule_id": "rule:vague:no_fields",
        "statement": "系统必须稳定运行",
        "semantic_frame": {},
        "confidence": 0.6,
        "source_id": "runtime:prd:synthetic",
    })
    ir["invariants"].append({
        "id": "bir_unbound_2",
        "description": "系统必须稳定运行",
        "expression": {"kind": "business_rule", "operator": "must_hold",
                       "operands": [], "raw": "系统必须稳定运行"},
        "operation_refs": [],
        "source_rule_refs": ["rule:vague:no_fields"],
    })
    _, receipt = m._derive_validation_invariants(ir, asset)
    skip = next(
        d for d in receipt["dispositions"] if d.get("rule_id") == "rule:vague:no_fields"
    )
    assert skip["disposition"] == "NO_ENTITY_FIELD_MATCH"
