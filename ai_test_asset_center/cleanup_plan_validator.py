"""Cleanup Plan Validator — compile-time and runtime validation of cleanup plans.

Validates that a compiled cleanup plan is semantically valid before the primary
write reaches transport. Called at two phases:
  - compile: after experiment compilation, before execution
  - runtime: immediately before write transport, verifying proof fingerprint
    and runtime binding integrity

Invalid cleanup plans produce BLOCKED experiments instead of Harness Failures.

SPEC v1.1 §10: Unified Cleanup Plan Validator with semantic validation.
"""
from __future__ import annotations

from typing import Any

from .write_reversibility_contract import (
    CLEANUP_AUTHORITIES,
    build_reversibility_proof,
    build_proof_fingerprint,
    verify_proof_fingerprint,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Exemption validation (SPEC §9) ──────────────────────────────────────────

_EXEMPTION_KINDS = frozenset({
    "ephemeral_session",
    "ephemeral_token",
    "ephemeral_otp",
    "ephemeral_captcha",
    "read_only_side_effect",
    "source_declared_ephemeral",
})


def _validate_cleanup_exemption(experiment: dict[str, Any]) -> dict[str, Any]:
    """Validate cleanup_not_required exemption contract (SPEC §9).

    Returns {"valid": bool, "reason_code": str, "detail": str}.
    """
    exemption = _dict(experiment.get("cleanup_exemption_contract"))
    if not exemption:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": "cleanup_exemption_unproven",
        }

    kind = _text(exemption.get("kind"))
    if kind not in _EXEMPTION_KINDS:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": f"cleanup_exemption_kind_invalid:{kind}",
        }

    if not exemption.get("persistent_effect_absent"):
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": "cleanup_exemption_persistent_effect_not_proven",
        }

    verification_basis = _text(exemption.get("verification_basis"))
    if verification_basis not in {"source_declared", "runtime_observed"}:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": f"cleanup_exemption_verification_basis_invalid:{verification_basis}",
        }

    return {"valid": True, "reason_code": "", "detail": ""}


# ─── Main Validator ───────────────────────────────────────────────────────────


