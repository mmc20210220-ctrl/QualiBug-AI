"""Read-only state audit obligations from unbound Behavior IR invariants.

For invariants that have no operation binding (empty operation_refs, no
joined relations), this module maps the referenced entity type to a GET
endpoint and generates read-only audit obligations. These obligations
verify state machine validity and data consistency by reading entity state
via GET requests — no writes, no cleanup required.

This is the "option A" safe strategy: only read-only reachable invariant
verification. No write execution, no safety risk.
"""
from __future__ import annotations

import re
import logging
from typing import Any

from .test_obligation import make_obligation, stable_obligation_id


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# Entity type keywords → canonical entity names (industry-neutral)
_ENTITY_KEYWORDS: dict[str, str] = {
    # English
    "order": "order",
    "orders": "order",
    "payment": "payment",
    "payments": "payment",
    "refund": "refund",
    "refunds": "refund",
    "product": "product",
    "products": "product",
    "inventory": "inventory",
    "stock": "inventory",
    "cart": "cart",
    "carts": "cart",
    "coupon": "coupon",
    "coupons": "coupon",
    "user": "user",
    "users": "user",
    "address": "address",
    "addresses": "address",
    "report": "report",
    "reports": "report",
    # Chinese
    "订单": "order",
    "支付": "payment",
    "退款": "refund",
    "商品": "product",
    "库存": "inventory",
    "购物车": "cart",
    "优惠券": "coupon",
    "用户": "user",
    "地址": "address",
    "报表": "report",
}

# State-related keywords that indicate a state machine invariant
_STATE_KEYWORDS = frozenset({
    "state", "status", "transition", "状态", "流转",
    "cancelled", "paid", "shipped", "completed", "refunded",
    "pending", "created", "closed",
})

# Amount/conservation keywords
_AMOUNT_KEYWORDS = frozenset({
    "amount", "price", "qty", "quantity", "total", "discount",
    "payable", "balance", "金额", "数量", "库存",
})


def _extract_entity_type(invariant: dict[str, Any]) -> str:
    """Extract the entity type referenced by an invariant."""
    # Check expression operands first
    expr = _dict(invariant.get("expression"))
    operands = _list(expr.get("operands"))
    for operand in operands:
        if not isinstance(operand, dict):
            continue
        entity = _text(operand.get("entity")).lower()
        if entity in _ENTITY_KEYWORDS:
            return _ENTITY_KEYWORDS[entity]

    # Check description text
    desc = _text(invariant.get("description") or expr.get("description")).lower()
    for keyword, entity in _ENTITY_KEYWORDS.items():
        if keyword in desc:
            return entity

    # Check invariant ID
    inv_id = _text(invariant.get("id")).lower()
    for keyword, entity in _ENTITY_KEYWORDS.items():
        if keyword in inv_id:
            return entity

    return ""


def _evaluable_expression(expression: dict[str, Any]) -> bool:
    """Whether an invariant expression carries something a response can be checked against.

    Prose with an empty operand list is not evaluable. A structured condition, an
    equation, or at least one operand is.
    """
    expr = _dict(expression)
    if _list(expr.get("operands")):
        return True
    for key in ("structured_expression", "condition", "equation", "predicate"):
        if expr.get(key):
            return True
    return False


def _classify_invariant_family(invariant: dict[str, Any]) -> str:
    """Classify an invariant into a risk family for read-only audit."""
    expr = _dict(invariant.get("expression"))
    kind = _text(expr.get("kind")).lower()
    desc = _text(invariant.get("description") or expr.get("description")).lower()
    combined = f"{kind} {desc}"

    if any(kw in combined for kw in _STATE_KEYWORDS):
        return "state"
    if any(kw in combined for kw in _AMOUNT_KEYWORDS):
        return "conservation"
    return "validation"


