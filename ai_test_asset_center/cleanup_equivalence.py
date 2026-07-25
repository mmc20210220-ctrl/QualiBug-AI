"""Cleanup Equivalence Engine — verify business state restoration after cleanup.

SPEC v1.1 §13: Cleanup Equivalence Engine

This module evaluates whether a cleanup operation actually restored the business
state to its pre-write condition. It does NOT rely on HTTP status codes alone;
it requires actual observation evidence.

Output: qualibug.cleanup-equivalence-receipt.v1
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256_stable(obj: Any) -> str:
    """Stable SHA256 of a JSON-serializable object."""
    if obj is None:
        return ""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─── Server-managed fields to ignore in comparison ───────────────────────────

SERVER_MANAGED_FIELDS = frozenset({
    "id", "uuid", "createdat", "updatedat", "created_at", "updated_at",
    "version", "revision", "etag", "sequence", "audit", "createdby",
    "updatedby", "created_by", "updated_by", "deletedat", "deleted_at",
})


# ─── Main Equivalence Evaluation ─────────────────────────────────────────────


def evaluate_cleanup_equivalence(
    *,
    proof: dict[str, Any],
    before_observation: dict[str, Any],
    after_write_observation: dict[str, Any],
    after_cleanup_observation: dict[str, Any],
    runtime_bindings: dict[str, Any],
    cleanup_execution_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate whether cleanup restored business state equivalence.

    Args:
        proof: The WriteReversibilityProof v1.1 from compile time.
        before_observation: State before the primary write.
        after_write_observation: State after the primary write.
        after_cleanup_observation: State after cleanup.
        runtime_bindings: Runtime materialized bindings.
        cleanup_execution_receipt: Receipt from cleanup execution.

    Returns:
        Cleanup equivalence receipt with status EQUIVALENT, NOT_EQUIVALENT,
        INDETERMINATE, or NOT_APPLICABLE.
    """
    proof_id = _text(proof.get("proof_id"))
    equivalence_contract = _dict(proof.get("equivalence_contract"))
    identity_contract = _dict(proof.get("identity_contract"))
    mode = _text(equivalence_contract.get("mode"))

    # Extract identity fields
    identity_fields = _list(identity_contract.get("identity_fields"))
    primary_identity = {
        f: runtime_bindings.get(f)
        for f in identity_fields
        if f in runtime_bindings
    }
    cleanup_identity = dict(primary_identity)  # Same identity for most modes

    # Check cleanup execution succeeded
    cleanup_succeeded = cleanup_execution_receipt.get("succeeded", False)
    cleanup_status_code = cleanup_execution_receipt.get("status_code")

    # If cleanup didn't execute or failed at transport, indeterminate
    if not cleanup_succeeded:
        return _build_receipt(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_status="INDETERMINATE",
            reason_code="CLEANUP_EXECUTION_FAILED",
            detail=f"cleanup_transport_failed:status={cleanup_status_code}",
            field_comparison={},
            relation_comparison={},
        )

    # Check after-cleanup observation exists
    if not after_cleanup_observation:
        return _build_receipt(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs={},
            equivalence_status="INDETERMINATE",
            reason_code="AFTER_CLEANUP_OBSERVATION_MISSING",
            detail="after_cleanup_observer_did_not_execute",
            field_comparison={},
            relation_comparison={},
        )

    # Dispatch by equivalence mode
    if mode == "created_entity_absent":
        return _evaluate_created_entity_absent(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
        )

    if mode == "field_comparison":
        return _evaluate_field_comparison(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_contract=equivalence_contract,
        )

    if mode == "business_state_restored":
        return _evaluate_business_state_restored(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
        )

    if mode == "conservation_check":
        return _evaluate_conservation_check(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_contract=equivalence_contract,
        )

    if mode == "full_entity_comparison":
        return _evaluate_full_entity_comparison(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_contract=equivalence_contract,
        )

    if mode == "environment_fingerprint_comparison":
        return _evaluate_environment_fingerprint(
            proof_id=proof_id,
            before_obs=before_observation,
            after_cleanup_obs=after_cleanup_observation,
        )

    # Unknown mode
    return _build_receipt(
        proof_id=proof_id,
        primary_identity=primary_identity,
        cleanup_identity=cleanup_identity,
        before_obs=before_observation,
        after_write_obs=after_write_observation,
        after_cleanup_obs=after_cleanup_observation,
        equivalence_status="INDETERMINATE",
        reason_code="UNKNOWN_EQUIVALENCE_MODE",
        detail=f"equivalence_mode_unrecognized:{mode}",
        field_comparison={},
        relation_comparison={},
    )


