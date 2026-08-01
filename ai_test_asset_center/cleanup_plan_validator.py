"""Cleanup plan validator for compile-time and runtime write safety.

The existing WriteReversibilityProof remains the semantic proof authority.
This module adds a fail-closed coverage gate in front of it so a multi-step
experiment cannot prove only its first write while leaving later mutations
without an explicitly scoped compensator.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .write_reversibility_contract import (
    CLEANUP_AUTHORITIES,
    build_reversibility_proof,
    verify_proof_fingerprint,
)


_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_EXEMPTION_KINDS = frozenset(
    {
        "ephemeral_session",
        "ephemeral_token",
        "ephemeral_otp",
        "ephemeral_captcha",
        "read_only_side_effect",
        "source_declared_ephemeral",
    }
)
_SEMANTIC_COMPENSATION_MODES = frozenset(
    {
        "field_restore",
        "restore_snapshot",
        "snapshot_restore",  # alias emitted by some cleanup plan builders
        "inverse_delta",
        "compensating_transition",
        "row_delete",
        "adapter_row_delete",
        "delete_created_resource",
    }
)
_FORMAL_ONLY_MODES = frozenset(
    {
        "reverse_order",
        "reverse",
        "replay",
        "same_operation",
    }
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _validate_cleanup_exemption(
    experiment: dict[str, Any],
) -> dict[str, Any]:
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
    if verification_basis not in {
        "source_declared",
        "runtime_observed",
    }:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": (
                "cleanup_exemption_verification_basis_invalid:"
                f"{verification_basis}"
            ),
        }
    return {"valid": True, "reason_code": "", "detail": ""}


def _mutating_steps(
    experiment: dict[str, Any],
    ops: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Return every declared mutating business step in execution order."""
    rows: list[dict[str, str]] = []
    for phase in ("control", "treatment"):
        for ordinal, raw in enumerate(
            _list(experiment.get(f"{phase}_plan")),
            start=1,
        ):
            step = _dict(raw)
            if not step:
                continue
            operation_ref = _text(step.get("operation_ref"))
            operation = _dict(ops.get(operation_ref))
            method = _text(
                step.get("method") or operation.get("method")
            ).upper()
            if method not in _WRITE_METHODS:
                continue
            rows.append(
                {
                    "step_id": _text(step.get("step_id") or step.get("id")),
                    "phase": phase,
                    "phase_ordinal": str(ordinal),
                    "operation_ref": operation_ref,
                    "method": method,
                    "path": _text(
                        step.get("path")
                        or operation.get("path")
                        or operation.get("raw_path")
                    ),
                }
            )
    return rows


def _cleanup_source_step_id(cleanup_step: dict[str, Any]) -> str:
    return _text(
        cleanup_step.get("source_step_id")
        or cleanup_step.get("compensates_step_id")
        or cleanup_step.get("write_step_id")
    )


def _cleanup_operation_ref(cleanup_step: dict[str, Any]) -> str:
    return _text(
        cleanup_step.get("operation_ref")
        or cleanup_step.get("cleanup_operation_ref")
        or cleanup_step.get("compensator_operation_ref")
    )


def _cleanup_mode(cleanup_step: dict[str, Any]) -> str:
    return _text(
        cleanup_step.get("mode")
        or cleanup_step.get("cleanup_mode")
        or cleanup_step.get("strategy")
    ).lower()


