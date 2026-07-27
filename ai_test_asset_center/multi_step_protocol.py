"""V1.5.0 Multi-Step Protocol Compiler.

Registers multi-step business process protocols with the existing
``experiment_protocol_registry``. Protocols are selected by (risk_family, template)
and produce 3+ step treatment plans from source-declared state transition chains
or business process declarations.

SPEC §19: Multi-Step Protocol compilation must use the existing Protocol Registry.
SPEC §20: Every step has a unique step_id threading through all phases.

This module does NOT create a second protocol registry. It registers compilers
with the existing ``register_family_protocol`` API.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Multi-step protocol template names
TEMPLATE_MULTI_STEP_PROCESS = "multi_step_business_process"
TEMPLATE_STATE_CHAIN_PROCESS = "state_chain_process"
TEMPLATE_SEQUENCE_VERIFICATION = "sequence_verification"

# Breakpoint codes
MULTI_STEP_PROTOCOL_NOT_RESOLVED = "MULTI_STEP_PROTOCOL_NOT_RESOLVED"
MULTI_STEP_IDENTITY_INVALID = "MULTI_STEP_IDENTITY_INVALID"
MULTI_STEP_ACTOR_NOT_BOUND = "MULTI_STEP_ACTOR_NOT_BOUND"
MULTI_STEP_OPERATION_NOT_BOUND = "MULTI_STEP_OPERATION_NOT_BOUND"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def compile_multi_step_process_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    """Compile a multi-step business process protocol.

    The protocol generates a treatment plan from source-declared state transitions
    or business process steps. Each step has a unique step_id.

    Envelope contains:
    - risk_family, operation, operation_ref, control_actor_ref, treatment_actor_ref
    - property_spec (may contain process_steps, expected_order, source_refs)
    - behavior_ir
    """
    family = _text(envelope.get("risk_family"))
    operation = _dict(envelope.get("operation"))
    operation_ref = _text(envelope.get("operation_ref"))
    control_actor = _text(envelope.get("control_actor_ref"))
    treatment_actor = _text(envelope.get("treatment_actor_ref"))
    prop = _dict(envelope.get("property_spec"))
    ir = _dict(envelope.get("behavior_ir"))

    actor_ref = treatment_actor or control_actor
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": MULTI_STEP_ACTOR_NOT_BOUND,
            "detail": "no_actor_for_multi_step_protocol",
        }

    # Source-declared process steps from property_spec
    process_steps = _list(prop.get("process_steps"))
    if not process_steps:
        # Try to derive from state transitions in Behavior IR
        process_steps = _derive_steps_from_transitions(ir, operation_ref)

    if not process_steps:
        return {
            "status": "BLOCKED",
            "reason_code": MULTI_STEP_PROTOCOL_NOT_RESOLVED,
            "detail": "no_source_declared_process_steps",
        }

    # Build treatment plan with unique step_ids
    treatment_plan: list[dict[str, Any]] = []
    seen_step_ids: set[str] = set()

    for idx, step_spec in enumerate(process_steps):
        if not isinstance(step_spec, dict):
            continue
        step_id = _text(step_spec.get("step_id")) or f"treatment_{idx + 1}"
        if step_id in seen_step_ids:
            return {
                "status": "BLOCKED",
                "reason_code": MULTI_STEP_IDENTITY_INVALID,
                "detail": f"duplicate_step_id:{step_id}",
            }
        seen_step_ids.add(step_id)

        op_ref = _text(step_spec.get("operation_ref")) or operation_ref
        if not op_ref:
            return {
                "status": "BLOCKED",
                "reason_code": MULTI_STEP_OPERATION_NOT_BOUND,
                "detail": f"step_{step_id}_missing_operation_ref",
            }

        step_actor = _text(step_spec.get("actor_ref")) or actor_ref

        treatment_plan.append({
            "step_id": step_id,
            "step_ordinal": idx + 1,
            "operation_ref": op_ref,
            "actor_ref": step_actor,
            "method": _text(step_spec.get("method")) or "POST",
            "path": _text(step_spec.get("path")),
            "intent": _text(step_spec.get("intent")) or "business_process_step",
            "protocol_step": "multi_step_treatment",
            "from_state": _text(step_spec.get("from_state")),
            "to_state": _text(step_spec.get("to_state")),
        })

    if len(treatment_plan) < 2:
        return {
            "status": "BLOCKED",
            "reason_code": MULTI_STEP_PROTOCOL_NOT_RESOLVED,
            "detail": f"insufficient_steps:{len(treatment_plan)}",
        }

    # Build cleanup plan (reverse order of treatment)
    cleanup_plan = [
        {
            "step_id": f"cleanup_{step['step_id']}",
            "operation_ref": step["operation_ref"],
            "mode": "reverse_order",
        }
        for step in reversed(treatment_plan)
    ]

    # Expected order from source (NOT from the plan itself)
    expected_order = _list(prop.get("expected_order"))
    source_refs = _list(prop.get("source_refs"))

    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": treatment_plan,
        "cleanup_plan": cleanup_plan,
        "assertion": {
            "kind": "process_completion",
            "expected_steps": [s["step_id"] for s in treatment_plan],
            "expected_order": expected_order,
        },
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "after_state"},
        ],
        "per_step_evidence": True,
        "requires_state_precondition": bool(prop.get("from_state")),
        "expected_order": expected_order,
        "source_refs": source_refs,
        "_registry_protocol_id": f"{family}:{TEMPLATE_MULTI_STEP_PROCESS}",
    }


def _derive_steps_from_transitions(
    behavior_ir: dict[str, Any],
    operation_ref: str,
) -> list[dict[str, Any]]:
    """Derive multi-step process from declared state transitions.

    Finds a chain of transitions starting from the operation's declared
    from_state, following declared edges.
    """
    ir = _dict(behavior_ir)
    relations = _list(ir.get("relations"))
    operations = {
        _text(op.get("id")): op
        for op in _list(ir.get("operations"))
        if _text(_dict(op).get("id"))
    }

    # Build transition adjacency
    transitions: list[dict[str, str]] = []
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if _text(rel.get("relation_type")) != "transitions":
            continue
        op_ref = _text(rel.get("operation_ref"))
        if op_ref and op_ref in operations:
            transitions.append({
                "operation_ref": op_ref,
                "from_ref": _text(rel.get("from_ref")),
                "to_ref": _text(rel.get("to_ref")),
            })

    if not transitions:
        return []

    # Find transitions involving our operation
    relevant = [t for t in transitions if t["operation_ref"] == operation_ref]
    if not relevant:
        return []

    # Build a simple chain from the first relevant transition
    steps: list[dict[str, Any]] = []
    current_to = relevant[0]["to_ref"]
    used_ops: set[str] = {operation_ref}

    # Add the primary operation as step 1
    steps.append({
        "step_id": "treatment_1",
        "operation_ref": operation_ref,
        "from_state": relevant[0]["from_ref"],
        "to_state": relevant[0]["to_ref"],
    })

    # Follow the chain
    for _ in range(10):  # max chain length
        next_trans = next(
            (t for t in transitions
             if t["from_ref"] == current_to
             and t["operation_ref"] not in used_ops),
            None,
        )
        if not next_trans:
            break
        used_ops.add(next_trans["operation_ref"])
        steps.append({
            "step_id": f"treatment_{len(steps) + 1}",
            "operation_ref": next_trans["operation_ref"],
            "from_state": next_trans["from_ref"],
            "to_state": next_trans["to_ref"],
        })
        current_to = next_trans["to_ref"]

    return steps


def compile_state_chain_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    """Compile a state chain verification protocol.

    Verifies that a declared state transition chain executes in order.
    """
    result = compile_multi_step_process_protocol(envelope)
    if result.get("status") != "COMPILED":
        return result

    # Override assertion kind for state chain
    result["assertion"] = {
        **_dict(result.get("assertion")),
        "kind": "step_sequence_order",
    }
    result["_registry_protocol_id"] = f"state:{TEMPLATE_STATE_CHAIN_PROCESS}"
    return result


def compile_sequence_verification_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    """Compile a sequence verification protocol.

    Verifies that source-declared expected order matches observed execution order.
    """
    result = compile_multi_step_process_protocol(envelope)
    if result.get("status") != "COMPILED":
        return result

    result["assertion"] = {
        **_dict(result.get("assertion")),
        "kind": "step_sequence_order",
    }
    result["_registry_protocol_id"] = f"process:{TEMPLATE_SEQUENCE_VERIFICATION}"
    return result


def register_v150_multi_step_protocols() -> list[str]:
    """Register all V1.5.0 multi-step protocols with the existing registry.

    Prerequisites: installs the process_step_observer surface (observer + assertion
    kind) first, because register_family_protocol validates that declared observers
    and assertion kinds are already registered.

    Returns list of registered protocol IDs.
    """
    from .experiment_protocol_registry import register_family_protocol
    from .process_step_observer import install_process_step_surface

    # Ensure the process step timeline observer and step_sequence_order assertion
    # kind exist before protocols that reference them.
    install_process_step_surface()

    registered: list[str] = []

    # Multi-step business process (generic)
    try:
        pid = register_family_protocol(
            "process",
            TEMPLATE_MULTI_STEP_PROCESS,
            compiler=compile_multi_step_process_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="process_completion",
            emits_control=False,
            per_step_evidence=True,
        )
        registered.append(pid)
    except Exception as exc:
        logger.debug("V1.5.0 multi-step process registration: %s", exc)

    # State chain process
    try:
        pid = register_family_protocol(
            "state",
            TEMPLATE_STATE_CHAIN_PROCESS,
            compiler=compile_state_chain_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="state_transition",
            emits_control=False,
            per_step_evidence=True,
        )
        registered.append(pid)
    except Exception as exc:
        logger.debug("V1.5.0 state chain registration: %s", exc)

    # Sequence verification
    try:
        pid = register_family_protocol(
            "process",
            TEMPLATE_SEQUENCE_VERIFICATION,
            compiler=compile_sequence_verification_protocol,
            observers=("http_response", "after_state"),
            assertion_kind="step_sequence_order",
            emits_control=False,
            per_step_evidence=True,
        )
        registered.append(pid)
    except Exception as exc:
        logger.debug("V1.5.0 sequence verification registration: %s", exc)

    return registered