# ─── Mode-specific evaluators ────────────────────────────────────────────────


def _evaluate_created_entity_absent(
    *,
    proof_id: str,
    primary_identity: dict[str, Any],
    cleanup_identity: dict[str, Any],
    before_obs: dict[str, Any],
    after_write_obs: dict[str, Any],
    after_cleanup_obs: dict[str, Any],
) -> dict[str, Any]:
    """identity_delete: created entity must be absent after cleanup."""
    # Before: entity should be absent (or not found)
    before_absent = _entity_absent(before_obs)
    # After write: entity should be present
    after_write_present = _entity_present(after_write_obs)
    # After cleanup: entity should be absent
    after_cleanup_absent = _entity_absent(after_cleanup_obs)

    if after_cleanup_absent:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "NOT_EQUIVALENT"
        reason = "ENTITY_STILL_PRESENT_AFTER_CLEANUP"
        detail = "created_entity_not_deleted"

    return _build_receipt(
        proof_id=proof_id,
        primary_identity=primary_identity,
        cleanup_identity=cleanup_identity,
        before_obs=before_obs,
        after_write_obs=after_write_obs,
        after_cleanup_obs=after_cleanup_obs,
        equivalence_status=status,
        reason_code=reason,
        detail=detail,
        field_comparison={
            "compared": ["entity_presence"],
            "matched": ["entity_presence"] if after_cleanup_absent else [],
            "mismatched": [] if after_cleanup_absent else ["entity_presence"],
        },
        relation_comparison={},
    )


def _evaluate_field_comparison(
    *,
    proof_id: str,
    primary_identity: dict[str, Any],
    cleanup_identity: dict[str, Any],
    before_obs: dict[str, Any],
    after_write_obs: dict[str, Any],
    after_cleanup_obs: dict[str, Any],
    equivalence_contract: dict[str, Any],
) -> dict[str, Any]:
    """field_snapshot_restore: compared fields must match before state."""
    compared_fields = _list(equivalence_contract.get("compared_fields"))
    ignored_fields = set(
        f.lower() for f in _list(equivalence_contract.get("ignored_server_fields"))
    ) | SERVER_MANAGED_FIELDS

    before_state = _extract_entity_state(before_obs)
    after_cleanup_state = _extract_entity_state(after_cleanup_obs)

    matched = []
    mismatched = []
    for field in compared_fields:
        if field.lower() in ignored_fields:
            continue
        before_val = before_state.get(field)
        cleanup_val = after_cleanup_state.get(field)
        if before_val == cleanup_val:
            matched.append(field)
        else:
            mismatched.append(field)

    if not mismatched:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "NOT_EQUIVALENT"
        reason = "FIELD_MISMATCH_AFTER_CLEANUP"
        detail = f"mismatched_fields:{','.join(mismatched)}"

    return _build_receipt(
        proof_id=proof_id,
        primary_identity=primary_identity,
        cleanup_identity=cleanup_identity,
        before_obs=before_obs,
        after_write_obs=after_write_obs,
        after_cleanup_obs=after_cleanup_obs,
        equivalence_status=status,
        reason_code=reason,
        detail=detail,
        field_comparison={
            "compared": compared_fields,
            "matched": matched,
            "mismatched": mismatched,
        },
        relation_comparison={},
    )