def _validate_multi_write_cleanup_coverage(
    *,
    writes: list[dict[str, str]],
    cleanup_plan: list[dict[str, Any]],
    ops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Prove every mutating step has one explicitly scoped compensator.

    A single-write experiment keeps backward compatibility with the existing
    unscoped WriteReversibilityProof. Two or more writes must declare formal
    step identities and cleanup scope; positional or operation-only matching is
    not accepted.
    """
    if not writes:
        return {
            "valid": True,
            "reason_code": "",
            "detail": "",
            "write_step_ids": [],
            "cleanup_source_step_ids": [],
            "multi_write": False,
        }

    if len(writes) == 1:
        return {
            "valid": True,
            "reason_code": "",
            "detail": "",
            "write_step_ids": [writes[0]["step_id"]],
            "cleanup_source_step_ids": [
                source_id
                for source_id in (
                    _cleanup_source_step_id(step)
                    for step in cleanup_plan
                )
                if source_id
            ],
            "multi_write": False,
        }

    write_step_ids = [row["step_id"] for row in writes]
    if any(not step_id for step_id in write_step_ids):
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": "multi_write_step_identity_missing",
            "write_step_ids": write_step_ids,
            "cleanup_source_step_ids": [],
            "multi_write": True,
        }
    if len(set(write_step_ids)) != len(write_step_ids):
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": "multi_write_step_identity_duplicate",
            "write_step_ids": write_step_ids,
            "cleanup_source_step_ids": [],
            "multi_write": True,
        }

    # A single experiment-level snapshot restore is sufficient when every
    # mutating step targets the same source operation. The runtime restore
    # authority already iterates all accepted writes for that operation;
    # duplicating the plan would send the same restore request repeatedly.
    global_snapshot = [
        step
        for step in cleanup_plan
        if not _cleanup_source_step_id(step)
        and _text(step.get("action")) == "restore_before_snapshot"
        and _cleanup_mode(step) in {"snapshot_restore", "restore_snapshot"}
    ]
    if len(global_snapshot) == 1 and len(global_snapshot) == len(cleanup_plan):
        cleanup_operation_ref = _cleanup_operation_ref(global_snapshot[0])
        cleanup_method = _text(
            global_snapshot[0].get("method")
            or _dict(ops.get(cleanup_operation_ref)).get("method")
        ).upper()
        cleanup_path = _text(
            global_snapshot[0].get("path")
            or _dict(ops.get(cleanup_operation_ref)).get("path")
        )
        if all(
            write["operation_ref"] == cleanup_operation_ref
            and write["method"] == cleanup_method
            and write["path"] == cleanup_path
            for write in writes
        ):
            return {
                "valid": True,
                "reason_code": "",
                "detail": "",
                "write_step_ids": write_step_ids,
                "cleanup_source_step_ids": [],
                "multi_write": True,
                "global_snapshot_restore": True,
            }

    writes_by_id = {row["step_id"]: row for row in writes}
    scoped_cleanup = [
        step
        for step in cleanup_plan
        if _cleanup_source_step_id(step)
    ]
    cleanup_source_ids = [
        _cleanup_source_step_id(step) for step in scoped_cleanup
    ]
    counts = Counter(cleanup_source_ids)
    duplicates = sorted(
        step_id for step_id, count in counts.items() if count > 1
    )
    if duplicates:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": (
                "duplicate_cleanup_compensator_scope:"
                + ",".join(duplicates)
            ),
            "write_step_ids": write_step_ids,
            "cleanup_source_step_ids": cleanup_source_ids,
            "multi_write": True,
        }

    expected = set(write_step_ids)
    actual = set(cleanup_source_ids)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        detail_parts: list[str] = []
        if missing:
            detail_parts.append(
                "missing_cleanup_for_steps:" + ",".join(missing)
            )
        if unknown:
            detail_parts.append(
                "cleanup_scope_not_in_write_plan:" + ",".join(unknown)
            )
        return {
            "valid": False,
            "reason_code": "BLOCKED_NON_REVERSIBLE_WRITE",
            "detail": ";".join(detail_parts),
            "write_step_ids": write_step_ids,
            "cleanup_source_step_ids": cleanup_source_ids,
            "multi_write": True,
        }

    expected_reverse_order = list(reversed(write_step_ids))
    if cleanup_source_ids != expected_reverse_order:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": (
                "cleanup_order_not_reverse_dependency_order:"
                f"expected={','.join(expected_reverse_order)}:"
                f"actual={','.join(cleanup_source_ids)}"
            ),
            "write_step_ids": write_step_ids,
            "cleanup_source_step_ids": cleanup_source_ids,
            "multi_write": True,
        }

    for cleanup_step in scoped_cleanup:
        source_step_id = _cleanup_source_step_id(cleanup_step)
        source_write = writes_by_id[source_step_id]
        source_operation_ref = source_write["operation_ref"]
        declared_source_operation = _text(
            cleanup_step.get("source_operation_ref")
            or cleanup_step.get("compensates_operation_ref")
        )
        if (
            declared_source_operation
            and declared_source_operation != source_operation_ref
        ):
            return {
                "valid": False,
                "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
                "detail": (
                    "cleanup_source_operation_mismatch:"
                    f"{source_step_id}:{declared_source_operation}:"
                    f"{source_operation_ref}"
                ),
                "write_step_ids": write_step_ids,
                "cleanup_source_step_ids": cleanup_source_ids,
                "multi_write": True,
            }

        cleanup_operation_ref = _cleanup_operation_ref(cleanup_step)
        mode = _cleanup_mode(cleanup_step)
        if cleanup_operation_ref and cleanup_operation_ref not in ops:
            return {
                "valid": False,
                "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
                "detail": (
                    "cleanup_operation_not_in_ir:"
                    f"{cleanup_operation_ref}"
                ),
                "write_step_ids": write_step_ids,
                "cleanup_source_step_ids": cleanup_source_ids,
                "multi_write": True,
            }

        if mode in _FORMAL_ONLY_MODES:
            return {
                "valid": False,
                "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
                "detail": (
                    "formal_reverse_order_is_not_compensation:"
                    f"{source_step_id}:{mode}"
                ),
                "write_step_ids": write_step_ids,
                "cleanup_source_step_ids": cleanup_source_ids,
                "multi_write": True,
            }
        if (
            cleanup_operation_ref
            and cleanup_operation_ref == source_operation_ref
            and mode not in _SEMANTIC_COMPENSATION_MODES
        ):
            return {
                "valid": False,
                "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
                "detail": (
                    "source_write_reused_without_semantic_restore:"
                    f"{source_step_id}:{source_operation_ref}:"
                    f"{mode or 'mode_missing'}"
                ),
                "write_step_ids": write_step_ids,
                "cleanup_source_step_ids": cleanup_source_ids,
                "multi_write": True,
            }

    return {
        "valid": True,
        "reason_code": "",
        "detail": "",
        "write_step_ids": write_step_ids,
        "cleanup_source_step_ids": cleanup_source_ids,
        "multi_write": True,
    }


def validate_cleanup_plan(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    *,
    phase: str = "compile",
    compile_proof_fingerprint: str = "",
    runtime_bindings: dict[str, Any] | None = None,
    binding_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate cleanup coverage, semantic authority, and runtime drift."""
    exp = _dict(experiment)
    ir = _dict(behavior_ir)
    ops = {
        _text(operation.get("id")): operation
        for operation in _list(ir.get("operations"))
        if isinstance(operation, dict)
        and _text(operation.get("id"))
    }
    writes = _mutating_steps(exp, ops)
    if not writes:
        return {
            "valid": True,
            "reason_code": "",
            "detail": "",
            "proof": {},
            "coverage": {
                "valid": True,
                "multi_write": False,
                "write_step_ids": [],
                "cleanup_source_step_ids": [],
            },
            "phase": phase,
        }

    cleanup_plan = [
        dict(step)
        for step in _list(exp.get("cleanup_plan"))
        if isinstance(step, dict)
    ]
    safety = _dict(exp.get("safety_contract"))
    if safety.get("cleanup_not_required") or _text(
        _dict(exp.get("cleanup_requirement")).get("required")
    ) == "false":
        exemption_result = _validate_cleanup_exemption(exp)
        if not exemption_result["valid"]:
            return {
                **exemption_result,
                "proof": {},
                "coverage": {},
                "phase": phase,
            }
        return {
            "valid": True,
            "reason_code": "",
            "detail": "",
            "proof": {},
            "coverage": {
                "valid": True,
                "exempt": True,
                "write_step_ids": [
                    write["step_id"] for write in writes
                ],
            },
            "phase": phase,
        }

    coverage = _validate_multi_write_cleanup_coverage(
        writes=writes,
        cleanup_plan=cleanup_plan,
        ops=ops,
    )
    if not coverage["valid"]:
        return {
            "valid": False,
            "reason_code": coverage["reason_code"],
            "detail": coverage["detail"],
            "proof": {},
            "coverage": coverage,
            "phase": phase,
        }

    primary_op_ref, primary_method, primary_path = (
        _identify_primary_write(exp, ops)
    )
    proof = build_reversibility_proof(
        primary_operation_ref=primary_op_ref,
        primary_method=primary_method,
        primary_path=primary_path,
        cleanup_plan=cleanup_plan,
        source_refs=_list(exp.get("source_refs")),
        behavior_ir=ir,
        experiment=exp,
    )
    if _text(proof.get("proof_status")) == "BLOCKED":
        return {
            "valid": False,
            "reason_code": (
                _text(proof.get("reason_code"))
                or "BLOCKED_NON_REVERSIBLE_WRITE"
            ),
            "detail": (
                _text(proof.get("reason_detail"))
                or "cleanup_authority_unresolved"
            ),
            "proof": proof,
            "coverage": coverage,
            "phase": phase,
        }

    authority_block = _dict(proof.get("cleanup_authority"))
    authority_kind = _text(authority_block.get("kind"))
    if authority_kind not in CLEANUP_AUTHORITIES:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": f"unknown_cleanup_authority:{authority_kind}",
            "proof": proof,
            "coverage": coverage,
            "phase": phase,
        }

    cleanup_op_ref = _text(authority_block.get("operation_ref"))
    if cleanup_op_ref and cleanup_op_ref not in ops:
        return {
            "valid": False,
            "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
            "detail": f"cleanup_operation_not_in_ir:{cleanup_op_ref}",
            "proof": proof,
            "coverage": coverage,
            "phase": phase,
        }
    if cleanup_op_ref:
        cleanup_op = _dict(ops.get(cleanup_op_ref))
        cleanup_path = _text(
            cleanup_op.get("path") or cleanup_op.get("raw_path")
        )
        if not cleanup_path.startswith("/"):
            return {
                "valid": False,
                "reason_code": "BLOCKED_INVALID_CLEANUP_PLAN",
                "detail": (
                    "cleanup_path_not_source_declared:"
                    f"{cleanup_op_ref}"
                ),
                "proof": proof,
                "coverage": coverage,
                "phase": phase,
            }

    fingerprint_valid, fingerprint_detail = verify_proof_fingerprint(proof)
    if not fingerprint_valid:
        return {
            "valid": False,
            "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
            "detail": fingerprint_detail,
            "proof": proof,
            "coverage": coverage,
            "phase": phase,
        }

    if phase == "runtime" and compile_proof_fingerprint:
        runtime_fingerprint = _text(proof.get("fingerprint"))
        if runtime_fingerprint != compile_proof_fingerprint:
            return {
                "valid": False,
                "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                "detail": (
                    "fingerprint_mismatch:"
                    f"compile={compile_proof_fingerprint}:"
                    f"runtime={runtime_fingerprint}"
                ),
                "proof": proof,
                "coverage": coverage,
                "phase": phase,
            }

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
                "coverage": coverage,
                "phase": phase,
            }

    return {
        "valid": True,
        "reason_code": "",
        "detail": "",
        "proof": proof,
        "coverage": coverage,
        "phase": phase,
    }


