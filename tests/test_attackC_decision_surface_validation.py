"""Attack-C: coupon decision-surface validation chain tests.

Locks in the two root-cause fixes for the coupon FN class:

(a) obligation_compiler_base: an entity-eligibility validation rule (operands
    carry entity fields) on its entity's own DECISION input surface
    (validate/check/use/claim/simulate — read-like POST whose response IS the
    decision) must compile an obligation — the explicit-body-validation
    read-drop exists for body-schema rules (format/required/type) on reads
    that have no body to validate, and must not swallow eligibility rules on
    decision surfaces;
(b) experiment_protocols_base + executor: quota rules derive
    violation_mode=usage and the runtime resolver selects a row whose declared
    usage reached its limit, failing closed (None) when the environment
    exposes no usage data.

Synthetic assets only — no LLM, no benchmark material, no GT.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.experiment_plan_step_executor_core import (
    _resolve_runtime_violating_row_identity,
)
from ai_test_asset_center.experiment_protocols_base import (
    _entity_state_violation_mode,
)
from ai_test_asset_center.obligation_compiler_base import _decision_input_surface


# ── (a) decision-surface gate in the obligation compiler ──

def test_decision_input_surface_detects_read_like_decision_posts():
    assert _decision_input_surface({
        "method": "POST",
        "path": "/api/coupons/validate",
        "summary": "校验优惠券并试算优惠",
    })
    assert _decision_input_surface({
        "method": "POST",
        "path": "/api/coupons/use",
        "summary": "核销优惠券",
    })
    assert _decision_input_surface({
        "method": "POST",
        "path": "/api/coupons/simulate-discount",
        "summary": "优惠金额模拟计算",
    })
    # A GET list read is never a decision input surface.
    assert not _decision_input_surface({
        "method": "GET",
        "path": "/api/coupons",
        "summary": "查询可用优惠券",
    })
    # A write with no decision vocabulary is not a decision surface.
    assert not _decision_input_surface({
        "method": "POST",
        "path": "/api/coupons/admin/create",
        "summary": "创建优惠券",
    })


def _compile_ir_with_validation_invariant(*, operands, kind="validation"):
    """Build a minimal IR with one validation invariant bound to a coupon op."""
    from ai_test_asset_center.behavior_ir import empty_behavior_ir

    ir = empty_behavior_ir()
    ir["entities"] = [
        {
            "id": "ent_coupon",
            "name": "coupon",
            "table": "coupons",
            "fields": [
                {"name": "code", "field_id": "cf_code", "semantic_type": "IDENTITY"},
                {"name": "status", "field_id": "cf_status", "semantic_type": "STATE"},
                {"name": "min_order_amount", "field_id": "cf_min", "semantic_type": "AMOUNT"},
                {"name": "user_limit", "field_id": "cf_ul", "semantic_type": "QUANTITY"},
            ],
        },
    ]
    ir["operations"] = [
        {
            "id": "op_validate",
            "method": "POST",
            "path": "/api/coupons/validate",
            "raw_path": "/api/coupons/validate",
            "read_write": "read",
            "side_effect_class": "read",
            "summary": "校验优惠券并试算优惠",
            "entity_refs": ["coupon"],
            "request_schema": {
                "type": "object",
                "required": ["code"],
                "properties": {"code": {"type": "string", "example": "NEW100"}},
            },
            "request_example": {"code": "NEW100"},
        },
        {
            "id": "op_list",
            "method": "GET",
            "path": "/api/coupons",
            "raw_path": "/api/coupons",
            "read_write": "read",
            "summary": "查询可用优惠券",
            "entity_refs": [],
        },
        {
            "id": "op_health",
            "method": "GET",
            "path": "/api/coupons/health",
            "raw_path": "/api/coupons/health",
            "read_write": "read",
            "summary": "健康检查",
        },
    ]
    ir["relations"] = []
    ir["invariants"] = [
        {
            "id": "bir_eligibility",
            "description": "优惠券状态必须为 ACTIVE",
            "expression": {
                "kind": kind,
                "operator": "must_hold",
                "operands": operands,
                "raw": "优惠券状态必须为 ACTIVE",
            },
            "operation_refs": ["op_validate", "op_list"],
            "source_rule_refs": ["rule:coupon:status"],
            "subject_entity_refs": ["coupon"],
        },
    ]
    ir["coverage_gaps"] = []
    ir["actors"] = []
    return ir


def test_entity_eligibility_rule_on_decision_surface_compiles_obligation():
    """Fix (a): the read-drop must not swallow an entity-eligibility rule on
    its entity's own decision input surface (POST /api/coupons/validate)."""
    from ai_test_asset_center.obligation_compiler import (
        compile_obligations_from_behavior_ir,
    )

    ir = _compile_ir_with_validation_invariant(
        operands=[{"entity_ref": "ent_coupon", "field": "status"}],
    )
    result = compile_obligations_from_behavior_ir(ir)
    obligations = [
        row for row in result.get("obligations", [])
        if isinstance(row, dict)
        and row.get("risk_family") == "validation"
        and "op_validate" in (row.get("required_operations") or [])
    ]
    assert obligations, "entity-eligibility rule must compile an obligation on the decision surface"
    # The read-side list op is not a decision surface: no obligation there.
    list_obligations = [
        row for row in result.get("obligations", [])
        if isinstance(row, dict)
        and "op_list" in (row.get("required_operations") or [])
        and row.get("risk_family") == "validation"
    ]
    assert not list_obligations