def validate_cleanup_plan(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    *,
    phase: str = "compile",
    compile_proof_fingerprint: str = "",
    runtime_bindings: dict[str, Any] | None = None,
    binding_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate the cleanup plan for a write experiment.

    Args:
        experiment: The compiled experiment dict.
        behavior_ir: The Behavior IR graph.
        phase: "compile" or "runtime".
        compile_proof_fingerprint: The frozen fingerprint from compile phase.
        runtime_bindings: Runtime materialized bindings (Phase B).
        binding_receipts: Binding materialization receipts.

    Returns:
        {
            "valid": bool,
            "reason_code": str,       # empty when valid
            "detail": str,            # empty when valid
            "proof": dict,            # WriteReversibilityProof v1.1
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

    # ── Cleanup exemption (SPEC §9) ──
    if safety.get("cleanup_not_required") or _text(
        _dict(exp.get("cleanup_requirement")).get("required")
    ) == "false":
        exemption_result = _validate_cleanup_exemption(exp)
        if not exemption_result["valid"]:
            return {
                "valid": False,
                "reason_code": exemption_result["reason_code"],
                "detail": exemption_result["detail"],
                "proof": {},
                "phase": phase,
            }
        return {"valid": True, "reason_code": "", "detail": "", "proof": {}, "phase": phase}

    # ── Build reversibility proof (v1.1 semantic) ──
    proof = build_reversibility_proof(
        primary_operation_ref=primary_op_ref,
        primary_method=primary_method,
        primary_path=primary_path,
        cleanup_plan=cleanup_plan,
        source_refs=_list(exp.get("source_refs")),
        behavior_ir=ir,
        experiment=exp,
    )

    # Check proof status
    if _text(proof.get("proof_status")) == "BLOCKED":
        return {
            "valid": False,
            "reason_code": _text(proof.get("reason_code")) or "BLOCKED_NON_REVERSIBLE_WRITE",
            "detail": _text(proof.get("reason_detail")) or "cleanup_authority_unresolved",
            "proof": proof,
            "phase": phase,
        }

    # Validate cleanup authority is in the allowed set
    authority_block = _dict(proof.get("cleanup_authority"))
    authority_kind = _text(authority_block.get("kind"))
    if authority_kind not in CLEANUP_AUTHORITIES:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": f"unknown_cleanup_authority:{authority_kind}",
            "proof": proof,
            "phase": phase,
        }

    # Validate cleanup operation exists in Behavior IR
    cleanup_op_ref = _text(authority_block.get("operation_ref"))
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

    # ── Fingerprint validation ──
    fp_valid, fp_detail = verify_proof_fingerprint(proof)
    if not fp_valid:
        return {
            "valid": False,
            "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
            "detail": fp_detail,
            "proof": proof,
            "phase": phase,
        }

    # ── Runtime phase: verify compile proof fingerprint matches ──
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

    # ── Runtime Phase B: binding validation (SPEC §10) ──
    if phase == "runtime" and runtime_bindings is not None:
        binding_result = _validate_runtime_bindings(
            proof=proof,
            runtime_bindings=runtime_bindings,
            binding_receipts=_list(binding_receipts),
            ops=ops,
        )
        if not binding_result["valid"]:
            return {
                "valid": False,
                "reason_code": binding_result["reason_code"],
                "detail": binding_result["detail"],
                "proof": proof,
                "phase": phase,
            }

    return {"valid": True, "reason_code": "", "detail": "", "proof": proof, "phase": phase}


# ─── Runtime Binding Validation (SPEC §10 Phase B) ───────────────────────────


def _validate_runtime_bindings(
    *,
    proof: dict[str, Any],
    runtime_bindings: dict[str, Any],
    binding_receipts: list[dict[str, Any]],
    ops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate runtime bindings against the frozen proof contract.

    Checks that runtime materialization has not drifted from compile-time proof.
    """
    identity_contract = _dict(proof.get("identity_contract"))
    cleanup_authority = _dict(proof.get("cleanup_authority"))
    cleanup_request = _dict(proof.get("cleanup_request_contract"))

    # Verify identity fields are bound
    identity_fields = _list(identity_contract.get("identity_fields"))
    if identity_fields:
        for field in identity_fields:
            if field not in runtime_bindings:
                return {
                    "valid": False,
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": f"identity_field_unbound:{field}",
                }

    # Verify cleanup required bindings are available
    required_bindings = _list(cleanup_request.get("required_bindings"))
    for binding in required_bindings:
        if binding not in runtime_bindings:
            return {
                "valid": False,
                "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                "detail": f"cleanup_binding_unbound:{binding}",
            }

    # Verify cleanup operation has not been replaced
    cleanup_op_ref = _text(cleanup_authority.get("operation_ref"))
    if cleanup_op_ref:
        # Check binding receipts for operation substitution
        for receipt in binding_receipts:
            if not isinstance(receipt, dict):
                continue
            if _text(receipt.get("kind")) == "operation_substitution":
                return {
                    "valid": False,
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": f"cleanup_operation_substituted:{_text(receipt.get('original'))}:{_text(receipt.get('substitute'))}",
                }

    # Verify cleanup path template has not been modified
    cleanup_path = _text(cleanup_authority.get("path"))
    if cleanup_path:
        for receipt in binding_receipts:
            if not isinstance(receipt, dict):
                continue
            if _text(receipt.get("kind")) == "path_modification":
                return {
                    "valid": False,
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": f"cleanup_path_modified:{_text(receipt.get('original'))}:{_text(receipt.get('modified'))}",
                }

    # Verify equivalence contract has not been modified
    for receipt in binding_receipts:
        if not isinstance(receipt, dict):
            continue
        if _text(receipt.get("kind")) == "equivalence_modification":
            return {
                "valid": False,
                "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                "detail": "equivalence_contract_modified_at_runtime",
            }

    return {"valid": True, "reason_code": "", "detail": ""}


# ─── Primary Write Identification ────────────────────────────────────────────


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
