"""Tests for the usage-limit (consumption-quota) replay treatment.

A rule constraining HOW MANY TIMES an object may be consumed (使用次数/限用 +
不能超过/限制/上限) is a quota: the violation is a REPLAYED consumption —
the same input applied again must not apply a new business effect. This is
measured on the TREATMENT (replay) window: a correct target observes 0 new
effects whether it refuses the replay (enforced quota) or accepts it as a
no-op, while a buggy target that applies the effect again observes 1.

Covers (a) the quota rekind: such rules bind to the CONSUMPTION operations
only and compile as idempotency, (b) the assertion contract
(expected_effect_count 0 on primary and secondary idempotency assertions),
and (c) the observer semantics: replay-accepted-noop → 0, replay-refused →
0, buggy replay → 1, replay-never-executed → INDETERMINATE (never a
violation). Synthetic data only.
"""

from __future__ import annotations

from ai_test_asset_center.assertion_dsl_base import evaluate_assertion
from ai_test_asset_center.behavior_ir_core import (
    build_behavior_ir_from_knowledge_asset,
)
from ai_test_asset_center.observer_contracts_base import (
    _observe_business_effect,
    observe_experiment_requirements,
)


_OPERATIONS = [
    {
        "operation_id": "op_coupons_validate",
        "method": "POST",
        "path": "/api/coupons/validate",
        "summary": "校验优惠券并试算优惠",
        "entity_refs": ["coupons"],
    },
    {
        "operation_id": "op_coupons_use",
        "method": "POST",
        "path": "/api/coupons/use",
        "summary": "使用优惠券",
        "entity_refs": ["coupons"],
        "request_example": {"couponCode": "NEW100", "orderId": "ORD-1"},
    },
    {
        "operation_id": "op_coupons_claim",
        "method": "POST",
        "path": "/api/coupons/claim",
        "summary": "领取优惠券",
        "entity_refs": ["coupons"],
    },
    {
        "operation_id": "op_coupons_health",
        "method": "GET",
        "path": "/api/coupons/health",
        "summary": "健康检查",
        "entity_refs": [],
    },
]


def _asset(statement: str) -> dict:
    return {
        "business_objects": [
            {"object": "coupon", "aliases": ["coupon", "voucher", "优惠券", "券码"]},
            {"object": "order", "aliases": ["order", "订单"]},
        ],
        "data_tables": [
            {
                "name": "coupons",
                "foreign_keys": [],
                "fields": [
                    {"name": "user_limit", "field_id": "cf_user_limit"},
                    {"name": "global_limit", "field_id": "cf_global_limit"},
                ],
            },
            {"name": "coupon_usage", "foreign_keys": ["coupons"]},
            {"name": "orders", "foreign_keys": []},
        ],
        "rule_library": [
            {"rule_id": "r-usage-limit", "statement": statement, "kind": "business_rule"},
        ],
    }


def _ir(statement: str, project_id: str, *, runtime_actors: list[dict] | None = None) -> dict:
    return build_behavior_ir_from_knowledge_asset(
        _asset(statement),
        api_operations=_OPERATIONS,
        runtime_actors=runtime_actors,
        project_id=project_id,
    )


def _ir_invariant(ir: dict, statement: str) -> dict:
    matches = [
        row for row in ir.get("invariants", [])
        if isinstance(row, dict) and row.get("description") == statement
    ]
    assert len(matches) == 1, f"expected one invariant for {statement!r}, got {len(matches)}"
    return matches[0]


def _bound_paths(ir: dict, inv: dict) -> list[str]:
    """Resolve the invariant's content-addressed op refs to their paths."""
    def _text(value: object) -> str:
        return str(value) if value is not None else ""

    path_by_id = {
        _text(op.get("id")): _text(op.get("path") or op.get("raw_path"))
        for op in ir.get("operations", [])
        if isinstance(op, dict) and _text(op.get("id"))
    }
    return sorted(
        path_by_id.get(_text(ref), _text(ref))
        for ref in inv.get("operation_refs", [])
        if _text(ref)
    )