def _evaluate_business_state_restored(
    *,
    proof_id: str,
    primary_identity: dict[str, Any],
    cleanup_identity: dict[str, Any],
    before_obs: dict[str, Any],
    after_write_obs: dict[str, Any],
    after_cleanup_obs: dict[str, Any],
) -> dict[str, Any]:
    """explicit_compensator: business state must be restored."""
    before_state = _extract_entity_state(before_obs)
    after_cleanup_state = _extract_entity_state(after_cleanup_obs)

    # Compare all non-server-managed fields
    matched = []
    mismatched = []
    for key in before_state:
        if key.lower() in SERVER_MANAGED_FIELDS:
            continue
        if before_state.get(key) == after_cleanup_state.get(key):
            matched.append(key)
        else:
            mismatched.append(key)

    if not mismatched:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "NOT_EQUIVALENT"
        reason = "BUSINESS_STATE_NOT_RESTORED"
        detail = f"mismatched_fields:{','.join(mismatched[:5])}"

    return _build_receipt(
        proof_id=proof_id,
        primary_identity=primary_identity,
        cleanup_identity=cleanup_identity,
        before_obs=before_obs,
        after_write_obs=after_write_obs,
        after_cleanup_obs=after_cleanup_obs,
        equivalence_status=status,
        reason_code=reason,
        detail=detail,
        field_comparison={
            "compared": list(before_state.keys()),
            "matched": matched,
            "mismatched": mismatched,
        },
        relation_comparison={},
    )


def _evaluate_conservation_check(
    *,
    proof_id: str,
    primary_identity: dict[str, Any],
    cleanup_identity: dict[str, Any],
    before_obs: dict[str, Any],
    after_write_obs: dict[str, Any],
    after_cleanup_obs: dict[str, Any],
    equivalence_contract: dict[str, Any],
) -> dict[str, Any]:
    """inverse_delta: conserved quantities must be restored."""
    compared_fields = _list(equivalence_contract.get("compared_fields"))
    before_state = _extract_entity_state(before_obs)
    after_cleanup_state = _extract_entity_state(after_cleanup_obs)

    matched = []
    mismatched = []
    for field in compared_fields:
        before_val = before_state.get(field)
        cleanup_val = after_cleanup_state.get(field)
        if before_val == cleanup_val:
            matched.append(field)
        else:
            mismatched.append(field)

    if not mismatched:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "NOT_EQUIVALENT"
        reason = "CONSERVATION_VIOLATED"
        detail = f"delta_not_reversed:{','.join(mismatched)}"

    return _build_receipt(
        proof_id=proof_id,
        primary_identity=primary_identity,
        cleanup_identity=cleanup_identity,
        before_obs=before_obs,
        after_write_obs=after_write_obs,
        after_cleanup_obs=after_cleanup_obs,
        equivalence_status=status,
        reason_code=reason,
        detail=detail,
        field_comparison={
            "compared": compared_fields,
            "matched": matched,
            "mismatched": mismatched,
        },
        relation_comparison={},
    )


def _evaluate_full_entity_comparison(
    *,
    proof_id: str,
    primary_identity: dict[str, Any],
    cleanup_identity: dict[str, Any],
    before_obs: dict[str, Any],
    after_write_obs: dict[str, Any],
    after_cleanup_obs: dict[str, Any],
    equivalence_contract: dict[str, Any],
) -> dict[str, Any]:
    """exact_recreate: full entity must match (excluding server fields)."""
    ignored_fields = set(
        f.lower() for f in _list(equivalence_contract.get("ignored_server_fields"))
    ) | SERVER_MANAGED_FIELDS

    before_state = _extract_entity_state(before_obs)
    after_cleanup_state = _extract_entity_state(after_cleanup_obs)

    matched = []
    mismatched = []
    all_keys = set(before_state.keys()) | set(after_cleanup_state.keys())
    for key in all_keys:
        if key.lower() in ignored_fields:
            continue
        if before_state.get(key) == after_cleanup_state.get(key):
            matched.append(key)
        else:
            mismatched.append(key)

    if not mismatched:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "NOT_EQUIVALENT"
        reason = "ENTITY_MISMATCH_AFTER_RECREATE"
        detail = f"mismatched_fields:{','.join(mismatched[:5])}"

    return _build_receipt(
        proof_id=proof_id,
        primary_identity=primary_identity,
        cleanup_identity=cleanup_identity,
        before_obs=before_obs,
        after_write_obs=after_write_obs,
        after_cleanup_obs=after_cleanup_obs,
        equivalence_status=status,
        reason_code=reason,
        detail=detail,
        field_comparison={
            "compared": list(all_keys),
            "matched": matched,
            "mismatched": mismatched,
        },
        relation_comparison={},
    )


