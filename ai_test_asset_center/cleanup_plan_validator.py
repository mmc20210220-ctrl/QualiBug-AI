"""Cleanup Plan Validator — compile-time and runtime validation of cleanup plans.

Validates that a compiled cleanup plan is semantically valid before the primary
write reaches transport. Called at two phases:
  - compile: after experiment compilation, before execution
  - runtime: immediately before write transport, verifying proof fingerprint

Invalid cleanup plans produce BLOCKED experiments instead of Harness Failures.

SPEC §10: Unified Cleanup Plan Validator
"""
from __future__ import annotations

from typing import Any

from .write_reversibility_contract import (
    CLEANUP_AUTHORITIES,
    build_reversibility_proof,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def validate_cleanup_plan(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    *,
    phase: str = "compile",
    compile_proof_fingerprint: str = "",
) -> dict[str, Any]:
    """Validate the cleanup plan for a write experiment.

    Returns:
        {
            "valid": bool,
            "reason_code": str,       # empty when valid
            "detail": str,            # empty when valid
            "proof": dict,            # WriteReversibilityProof
            "phase": str,
        }
    """
    exp = _dict(experiment)
    ir = _dict(behavior_ir)
    ops = {
        _text(op.get("id")): op
        for op in _list(ir.get("operations"))
        if isinstance(op, dict)
    }

    # Identify the primary write operation
    primary_op_ref, primary_method, primary_path = _identify_primary_write(exp, ops)
    if not primary_op_ref:
        # Read-only experiment — no cleanup validation needed
        return {"valid": True, "reason_code": "", "detail": "", "proof": {}, "phase": phase}

    cleanup_plan = _list(exp.get("cleanup_plan"))
    safety = _dict(exp.get("safety_contract"))

    # Cleanup explicitly not required (ephemeral sessions, read-only probes)
    if safety.get("cleanup_not_required") or _text(
        _dict(exp.get("cleanup_requirement")).get("required")
    ) == "false":
        return {"valid": True, "reason_code": "", "detail": "", "proof": {}, "phase": phase}

    # Build reversibility proof
    proof = build_reversibility_proof(
        primary_operation_ref=primary_op_ref,
        primary_method=primary_method,
        primary_path=primary_path,
        cleanup_plan=cleanup_plan,
        source_refs=_list(exp.get("source_refs")),
        behavior_ir=ir,
    )

    if _text(proof.get("status")) == "BLOCKED":
        return {
            "valid": False,
            "reason_code": _text(proof.get("reason_code")) or "BLOCKED_NON_REVERSIBLE_WRITE",
            "detail": _text(proof.get("reason_detail")) or "cleanup_authority_unresolved",
            "proof": proof,
            "phase": phase,
        }

    # Validate cleanup authority is in the allowed set
    authority = _text(proof.get("cleanup_authority"))
    if authority not in CLEANUP_AUTHORITIES:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": f"unknown_cleanup_authority:{authority}",
            "proof": proof,
            "phase": phase,
        }

    # Validate cleanup operation exists in Behavior IR
    cleanup_op_ref = _text(proof.get("cleanup_operation_ref"))
    if cleanup_op_ref and cleanup_op_ref not in ops:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": f"cleanup_operation_not_in_ir:{cleanup_op_ref}",
            "proof": proof,
            "phase": phase,
        }

    # Validate cleanup path is source-declared
    if cleanup_op_ref:
        cleanup_op = _dict(ops.get(cleanup_op_ref))
        cleanup_path = _text(cleanup_op.get("path") or cleanup_op.get("raw_path"))
        if not cleanup_path.startswith("/"):
            return {
                "valid": False,
                "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
                "detail": f"cleanup_path_not_source_declared:{cleanup_op_ref}",
                "proof": proof,
                "phase": phase,
            }

    # Runtime phase: verify compile proof fingerprint matches
    if phase == "runtime" and compile_proof_fingerprint:
        runtime_fingerprint = _text(proof.get("fingerprint"))
        if runtime_fingerprint != compile_proof_fingerprint:
            return {
                "valid": False,
                "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                "detail": f"fingerprint_mismatch:compile={compile_proof_fingerprint}:runtime={runtime_fingerprint}",
                "proof": proof,
                "phase": phase,
            }

    return {"valid": True, "reason_code": "", "detail": "", "proof": proof, "phase": phase}


def _identify_primary_write(
    experiment: dict[str, Any],
    ops: dict[str, dict[str, Any]],
) -> tuple[str, str, str]:
    """Identify the primary write operation from treatment plan."""
    _WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    for step in _list(experiment.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        op_ref = _text(step.get("operation_ref"))
        op = _dict(ops.get(op_ref))
        method = _text(step.get("method") or op.get("method")).upper()
        if method in _WRITE_METHODS:
            path = _text(step.get("path") or op.get("path") or op.get("raw_path"))
            return op_ref, method, path
    # Fallback: check control plan for write experiments
    safety = _dict(experiment.get("safety_contract"))
    if safety.get("governed_write"):
        for step in _list(experiment.get("control_plan")):
            if not isinstance(step, dict):
                continue
            op_ref = _text(step.get("operation_ref"))
            op = _dict(ops.get(op_ref))
            method = _text(step.get("method") or op.get("method")).upper()
            if method in _WRITE_METHODS:
                path = _text(step.get("path") or op.get("path") or op.get("raw_path"))
                return op_ref, method, path
    return "", "", ""