# ── (a) quota rekind: consumption-op binding + idempotency family ──

def test_usage_limit_rule_rekinds_to_idempotency_and_binds_use_only():
    statement = "每张优惠券每个用户限用1次"
    ir = _ir(statement, "usage-limit-rekind")
    inv = _ir_invariant(ir, statement)
    assert inv["expression"]["kind"] == "idempotency"
    assert inv["expression"]["operator"] == "business_effect_count"
    operand = inv["expression"]["operands"][0]
    assert operand["expected_effect_count"] == 0
    # Bound to the consumption operation only — never the read-only
    # eligibility (validate) or claim surface the statement does not name.
    assert _bound_paths(ir, inv) == ["/api/coupons/use"]


def test_claim_quota_rule_binds_claim_only():
    statement = "每张优惠券每个用户限领1次"
    ir = _ir(statement, "usage-limit-claim")
    inv = _ir_invariant(ir, statement)
    assert inv["expression"]["kind"] == "idempotency"
    assert _bound_paths(ir, inv) == ["/api/coupons/claim"]


def test_usage_count_restriction_rule_rekinds():
    statement = "优惠券使用次数不能超过限制"
    ir = _ir(statement, "usage-limit-count")
    inv = _ir_invariant(ir, statement)
    assert inv["expression"]["kind"] == "idempotency"
    assert _bound_paths(ir, inv) == ["/api/coupons/use"]


def test_eligibility_rule_keeps_validation_family():
    # 必须满足 is not a quota restrictor: the rule stays a validation
    # contract on the decision surface and must not be rekinded.
    statement = "优惠券必须满足最低订单金额"
    ir = _ir(statement, "usage-limit-negative")
    inv = _ir_invariant(ir, statement)
    assert inv["expression"]["kind"] != "idempotency"


# ── (b) assertion contract: expected_effect_count 0 on both assertions ──