def _validate_runtime_bindings(
    *,
    proof: dict[str, Any],
    runtime_bindings: dict[str, Any],
    binding_receipts: list[dict[str, Any]],
    ops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    identity_contract = _dict(proof.get("identity_contract"))
    cleanup_authority = _dict(proof.get("cleanup_authority"))
    cleanup_request = _dict(proof.get("cleanup_request_contract"))

    identity_from_write_response = _text(
        identity_contract.get("primary_identity_source")
    ) == "primary_write_response"
    identity_fields = _list(identity_contract.get("identity_fields"))
    if identity_fields and not identity_from_write_response:
        for field in identity_fields:
            if field not in runtime_bindings:
                return {
                    "valid": False,
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": f"identity_field_unbound:{field}",
                }

    required_bindings = _list(
        cleanup_request.get("required_bindings")
    )
    identity_field_set = (
        set(identity_fields) if identity_from_write_response else set()
    )
    for binding in required_bindings:
        if binding in identity_field_set:
            continue
        if binding not in runtime_bindings:
            return {
                "valid": False,
                "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                "detail": f"cleanup_binding_unbound:{binding}",
            }

    cleanup_op_ref = _text(cleanup_authority.get("operation_ref"))
    if cleanup_op_ref:
        for receipt in binding_receipts:
            if _text(_dict(receipt).get("kind")) == (
                "operation_substitution"
            ):
                return {
                    "valid": False,
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": (
                        "cleanup_operation_substituted:"
                        f"{_text(_dict(receipt).get('original'))}:"
                        f"{_text(_dict(receipt).get('substitute'))}"
                    ),
                }

    cleanup_path = _text(cleanup_authority.get("path"))
    if cleanup_path:
        for receipt in binding_receipts:
            if _text(_dict(receipt).get("kind")) == "path_modification":
                return {
                    "valid": False,
                    "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                    "detail": (
                        "cleanup_path_modified:"
                        f"{_text(_dict(receipt).get('original'))}:"
                        f"{_text(_dict(receipt).get('modified'))}"
                    ),
                }

    for receipt in binding_receipts:
        if _text(_dict(receipt).get("kind")) == (
            "equivalence_modification"
        ):
            return {
                "valid": False,
                "reason_code": "BLOCKED_CLEANUP_CONTRACT_DRIFT",
                "detail": "equivalence_contract_modified_at_runtime",
            }
    return {"valid": True, "reason_code": "", "detail": ""}


def _identify_primary_write(
    experiment: dict[str, Any],
    ops: dict[str, dict[str, Any]],
) -> tuple[str, str, str]:
    """Compatibility projection for the existing single-proof authority."""
    writes = _mutating_steps(experiment, ops)
    treatment_writes = [
        write for write in writes if write["phase"] == "treatment"
    ]
    primary = treatment_writes[0] if treatment_writes else (
        writes[0] if writes else {}
    )
    return (
        _text(primary.get("operation_ref")),
        _text(primary.get("method")),
        _text(primary.get("path")),
    )