def test_body_schema_rule_on_read_op_stays_dropped():
    """Regression guard: body-schema validation rules (no entity operands) on
    read ops must remain dropped — the original filter intent."""
    from ai_test_asset_center.obligation_compiler import (
        compile_obligations_from_behavior_ir,
    )

    ir = _compile_ir_with_validation_invariant(
        operands=[],
    )
    result = compile_obligations_from_behavior_ir(ir)
    obligations = [
        row for row in result.get("obligations", [])
        if isinstance(row, dict) and row.get("risk_family") == "validation"
    ]
    assert not obligations


# ── (b) usage violation mode ──

def test_usage_rule_derives_usage_violation_mode():
    assert _entity_state_violation_mode("用户使用次数不能超过限制") == "usage"
    assert _entity_state_violation_mode("每张优惠券每个用户限用1次") == "usage"
    # Status/expiry vocabulary keeps priority over the usage fallback.
    assert _entity_state_violation_mode("优惠券状态必须为 ACTIVE") == "status"
    assert _entity_state_violation_mode("优惠券必须在有效期内") == "expiry"
    assert _entity_state_violation_mode("价格不得小于 0") == "any"


def _usage_mutation(resolver_rows):
    return {
        "class": "runtime_entity_state_violation",
        "identity_field": "code",
        "status_field": "status",
        "violation_mode": "usage",
        "resolver_operations": [{"path": "/api/coupons", "method": "GET"}],
    }


class _FakeToken:
    pass


def _resolver_http(resolver_rows):
    """Return a resolver stub that answers the list read with the rows."""
    def _run(base_url, method, path, token):
        return {"status_code": 200, "body": resolver_rows}
    return _run


def test_usage_resolver_selects_exhausted_row(monkeypatch):
    rows = [
        {"code": "NEW100", "status": "ACTIVE", "user_limit": 1, "used": 0},
        {"code": "USED50", "status": "ACTIVE", "user_limit": 1, "used": 1},
    ]
    mutation = _usage_mutation(rows)
    # Run the resolver against the real function; monkeypatch the HTTP step.
    import ai_test_asset_center.experiment_plan_step_executor_core as ex

    monkeypatch.setattr(ex, "_run_http_step", _resolver_http(rows))
    identity = _resolve_runtime_violating_row_identity(
        mutation,
        base_url="http://localhost:1",
        actor={},
        tokens={"buyer": _FakeToken()},
    )
    assert identity == "USED50"


def test_usage_resolver_fails_closed_without_usage_data(monkeypatch):
    """No usage data in the environment → None (fail closed, never a
    fabricated violation or a fallback to a non-exhausted row)."""
    rows = [
        {"code": "NEW100", "status": "ACTIVE", "user_limit": 1},
        {"code": "DISABLED1", "status": "DISABLED"},
    ]
    mutation = _usage_mutation(rows)
    import ai_test_asset_center.experiment_plan_step_executor_core as ex

    monkeypatch.setattr(ex, "_run_http_step", _resolver_http(rows))
    identity = _resolve_runtime_violating_row_identity(
        mutation,
        base_url="http://localhost:1",
        actor={},
        tokens={"buyer": _FakeToken()},
    )
    assert identity is None