def test_idempotency_assertions_carry_replay_expected_zero():
    from ai_test_asset_center.experiment_compiler import compile_experiments
    from ai_test_asset_center.obligation_compiler import (
        compile_obligations_from_behavior_ir,
    )

    statement = "每张优惠券每个用户限用1次"
    ir = _ir(
        statement,
        "usage-limit-assertions",
        runtime_actors=[
            {
                "actor_ref": "user-1",
                "role": "buyer",
                "account_ref": "user-1@example.test",
            },
        ],
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    obligations = [
        row for row in compiled.get("obligations", [])
        if isinstance(row, dict) and row.get("risk_family") == "idempotency"
    ]
    assert obligations, "no idempotency obligation compiled"
    pack = compile_experiments(
        obligations,
        behavior_ir=ir,
        environment_type="dev",
        available_adapters={"http_api"},
    )
    experiments = [
        exp for exp in pack.get("experiments", [])
        if isinstance(exp, dict) and exp.get("risk_family") == "idempotency"
    ]
    assert experiments, "no idempotency experiment compiled"
    for exp in experiments:
        assertions = [a for a in exp.get("assertions", []) if isinstance(a, dict)]
        effect_assertions = [
            a for a in assertions
            if a.get("kind") in {"idempotency", "idempotency_effect"}
        ]
        assert effect_assertions, f"missing idempotency assertions in {exp.get('obligation_id')}"
        for assertion in effect_assertions:
            assert assertion.get("expected_effect_count") == 0, (
                f"{assertion.get('assertion_id')} expected_effect_count must be 0"
            )


# ── (c) observer semantics: replay window is the measurement ──

def _governance_step(phase: str, status: int, body: dict) -> dict:
    return {
        "phase": phase,
        "method": "POST",
        "governance_receipt": {
            "before": {"status_code": 200, "body": body.get("before")},
            "after": {"status_code": status, "body": body.get("after")},
            "write": {"status_code": status, "body": body.get("write")},
            "response_bound_after": {
                "status_code": status,
                "body": body.get("response_bound_after") or body.get("after"),
            },
        },
    }


def _replay_observation(steps: list[dict]) -> dict:
    receipt = _observe_business_effect(steps, require_treatment_window=True)
    evidence = dict(receipt.get("evidence") or {})
    evidence["_status"] = receipt.get("status")
    evidence["_reason"] = receipt.get("reason_code")
    return evidence


def _idempotency_spec(expected: int) -> dict:
    return {
        "assertion_id": "assert_idempotency_effect",
        "kind": "idempotency_effect",
        "expected_effect_count": expected,
        "property": {"template": "idempotency_effect"},
    }


def test_correct_noop_replay_observes_zero_and_passes():
    # Control creates the usage row; the replay is accepted as a no-op
    # (2xx, same state). Treatment window: 0 new effects.
    obs = _replay_observation([
        _governance_step("control", 201, {
            "before": {"rows": []},
            "after": {"rows": [{"id": "u1"}]},
        }),
        _governance_step("treatment", 200, {
            "before": {"rows": [{"id": "u1"}]},
            "after": {"rows": [{"id": "u1"}]},
        }),
    ])
    assert obs["effect_count"] == 0
    verdict = evaluate_assertion(_idempotency_spec(0), observations=obs)
    assert verdict["status"] == "PASS"


def test_enforced_quota_refused_replay_observes_zero_and_passes():
    # The quota refuses the replay (4xx). The refused write is a complete
    # zero-side-effect observation — never a violation.
    obs = _replay_observation([
        _governance_step("control", 201, {
            "before": {"rows": []},
            "after": {"rows": [{"id": "u1"}]},
        }),
        _governance_step("treatment", 400, {
            "before": {"rows": [{"id": "u1"}]},
            "after": {"rows": [{"id": "u1"}]},
            "write": {"status_code": 400, "body": {"error": "usage limit reached"}},
        }),
    ])
    assert obs["effect_count"] == 0
    verdict = evaluate_assertion(_idempotency_spec(0), observations=obs)
    assert verdict["status"] == "PASS"


def test_buggy_replay_creates_second_effect_and_violates():
    # The buggy target applies the effect again: a second usage row appears
    # in the treatment window.
    obs = _replay_observation([
        _governance_step("control", 201, {
            "before": {"rows": []},
            "after": {"rows": [{"id": "u1"}]},
        }),
        _governance_step("treatment", 201, {
            "before": {"rows": [{"id": "u1"}]},
            "after": {"rows": [{"id": "u1"}, {"id": "u2"}]},
        }),
    ])
    assert obs["effect_count"] == 1
    verdict = evaluate_assertion(_idempotency_spec(0), observations=obs)
    assert verdict["status"] == "VIOLATION"


def test_old_combined_window_semantics_would_false_positive_enforced_quota():
    # Regression pin for the semantic fix: the legacy aggregate window
    # collapses to 0 when the replay is refused, and the old expected=1
    # would report an enforced quota as a violation.
    obs = _replay_observation([
        _governance_step("control", 201, {
            "before": {"rows": []},
            "after": {"rows": [{"id": "u1"}]},
        }),
        _governance_step("treatment", 400, {
            "before": {"rows": [{"id": "u1"}]},
            "after": {"rows": [{"id": "u1"}]},
            "write": {"status_code": 400, "body": {"error": "usage limit reached"}},
        }),
    ])
    legacy_verdict = evaluate_assertion(_idempotency_spec(1), observations=obs)
    assert legacy_verdict["status"] == "VIOLATION"


def test_missing_treatment_window_is_indeterminate_never_violation():
    # The replay never executed: only the control window exists. Falling
    # back to the control window would report the control's own effect as
    # a violation — the guard makes the observation INDETERMINATE.
    obs = _replay_observation([
        _governance_step("control", 201, {
            "before": {"rows": []},
            "after": {"rows": [{"id": "u1"}]},
        }),
    ])
    assert obs["_status"] == "INDETERMINATE"
    assert obs["_reason"] == "BUSINESS_EFFECT_TREATMENT_WINDOW_MISSING"
    verdict = evaluate_assertion(_idempotency_spec(0), observations=obs)
    assert verdict["status"] == "INDETERMINATE"
