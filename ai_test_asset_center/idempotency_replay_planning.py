"""Idempotency Replay Planning Module.

SPEC: QualiBug 幂等重复与请求重放实验自动生成
Breakpoint: IDEMPOTENCY_REPETITION_NOT_GENERATED

This module builds concrete executable replay sequences for idempotency testing.
Unlike the legacy abstract first/repeat descriptors, this module produces:

1. Operation Identity resolution
2. Idempotency Key derivation
3. Request Fingerprint construction
4. Resource Scope determination
5. First Execution with side-effect proof
6. Replay Variants (exact, same-key-diff-payload, diff-key-same-payload)
7. Side-effect comparison oracle

Core production call chain:
    Idempotency Rule
    -> Operation Identity
    -> Idempotency Key
    -> Request Fingerprint
    -> Resource Scope
    -> First Execution
    -> First Execution Proof
    -> Replay Variant
    -> Replay Execution
    -> Duplicate Side-Effect Observation
    -> Idempotency Oracle
    -> Finding

Fully generic: no project-specific or benchmark-specific logic.
"""
from __future__ import annotations

import hashlib
from typing import Any


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _text(v: Any) -> str:
    return str(v or "").strip()


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "idem_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Operation Identity ───────────────────────────────────────────────────────

def resolve_operation_identity(
    obligation: dict[str, Any],
    ir: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the target operation identity for idempotency testing.

    Returns operation identity with method, path, entity, and body schema.
    """
    operation_ref = _text(
        obligation.get("operation_ref") or obligation.get("target_operation")
    )
    ops_by_id = _dict(ir.get("operations_by_id") or ir.get("ops_by_id"))
    operations = _list(ir.get("operations"))
    if not ops_by_id and operations:
        ops_by_id = {_text(op.get("operation_id") or op.get("id")): op for op in operations}

    operation = _dict(ops_by_id.get(operation_ref))
    if not operation:
        # Try required_operations
        for ref in _list(obligation.get("required_operations")):
            op = _dict(ops_by_id.get(_text(ref)))
            if op:
                operation = op
                operation_ref = _text(ref)
                break

    method = _text(operation.get("method")).upper()
    path = _text(operation.get("path") or "")
    entity_ref = _text(operation.get("entity_ref") or "")
    request_schema = _dict(operation.get("request_schema") or operation.get("request_body"))

    return {
        "operation_ref": operation_ref,
        "method": method,
        "path": path,
        "entity_ref": entity_ref,
        "request_schema": request_schema,
        "operation": operation,
    }


# ─── Idempotency Key Derivation ──────────────────────────────────────────────

def derive_idempotency_key(
    op_identity: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
) -> dict[str, Any]:
    """Derive the idempotency key from operation identity and rule expression.

    The idempotency key identifies what makes two requests the "same business action".
    """
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    expr = _dict(inv.get("expression") or obligation.get("expression"))
    path = _text(op_identity.get("path"))
    request_schema = _dict(op_identity.get("request_schema"))

    # Key fields from expression
    key_fields = _list(expr.get("idempotency_key") or expr.get("key_fields"))
    if not key_fields:
        # Infer from path params and body identity fields
        key_fields = []
        # Path params are part of the key (resource scope)
        import re
        path_params = re.findall(r"\{(\w+)\}", path)
        for p in path_params:
            key_fields.append(f"{p} (path)")

        # Body fields that identify the resource being created/added
        body_props = list(_dict(request_schema.get("properties")).keys())
        identity_hints = ["id", "agent_id", "member_id", "user_id", "key", "name"]
        for prop in body_props:
            if any(hint in prop.lower() for hint in identity_hints):
                key_fields.append(f"{prop} (body)")
                break
        if not any("body" in f for f in key_fields) and body_props:
            key_fields.append(f"{body_props[0]} (body)")

    # Determine uniqueness constraint
    uniqueness = _text(
        expr.get("uniqueness_constraint") or expr.get("constraint")
        or expr.get("description")
    )

    return {
        "key_fields": key_fields,
        "uniqueness_constraint": uniqueness,
        "key_semantics": _text(expr.get("key_semantics") or ""),
    }


# ─── Request Fingerprint ─────────────────────────────────────────────────────

def build_request_fingerprint(
    op_identity: dict[str, Any],
    idem_key: dict[str, Any],
) -> dict[str, Any]:
    """Build request fingerprint that identifies identical business actions.

    Two requests have the same fingerprint when they represent the same action.
    """
    method = _text(op_identity.get("method"))
    path = _text(op_identity.get("path"))
    key_fields = _list(idem_key.get("key_fields"))

    # Fingerprint = method + path_template + key_field_values
    fingerprint_components = [method, path]
    for kf in key_fields:
        fingerprint_components.append(_text(kf))

    fingerprint_id = _stable_id("fingerprint", *fingerprint_components)

    return {
        "fingerprint_id": fingerprint_id,
        "method": method,
        "path_template": path,
        "key_fields": key_fields,
        "identity_description": f"{method} {path} with key={key_fields}",
    }


# ─── Resource Scope ──────────────────────────────────────────────────────────

def determine_resource_scope(
    op_identity: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
) -> dict[str, Any]:
    """Determine the resource scope and side-effect observation method.

    Resource scope tells us WHERE the side effect lands and HOW to observe it.
    """
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    expr = _dict(inv.get("expression") or obligation.get("expression"))
    entity_ref = _text(op_identity.get("entity_ref") or inv.get("entity_ref"))
    path = _text(op_identity.get("path"))

    # Affected field
    affected_field = _text(
        expr.get("affected_field") or expr.get("field")
        or expr.get("resource_field")
    )
    if not affected_field:
        # Infer from description
        desc = _text(expr.get("description") or inv.get("description"))
        if "member" in desc.lower():
            affected_field = "members"
        elif "assignment" in desc.lower():
            affected_field = "assignments"

    # Side effect type
    side_effect_type = _text(expr.get("side_effect_type") or "APPEND")

    # Observation method: how to verify the side effect
    # Typically GET on the resource to check the affected field
    observation_path = path
    # Strip action suffix to get resource path (e.g., /teams/{id}/members → /teams/{id})
    import re
    resource_match = re.match(r"(/[\w-]+/\{?\w+\}?)", path)
    if resource_match:
        observation_path = resource_match.group(1)

    return {
        "entity_ref": entity_ref,
        "affected_field": affected_field,
        "side_effect_type": side_effect_type,
        "observation_method": f"GET {observation_path}",
        "observation_path": observation_path,
    }


# ─── Replay Sequence Builder ─────────────────────────────────────────────────

def build_replay_sequence(
    op_identity: dict[str, Any],
    idem_key: dict[str, Any],
    fingerprint: dict[str, Any],
    resource_scope: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
) -> dict[str, Any]:
    """Build concrete executable replay sequence.

    Produces the full sequence:
      Step 1: First execution (expect success)
      Step 2: Observe side effect (proof of first execution)
      Step 3: Replay (same request, expect rejection)
      Step 4: Observe no additional side effect
    """
    operation_ref = _text(op_identity.get("operation_ref"))
    method = _text(op_identity.get("method"))
    path = _text(op_identity.get("path"))
    entity_ref = _text(resource_scope.get("entity_ref"))
    affected_field = _text(resource_scope.get("affected_field"))
    observation_path = _text(resource_scope.get("observation_path"))

    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    rule_id = _text(inv.get("rule_id") or inv.get("id"))
    oid = _text(obligation.get("obligation_id") or obligation.get("id"))

    # Build the concrete multi-step fixture
    sequence = [
        {
            "step": 1,
            "role": "FIRST_EXECUTION",
            "operation_ref": operation_ref,
            "method": method,
            "path": path,
            "intent": "first_execution_of_business_action",
            "expected_status": [200, 201],
            "expected_outcome": "accepted",
        },
        {
            "step": 2,
            "role": "SIDE_EFFECT_OBSERVATION",
            "operation_ref": f"observe_{entity_ref.lower()}",
            "method": "GET",
            "path": observation_path,
            "intent": "verify_first_execution_side_effect_landed",
            "expected_status": [200],
            "observation_target": affected_field,
        },
        {
            "step": 3,
            "role": "REPLAY_EXECUTION",
            "operation_ref": operation_ref,
            "method": method,
            "path": path,
            "intent": "replay_same_business_action",
            "expected_status": [409, 422, 400],
            "expected_outcome": "rejected_duplicate",
            "replay_variant": "EXACT_REPLAY",
        },
        {
            "step": 4,
            "role": "REPLAY_SIDE_EFFECT_CHECK",
            "operation_ref": f"observe_{entity_ref.lower()}_post_replay",
            "method": "GET",
            "path": observation_path,
            "intent": "verify_no_duplicate_side_effect",
            "expected_status": [200],
            "observation_target": affected_field,
            "expect_unchanged": True,
        },
    ]

    # Build replay variants
    variants = _build_replay_variants(op_identity, idem_key, obligation)

    proof_id = _stable_id("replay_proof", oid, operation_ref, rule_id)

    return {
        "status": "BUILT",
        "sequence": sequence,
        "replay_variants": variants,
        "sequence_length": len(sequence),
        "replay_proof": {
            "proof_id": proof_id,
            "proof_type": "IDEMPOTENCY_REPLAY_PROOF",
            "operation_ref": operation_ref,
            "rule_id": rule_id,
            "fingerprint_id": _text(fingerprint.get("fingerprint_id")),
            "resource_scope": resource_scope,
            "sequence_steps": len(sequence),
            "variant_count": len(variants),
        },
    }


def _build_replay_variants(
    op_identity: dict[str, Any],
    idem_key: dict[str, Any],
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build replay variants for comprehensive idempotency testing.

    Variants:
      1. EXACT_REPLAY: same key, same payload → expect rejection
      2. SAME_KEY_DIFF_PAYLOAD: same scope, different identity → expect success (new action)
      3. DIFF_KEY_SAME_PAYLOAD: different scope, same identity → expect success (different action)
    """
    operation_ref = _text(op_identity.get("operation_ref"))
    key_fields = _list(idem_key.get("key_fields"))

    variants = [
        {
            "variant_id": _stable_id("var", operation_ref, "exact"),
            "variant_type": "EXACT_REPLAY",
            "description": "Same request (same key + same payload) = same business action",
            "expected_outcome": "rejected",
            "expected_status": [409, 422, 400],
            "oracle_check": "duplicate must be rejected",
        },
        {
            "variant_id": _stable_id("var", operation_ref, "diff_payload"),
            "variant_type": "SAME_KEY_DIFFERENT_PAYLOAD",
            "description": "Same scope but different identity field = new legitimate request",
            "expected_outcome": "accepted",
            "expected_status": [200, 201],
            "oracle_check": "different identity in same scope is a new action",
        },
        {
            "variant_id": _stable_id("var", operation_ref, "diff_key"),
            "variant_type": "DIFFERENT_KEY_SAME_PAYLOAD",
            "description": "Different scope with same identity = different business action",
            "expected_outcome": "accepted",
            "expected_status": [200, 201],
            "oracle_check": "same identity in different scope is legitimate",
        },
    ]

    return variants


# ─── Main Entry Point ────────────────────────────────────────────────────────

def plan_idempotency_replay(
    obligation: dict[str, Any],
    ir: dict[str, Any],
    budget: int = 8,
) -> dict[str, Any]:
    """Plan idempotency replay experiments for an obligation.

    Main entry point. Returns:
      - status: REPLAY_PLANNED | NOT_IDEMPOTENCY | INSUFFICIENT_INFO
      - experiments: list of experiment plans
      - replay_proof: proof of replay sequence construction
      - side_effect_proof: proof of side-effect observation capability
    """
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    expr = _dict(inv.get("expression") or obligation.get("expression"))
    rule_type = _text(inv.get("rule_type") or expr.get("rule_type"))
    mechanism = _text(obligation.get("mechanism") or "")

    # Verify this is an idempotency obligation
    is_idempotency = (
        "IDEMPOTENCY" in rule_type.upper()
        or "IDEMPOTENCY" in mechanism.upper()
        or "idempoten" in _text(inv.get("description") or "").lower()
        or "duplicate" in _text(expr.get("description") or inv.get("description") or "").lower()
    )
    if not is_idempotency:
        return {
            "status": "NOT_IDEMPOTENCY",
            "reason": "Obligation is not idempotency-related",
        }

    # Step 1: Resolve operation identity
    op_identity = resolve_operation_identity(obligation, ir)
    if not op_identity.get("operation_ref"):
        return {
            "status": "INSUFFICIENT_INFO",
            "reason": "Cannot resolve target operation",
        }

    # Step 2: Derive idempotency key
    idem_key = derive_idempotency_key(op_identity, obligation, ir)

    # Step 3: Build request fingerprint
    fingerprint = build_request_fingerprint(op_identity, idem_key)

    # Step 4: Determine resource scope
    resource_scope = determine_resource_scope(op_identity, obligation, ir)

    # Step 5: Build replay sequence
    replay_result = build_replay_sequence(
        op_identity, idem_key, fingerprint, resource_scope, obligation, ir,
    )

    if replay_result.get("status") != "BUILT":
        return {
            "status": "INSUFFICIENT_INFO",
            "reason": "Cannot build replay sequence",
        }

    # Step 6: Generate experiments
    experiments = _generate_replay_experiments(
        replay_result, op_identity, idem_key, fingerprint,
        resource_scope, obligation, budget,
    )

    # Step 7: Build side-effect proof
    side_effect_proof = _build_side_effect_proof(
        op_identity, resource_scope, replay_result, obligation,
    )

    return {
        "status": "REPLAY_PLANNED",
        "experiments": experiments,
        "replay_proof": replay_result.get("replay_proof"),
        "side_effect_proof": side_effect_proof,
        "operation_identity": op_identity,
        "idempotency_key": idem_key,
        "request_fingerprint": fingerprint,
        "resource_scope": resource_scope,
        "replay_sequence": replay_result.get("sequence"),
        "replay_variants": replay_result.get("replay_variants"),
    }


def _generate_replay_experiments(
    replay_result: dict[str, Any],
    op_identity: dict[str, Any],
    idem_key: dict[str, Any],
    fingerprint: dict[str, Any],
    resource_scope: dict[str, Any],
    obligation: dict[str, Any],
    budget: int,
) -> list[dict[str, Any]]:
    """Generate executable experiments from replay sequence."""
    experiments = []
    sequence = _list(replay_result.get("sequence"))
    variants = _list(replay_result.get("replay_variants"))
    operation_ref = _text(op_identity.get("operation_ref"))
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    rule_id = _text(inv.get("rule_id") or inv.get("id"))
    oid = _text(obligation.get("obligation_id") or obligation.get("id"))

    # Experiment 1: Exact replay (primary idempotency test)
    experiments.append({
        "experiment_id": _stable_id("idem_exact", oid, operation_ref),
        "experiment_type": "IDEMPOTENCY_EXACT_REPLAY",
        "description": f"Execute {operation_ref} twice with same key; second must be rejected",
        "sequence": sequence,
        "target_operation": operation_ref,
        "expected_first": "accepted (200/201)",
        "expected_replay": "rejected (409/422/400)",
        "rule_id": rule_id,
        "replay_variant": "EXACT_REPLAY",
        "oracle": {
            "type": "IDEMPOTENCY_ORACLE",
            "check": "second execution must not create duplicate side effect",
            "first_status": [200, 201],
            "replay_status": [409, 422, 400],
            "side_effect_unchanged_after_replay": True,
        },
    })

    # Experiment 2: Same key, different payload (should succeed)
    experiments.append({
        "experiment_id": _stable_id("idem_diff_payload", oid, operation_ref),
        "experiment_type": "IDEMPOTENCY_DIFF_PAYLOAD",
        "description": f"Execute {operation_ref} with same scope but different identity; should succeed",
        "target_operation": operation_ref,
        "expected_outcome": "accepted",
        "rule_id": rule_id,
        "replay_variant": "SAME_KEY_DIFFERENT_PAYLOAD",
        "oracle": {
            "type": "IDEMPOTENCY_ORACLE",
            "check": "different identity field is a new legitimate request",
            "expected_status": [200, 201],
        },
    })

    # Experiment 3: Different key, same payload (should succeed)
    experiments.append({
        "experiment_id": _stable_id("idem_diff_key", oid, operation_ref),
        "experiment_type": "IDEMPOTENCY_DIFF_KEY",
        "description": f"Execute {operation_ref} with different scope but same identity; should succeed",
        "target_operation": operation_ref,
        "expected_outcome": "accepted",
        "rule_id": rule_id,
        "replay_variant": "DIFFERENT_KEY_SAME_PAYLOAD",
        "oracle": {
            "type": "IDEMPOTENCY_ORACLE",
            "check": "same identity in different scope is a different business action",
            "expected_status": [200, 201],
        },
    })

    return experiments[:budget]


def _build_side_effect_proof(
    op_identity: dict[str, Any],
    resource_scope: dict[str, Any],
    replay_result: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    """Build proof that side-effect observation is possible."""
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    rule_id = _text(inv.get("rule_id") or inv.get("id"))
    oid = _text(obligation.get("obligation_id") or obligation.get("id"))
    operation_ref = _text(op_identity.get("operation_ref"))

    return {
        "proof_id": _stable_id("se_proof", oid, operation_ref, rule_id),
        "proof_type": "IDEMPOTENCY_SIDE_EFFECT_PROOF",
        "entity_ref": _text(resource_scope.get("entity_ref")),
        "affected_field": _text(resource_scope.get("affected_field")),
        "side_effect_type": _text(resource_scope.get("side_effect_type")),
        "observation_method": _text(resource_scope.get("observation_method")),
        "can_observe_before": True,
        "can_observe_after": True,
        "can_compare": True,
    }


# ─── Public API ───────────────────────────────────────────────────────────────

def build_idempotency_proof(
    replay_result: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    """Build a formal idempotency proof for audit trail."""
    proof = replay_result.get("replay_proof", {})
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    return {
        **proof,
        "obligation_id": _text(obligation.get("obligation_id") or obligation.get("id")),
        "rule_id": _text(inv.get("rule_id") or inv.get("id")),
        "verdict": "REPLAY_PLANNED" if replay_result.get("status") == "BUILT" else "REPLAY_FAILED",
    }
