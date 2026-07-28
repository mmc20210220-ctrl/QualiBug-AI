"""Prose invariants must be joined to the operations they constrain.

A rule extracted from a requirements document never names an endpoint, and
``invariant.operation_refs`` was populated only from the rule's own operation
reference. So the refs stayed empty: measured on a live 11-service target, 107 of 111
invariants were unbound and ``MISSING_PRIMARY_OPERATION`` was the single largest
terminal reason at 174 of 435 obligations. The product read the business rules and
could not reach any of them.

The binder closes that join. Measured on the same target's real IR, compiling
obligations before and after: invariants with refs 6 -> 95, obligations 89 -> 598,
and four risk families that had produced literally zero obligations -- concurrency,
conservation, idempotency, visibility -- began producing them.

These tests exist mostly to hold the line on the opposite risk. A binder that guesses
attaches assertions to endpoints they do not govern, and a failed assertion on a
wrongly-bound operation is a fabricated defect reported against innocent code. Every
test below either proves a real join happens or proves a plausible-looking one is
refused.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.invariant_operation_binder import (
    BINDER_SCHEMA,
    apply_invariant_operation_bindings,
    bind_invariants_to_operations,
    operation_actions,
    operation_tokens,
)

LEXICON = {
    "entity_token_lexicon": {
        "订单": ["order"], "支付": ["payment"], "退款": ["refund"],
        "库存": ["inventory"], "优惠券": ["coupon"],
    },
    "verb_action_lexicon": {
        "取消": ["cancel"], "支付": ["pay"], "发货": ["ship"], "确认": ["confirm"],
        "审批": ["approve"], "退款": ["refund"], "下单": ["create"], "新建": ["create"],
        "cancel": ["cancel"], "confirm": ["confirm"], "approve": ["approve"],
    },
}


def _ir(invariants, operations, entities=(), states=()):
    return {
        "invariants": list(invariants),
        "operations": list(operations),
        "entities": [{"name": n} for n in entities],
        "states": [{"name": s} for s in states],
        "coverage_gaps": [],
    }


def _op(method, path, params=()):
    return {
        "id": f"bir_{method}_{path}".replace("/", "_"),
        "operation_id": f"{method.lower()}{path.replace('/', '_')}",
        "method": method,
        "path": path,
        "parameters": list(params),
    }


def _inv(node_id, description):
    return {"id": node_id, "description": description, "operation_refs": []}


# ── action resolution ───────────────────────────────────────────────────────

def test_path_verb_beats_the_http_method() -> None:
    """POST /orders/:id/cancel is a cancel, not a create.

    Reading it as a create binds every "must not be cancelled" rule to the wrong
    endpoint, which is worse than not binding at all.
    """
    verbs = LEXICON["verb_action_lexicon"]
    assert operation_actions(_op("POST", "/api/orders/:id/cancel"), verbs) == {"cancel"}
    assert operation_actions(_op("POST", "/api/orders"), verbs) == {"create"}


def test_short_canonical_verbs_resolve_from_the_path() -> None:
    """"pay" and "ship" are absent from the lexicon keys on purpose.

    Its other consumers match by substring, where "ship" hits "relationship" and "use"
    hits "user". A path segment is compared for equality, so the canonical action
    vocabulary is matched here directly -- without it POST /api/payments/pay resolves
    to "create" and every payment rule binds to the wrong endpoint.
    """
    verbs = LEXICON["verb_action_lexicon"]
    assert operation_actions(_op("POST", "/api/payments/pay"), verbs) == {"pay"}
    assert operation_actions(_op("POST", "/api/orders/:id/ship"), verbs) == {"ship"}


def test_method_default_applies_when_no_verb_is_present() -> None:
    verbs = LEXICON["verb_action_lexicon"]
    assert operation_actions(_op("GET", "/api/orders"), verbs) == {"read"}
    assert operation_actions(_op("PATCH", "/api/cart/items/:id"), verbs) == {"update"}
    assert operation_actions(_op("DELETE", "/api/products/:sku"), verbs) == {"delete"}


# ── entity extraction ───────────────────────────────────────────────────────

def test_path_entities_are_singularised() -> None:
    tokens = operation_tokens(_op("GET", "/api/orders"))
    assert "order" in tokens["entities"]


def test_api_and_version_segments_are_not_entities() -> None:
    tokens = operation_tokens(_op("GET", "/api/v1/orders"))
    assert tokens["entities"] == {"order"}


def test_referenced_entities_come_from_declared_fields() -> None:
    """orderId on a payment operation is the only declaration that the payment
    relates to an order. Without it "已取消订单不能支付" cannot reach /payments/pay."""
    tokens = operation_tokens(_op("POST", "/api/payments/pay", ["orderId", "amount"]))
    assert "payment" in tokens["entities"]
    assert "order" in tokens["field_entities"]
    assert "order" not in tokens["entities"], "a referenced entity is not the subject"


# ── real joins ──────────────────────────────────────────────────────────────

def test_cancelled_order_rule_reaches_every_operation_it_forbids() -> None:
    """The motivating case, end to end."""
    ir = _ir(
        [_inv("inv1", "已取消订单不能支付、发货、确认收货")],
        [
            _op("POST", "/api/orders/:id/cancel"),
            _op("POST", "/api/orders/:id/ship"),
            _op("POST", "/api/orders/:id/confirm"),
            _op("POST", "/api/payments/pay", ["orderId", "amount"]),
            _op("GET", "/api/products"),
        ],
    )
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    assert result["counts"]["bound"] == 1
    paths = {m["path"] for m in result["bindings"][0]["operations"]}
    assert "/api/orders/:id/ship" in paths
    assert "/api/orders/:id/confirm" in paths
    assert "/api/payments/pay" in paths
    assert "/api/products" not in paths, "an unrelated read must not be bound"


def test_every_binding_states_the_tokens_that_produced_it() -> None:
    """A binding with no stated basis is a guess wearing a receipt."""
    ir = _ir(
        [_inv("inv1", "财务或管理员审批退款")],
        [_op("POST", "/api/refunds/:id/approve"), _op("GET", "/api/products")],
    )
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    match = result["bindings"][0]["operations"][0]
    assert match["basis"] == "entity_and_action"
    assert match["matched_tokens"]["entities"] == ["refund"]
    assert match["matched_tokens"]["actions"] == ["approve"]
    assert 0 < match["confidence"] <= 1


# ── refusals: the part that prevents fabricated defects ─────────────────────

def test_one_shared_entity_is_not_a_binding() -> None:
    """Entity alone would bind every order rule to every order endpoint."""
    ir = _ir(
        [_inv("inv1", "订单信息应当完整")],
        [_op("GET", "/api/orders"), _op("POST", "/api/orders")],
    )
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    assert result["counts"]["bound"] == 0
    assert result["unbound"][0]["reason_code"] == "INVARIANT_NO_MATCHING_OPERATION"


def test_umbrella_statements_are_refused() -> None:
    """"系统应保证权限隔离、状态一致性、金额准确性、库存准确性" is a summary.

    Binding it to every write it mentions attaches an unfalsifiable assertion to real
    traffic, which fails on something and reports a defect that was never specified.
    """
    ir = _ir(
        [_inv("inv1", "系统应保证订单、库存、优惠券的一致性")],
        [_op("POST", "/api/orders"), _op("POST", "/api/payments/pay")],
    )
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    assert result["counts"]["bound"] == 0
    assert result["unbound"][0]["reason_code"] == "INVARIANT_STATEMENT_NOT_OPERATION_SPECIFIC"


def test_a_generic_method_action_cannot_carry_a_referenced_entity_join() -> None:
    """The real false binding this rule was added for.

    On the live target "并发下单不得超卖" bound to POST /api/refunds: the rule says
    create, POST defaults
    to create, and refunds carry an orderId. Every POST that references the entity
    would match. Only a verb the path actually declares may satisfy the weaker rule.
    """
    ir = _ir(
        [_inv("inv1", "新建订单时不得超卖")],
        [
            _op("POST", "/api/orders", ["items"]),
            _op("POST", "/api/refunds", ["orderId", "amount"]),
        ],
    )
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    paths = {m["path"] for m in result["bindings"][0]["operations"]}
    assert "/api/orders" in paths
    assert "/api/refunds" not in paths


def test_an_invariant_already_bound_at_source_is_left_alone() -> None:
    """A declared ref is authoritative; second-guessing it would override the source."""
    ir = _ir(
        [{"id": "inv1", "description": "已取消订单不能支付", "operation_refs": ["bir_declared"]}],
        [_op("POST", "/api/payments/pay", ["orderId"])],
    )
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    assert result["counts"]["bound"] == 0
    apply_invariant_operation_bindings(ir, result)
    assert ir["invariants"][0]["operation_refs"] == ["bir_declared"]


def test_empty_statement_is_reported_not_bound() -> None:
    ir = _ir([_inv("inv1", "")], [_op("POST", "/api/orders")])
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    assert result["unbound"][0]["reason_code"] == "INVARIANT_STATEMENT_EMPTY"


def test_overflow_is_reported_never_silently_capped() -> None:
    """A truncated binding set reads as "this is all of them"."""
    ops = [_op("POST", f"/api/orders/:id/cancel{i}") for i in range(12)]
    for op in ops:
        op["path"] = op["path"].replace(f"cancel{ops.index(op)}", "cancel")
        op["id"] = f"bir_op_{ops.index(op)}"
    ir = _ir([_inv("inv1", "已取消订单不能取消")], ops)
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    entry = result["bindings"][0]
    assert len(entry["operations"]) <= 8
    assert entry["truncated_match_count"] == 12 - 8
    assert "dropped" in entry["truncation_note"]


# ── applying the join ───────────────────────────────────────────────────────

def test_apply_writes_refs_and_a_derivation_marker() -> None:
    """A derived binding must be distinguishable from a source-declared one."""
    ir = _ir(
        [_inv("inv1", "财务或管理员审批退款")],
        [_op("POST", "/api/refunds/:id/approve")],
    )
    receipt = apply_invariant_operation_bindings(ir)
    invariant = ir["invariants"][0]
    assert invariant["operation_refs"]
    basis = invariant["operation_binding_basis"]
    assert basis["derivation"] == "derived_by_invariant_operation_binder"
    assert basis["schema_version"] == BINDER_SCHEMA
    assert receipt["invariants_bound"] == 1


def test_low_confidence_matches_are_excluded_by_the_threshold() -> None:
    ir = _ir(
        [_inv("inv1", "订单必须处于 PENDING_PAYMENT 状态")],
        [_op("POST", "/api/orders/:id/cancel")],
        states=["PENDING_PAYMENT"],
    )
    result = bind_invariants_to_operations(ir, lexicon=LEXICON)
    assert result["bindings"][0]["operations"][0]["basis"] == "entity_and_state_write"

    apply_invariant_operation_bindings(ir, result, min_confidence=0.9)
    assert not ir["invariants"][0].get("operation_refs"), "0.6 match must not pass a 0.9 bar"


def test_stale_unbound_gaps_are_pruned_for_newly_bound_invariants() -> None:
    """The gap was written before the join ran.

    Leaving it makes the result claim an invariant is unreachable while carrying its
    operation_refs, and the release gate counts these.
    """
    ir = _ir(
        [_inv("inv1", "财务或管理员审批退款")],
        [_op("POST", "/api/refunds/:id/approve")],
    )
    ir["coverage_gaps"] = [
        {"reason_code": "SOURCE_INVARIANT_OPERATION_UNBOUND", "invariant_ref": "inv1"},
        {"reason_code": "SOURCE_INVARIANT_OPERATION_UNBOUND", "invariant_ref": "inv_other"},
        {"reason_code": "TEST_DATA_GAP"},
    ]
    receipt = apply_invariant_operation_bindings(ir)
    assert receipt["stale_unbound_gaps_pruned"] == 1
    remaining = {g.get("invariant_ref") for g in ir["coverage_gaps"]}
    assert "inv1" not in remaining
    assert "inv_other" in remaining, "an invariant that stayed unbound keeps its gap"
    assert any(g.get("reason_code") == "TEST_DATA_GAP" for g in ir["coverage_gaps"])


def test_heuristic_binder_is_not_wired_into_the_product_mainline() -> None:
    """Only exact source or agent-semantic identities may join rules to operations."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "discovery_runtime_planning.py"
    ).read_text(encoding="utf-8")
    assert "bind_invariants_to_operations(behavior_ir)" not in source
    assert "apply_invariant_operation_bindings(" not in source
    assert "agent_semantic_link_receipt" in source