def _evaluate_environment_fingerprint(
    *,
    proof_id: str,
    before_obs: dict[str, Any],
    after_cleanup_obs: dict[str, Any],
) -> dict[str, Any]:
    """verified_environment_reset: environment fingerprint must match."""
    before_fp = _sha256_stable(before_obs)
    after_fp = _sha256_stable(after_cleanup_obs)

    if before_fp == after_fp:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "NOT_EQUIVALENT"
        reason = "ENVIRONMENT_FINGERPRINT_MISMATCH"
        detail = f"before={before_fp}:after={after_fp}"

    return _build_receipt(
        proof_id=proof_id,
        primary_identity={},
        cleanup_identity={},
        before_obs=before_obs,
        after_write_obs={},
        after_cleanup_obs=after_cleanup_obs,
        equivalence_status=status,
        reason_code=reason,
        detail=detail,
        field_comparison={},
        relation_comparison={},
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _entity_absent(obs: dict[str, Any]) -> bool:
    """Check if entity is absent from observation."""
    if not obs:
        return True
    status = obs.get("status_code")
    if status in {404, 410}:
        return True
    body = obs.get("body")
    if body is None:
        return True
    if isinstance(body, dict) and not body:
        return True
    if isinstance(body, list) and len(body) == 0:
        return True
    # Check for explicit not_found indicators
    if isinstance(body, dict):
        if body.get("error") in {"not_found", "NotFound", "NOT_FOUND"}:
            return True
        if body.get("code") in {"NOT_FOUND", "ENTITY_NOT_FOUND"}:
            return True
    return False


def _entity_present(obs: dict[str, Any]) -> bool:
    """Check if entity is present in observation."""
    return not _entity_absent(obs)


def _extract_entity_state(obs: dict[str, Any]) -> dict[str, Any]:
    """Extract entity state from observation."""
    if not obs:
        return {}
    body = obs.get("body")
    if isinstance(body, dict):
        return body
    if isinstance(body, list) and len(body) == 1 and isinstance(body[0], dict):
        return body[0]
    return {}


def _build_receipt(
    *,
    proof_id: str,
    primary_identity: dict[str, Any],
    cleanup_identity: dict[str, Any],
    before_obs: dict[str, Any],
    after_write_obs: dict[str, Any],
    after_cleanup_obs: dict[str, Any],
    equivalence_status: str,
    reason_code: str,
    detail: str,
    field_comparison: dict[str, Any],
    relation_comparison: dict[str, Any],
) -> dict[str, Any]:
    """Build the cleanup equivalence receipt."""
    receipt = {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "proof_id": proof_id,
        "primary_identity": {"fields": primary_identity},
        "cleanup_identity": {"fields": cleanup_identity},
        "identity_matched": primary_identity == cleanup_identity,
        "before_state_fingerprint": _sha256_stable(before_obs),
        "after_write_state_fingerprint": _sha256_stable(after_write_obs),
        "after_cleanup_state_fingerprint": _sha256_stable(after_cleanup_obs),
        "field_comparison": field_comparison,
        "relation_comparison": relation_comparison,
        "equivalence_status": equivalence_status,
        "reason_code": reason_code,
        "detail": detail,
        "fingerprint": "",
    }
    receipt["fingerprint"] = _sha256_stable({
        "proof_id": proof_id,
        "equivalence_status": equivalence_status,
        "field_comparison": field_comparison,
        "relation_comparison": relation_comparison,
    })
    return receipt