def _find_get_endpoint_for_entity(
    entity: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find a GET endpoint that reads the given entity type.

    Prefers detail endpoints (GET /entity/{id}) over list endpoints.
    """
    entity_lower = entity.lower()
    # Also match plural forms
    entity_plural = entity_lower + "s" if not entity_lower.endswith("s") else entity_lower

    detail_candidates: list[dict[str, Any]] = []
    list_candidates: list[dict[str, Any]] = []

    for op in operations:
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method")).upper()
        if method not in {"GET", "HEAD"}:
            continue
        path = _text(op.get("path") or op.get("raw_path")).lower()
        op_id = _text(op.get("id"))
        if not op_id:
            continue

        # Check if path contains the entity name
        path_tokens = re.split(r"[/\s]+", path)
        matched = False
        for token in path_tokens:
            clean = token.strip("{}:").lower()
            if clean in {entity_lower, entity_plural}:
                matched = True
                break
        if not matched:
            # Also check operation ID
            if entity_lower in op_id.lower() or entity_plural in op_id.lower():
                matched = True

        if not matched:
            continue

        # Classify as detail or list endpoint
        has_placeholder = "{" in path or ":" in path.split("?")[0]
        if has_placeholder:
            detail_candidates.append(op)
        else:
            list_candidates.append(op)

    # Prefer detail endpoints (more specific state check)
    if detail_candidates:
        return detail_candidates[0]
    if list_candidates:
        return list_candidates[0]
    return None


def build_readonly_state_audit_obligations(
    behavior_ir: dict[str, Any],
    *,
    max_obligations: int = 50,
) -> list[dict[str, Any]]:
    """Generate read-only state audit obligations from unbound invariants.

    For each invariant that has no operation binding:
    1. Extract the entity type
    2. Find a GET endpoint for that entity
    3. Create a read-only audit obligation

    These obligations are safe: they only read state, never write.
    """
    ir = _dict(behavior_ir)
    invariants = _list(ir.get("invariants"))
    operations = _list(ir.get("operations"))
    relations = _list(ir.get("relations"))
    actors = _list(ir.get("actors"))

    # Build operation ID set for quick lookup
    operations_by_id = {
        _text(op.get("id")): op
        for op in operations
        if isinstance(op, dict) and _text(op.get("id"))
    }

    # Build set of invariants that already have operation bindings
    bound_invariant_refs: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        rel_type = _text(relation.get("relation_type"))
        if rel_type in {"observes", "conserves", "transitions", "produces", "consumes"}:
            from_ref = _text(relation.get("from_ref"))
            to_ref = _text(relation.get("to_ref"))
            op_ref = _text(relation.get("operation_ref"))
            if op_ref and op_ref in operations_by_id:
                bound_invariant_refs.add(from_ref)
                bound_invariant_refs.add(to_ref)

    # Find executable actors (prefer admin for read-only audit)
    # Note: We don't set required_actors here because behavior IR actors
    # may have unresolvable secret_ref:actor:* placeholders. The experiment
    # compiler will auto-assign actors at compile time.
    executable_actors = [
        op for op in actors
        if isinstance(op, dict)
        and _text(op.get("credential_secret_ref") or op.get("secret_ref"))
    ]
    admin_actor = next(
        (a for a in executable_actors
         if _text(a.get("role")).lower() in {"admin", "administrator", "superuser"}),
        None,
    )
    audit_actor = admin_actor or (executable_actors[0] if executable_actors else None)
    audit_actor_id = _text(audit_actor.get("id")) if audit_actor else ""

    obligations: list[dict[str, Any]] = []
    # Invariants whose expression cannot be evaluated against a response. Collected so
    # the skip is visible: silently emitting fewer audits reads the same as there being
    # nothing to audit.
    skipped_unevaluable: list[dict[str, Any]] = []
    seen_entities: set[str] = set()

    for inv in invariants:
        if not isinstance(inv, dict):
            continue
        inv_id = _text(inv.get("id"))
        if not inv_id:
            continue

        # Skip invariants that already have operation bindings
        if inv_id in bound_invariant_refs:
            continue

        # Check operation_refs field
        op_refs = [_text(r) for r in _list(inv.get("operation_refs")) if _text(r)]
        if op_refs and any(r in operations_by_id for r in op_refs):
            continue

        # Extract entity type
        entity = _extract_entity_type(inv)
        if not entity:
            continue

        # Find GET endpoint for this entity
        get_op = _find_get_endpoint_for_entity(entity, operations)
        if not get_op:
            continue

        get_op_id = _text(get_op.get("id"))
        if not get_op_id or get_op_id not in operations_by_id:
            continue

        # Classify the invariant family
        family = _classify_invariant_family(inv)

        # Build the audit obligation
        expr = _dict(inv.get("expression"))

        # A GET can observe a current state, but it cannot establish or exercise
        # a state transition.  Mapping an unbound forbidden/allowed transition
        # to a guessed GET endpoint therefore creates a state-transition
        # obligation with no causal operation and no valid before/after
        # precondition.  Keep the source invariant as a visible coverage gap;
        # do not turn entity-name similarity into an executable experiment.
        expression_kind = _text(expr.get("kind")).lower()
        if family == "state" or expression_kind in {
            "state_transition",
            "forbidden_state_transition",
        }:
            skipped_unevaluable.append({
                "invariant_ref": inv_id,
                "reason_code": "AUDIT_REQUIRES_EXACT_TRANSITION_OPERATION",
                "expression_kind": expression_kind,
                "statement": _text(inv.get("description") or expr.get("raw"))[:160],
            })
            continue

        # An audit reads the entity and checks the invariant against what came back.
        # That needs an evaluable condition. These invariants arrive as prose with
        # ``operands: []`` -- 「同一订单不能重复成功退款」 has nothing a response can be
        # compared against -- and with no condition to evaluate the compiler fell back
        # to a validation_rejection assertion expecting HTTP 4xx. That asserts the READ
        # must be refused, which is meaningless for an audit, so every successful read
        # became a defect. Both remaining published findings on a live target were this.
        #
        # Emitting nothing is the honest outcome: an audit that cannot evaluate its own
        # condition can only produce a fabricated verdict.
        if not _evaluable_expression(expr):
            skipped_unevaluable.append({
                "invariant_ref": inv_id,
                "reason_code": "AUDIT_EXPRESSION_NOT_EVALUABLE",
                "expression_kind": _text(expr.get("kind")),
                "statement": _text(inv.get("description") or expr.get("raw"))[:160],
            })
            continue

        property_spec = {
            "template": f"readonly_audit_{family}",
            "invariant_ref": inv_id,
            "expression": expr,
            "operation_ref": get_op_id,
            "entity_type": entity,
            "audit_mode": "read_only",
            "description": _text(inv.get("description") or expr.get("description")),
        }

        # Create source refs for traceability
        source_refs = _list(inv.get("source_refs"))
        source_refs.append({
            "source_id": "state_audit_planner",
            "kind": "derived_readonly_audit",
            "locator": f"GET {get_op.get('path', '')}",
        })

        obl = make_obligation(
            risk_family=family,
            subject_refs=[inv_id, get_op_id],
            property_spec=property_spec,
            required_actors=[],  # Let experiment compiler auto-assign
            required_operations=[get_op_id],
            required_observers=["http_response"],
            cleanup_requirement={"required": False},
            source_refs=source_refs,
            confidence=min(float(inv.get("confidence") or 0.6), 0.7),
            obligation_id=stable_obligation_id(
                "readonly_audit",
                family,
                inv_id,
                get_op_id,
            ),
        )
        obligations.append(obl)
        seen_entities.add(entity)

        if len(obligations) >= max_obligations:
            break

    if skipped_unevaluable:
        logging.getLogger(__name__).info(
            "state_audit_planner: %d invariant(s) skipped as non-evaluable for read-only "
            "audit (%s)",
            len(skipped_unevaluable),
            ", ".join(
                sorted({_text(row.get("expression_kind")) or "unknown" for row in skipped_unevaluable})
            ),
        )
    return obligations
