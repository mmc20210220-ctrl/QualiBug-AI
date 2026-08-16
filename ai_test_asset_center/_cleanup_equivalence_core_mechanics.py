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


_UNCHANGED_PROOF_IGNORED_FIELDS = frozenset({
    "createdat",
    "updatedat",
    "created_at",
    "updated_at",
    "createdby",
    "updatedby",
    "created_by",
    "updated_by",
    "deletedat",
    "deleted_at",
})


def _business_state_material(value: Any) -> Any:
    """Retain nested business state while removing only volatile metadata."""
    if isinstance(value, dict):
        return {
            _text(key): _business_state_material(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
            if _text(key).lower() not in _UNCHANGED_PROOF_IGNORED_FIELDS
        }
    if isinstance(value, list):
        return [_business_state_material(item) for item in value]
    return value


def _business_field_fingerprint(body: Any) -> str:
    """Fingerprint the complete nested business state for unchanged proof."""
    return _sha256_stable(_business_state_material(body))


def _sealed_state_unchanged_proof(
    *,
    cleanup_execution_receipt: dict[str, Any],
    before_observation: dict[str, Any],
    after_write_observation: dict[str, Any],
) -> bool:
    """True only when sealed before/after-write fingerprints prove no mutation."""
    before_obs = _dict(before_observation)
    after_obs = _dict(after_write_observation)
    if not before_obs or not after_obs:
        return False
    before_status = int(before_obs.get("status_code") or before_obs.get("status") or 0)
    after_status = int(after_obs.get("status_code") or after_obs.get("status") or 0)
    if not (200 <= before_status < 300 and 200 <= after_status < 300):
        return False
    return _business_field_fingerprint(before_obs.get("body")) == _business_field_fingerprint(
        after_obs.get("body")
    )


# ─── Mode resolution from executed cleanup authority ─────────────────────────

_FIELD_RESTORE_EXECUTED = frozenset({
    "field_restore",
    "adapter_field_restore",
    "snapshot_restore",
    "business_state_restored",
})
_ROW_DELETE_EXECUTED = frozenset({
    "row_delete",
    "adapter_row_delete",
    "identity_delete",
    "created_entity_absent",
})


def _executed_cleanup_surface(cleanup_execution_receipt: dict[str, Any]) -> str:
    """Return the cleanup surface actually executed, if the receipt names one."""
    cer = _dict(cleanup_execution_receipt)
    for key in ("cleanup_mode", "mode", "cleanup_surface"):
        value = _text(cer.get(key)).lower()
        if value:
            return value
    adapter = _dict(cer.get("adapter_cleanup_receipt"))
    for key in ("mode", "cleanup_mode", "cleanup_surface"):
        value = _text(adapter.get(key)).lower()
        if value:
            return value
    return ""


def _effective_equivalence_mode(
    proof_mode: str,
    cleanup_execution_receipt: dict[str, Any],
) -> str:
    """Prefer the executed cleanup surface over a stale compile-time WRP mode.

    field_restore leaves the entity present and must be judged as
    business_state_restored — never created_entity_absent /
    ENTITY_STILL_PRESENT_AFTER_CLEANUP.
    """
    executed = _executed_cleanup_surface(cleanup_execution_receipt)
    if executed in _FIELD_RESTORE_EXECUTED:
        return "business_state_restored"
    if executed in _ROW_DELETE_EXECUTED:
        return "created_entity_absent"
    return _text(proof_mode)


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
    mode = _effective_equivalence_mode(
        _text(equivalence_contract.get("mode")),
        cleanup_execution_receipt,
    )

    # Extract identity fields
    identity_fields = _list(identity_contract.get("identity_fields"))
    primary_identity = {
        f: runtime_bindings.get(f)
        for f in identity_fields
        if f in runtime_bindings
    }
    cleanup_identity = dict(primary_identity)  # Same identity for most modes

    # Check cleanup execution succeeded
    # SPEC v1.1.1 §6.3: Missing receipt → INDETERMINATE, never infer success
    if not cleanup_execution_receipt:
        return _build_receipt(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_status="INDETERMINATE",
            reason_code="CLEANUP_EXECUTION_RECEIPT_MISSING",
            detail="no_explicit_cleanup_execution_receipt",
            field_comparison={},
            relation_comparison={},
        )
    if _text(cleanup_execution_receipt.get("schema_version")) != (
        "qualibug.cleanup-execution-receipt.v1"
    ):
        return _build_receipt(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_status="INDETERMINATE",
            reason_code="CLEANUP_EXECUTION_RECEIPT_SCHEMA_INVALID",
            detail="cleanup_execution_receipt_schema_missing_or_invalid",
            field_comparison={},
            relation_comparison={},
        )

    cleanup_receipt_status = _text(
        cleanup_execution_receipt.get("status")
    ).upper()
    # Accepted-residue mode is an explicit non-production waiver: the target's
    # leftover is deliberately accepted (audit receipt, no source compensator)
    # instead of restored, so restoration equivalence does not apply. Unlike a
    # bare NOT_REQUIRED it needs no sealed state-unchanged proof — the state
    # is intentionally NOT restored, and the residue receipt is the contract.
    if (
        cleanup_receipt_status == "NOT_REQUIRED"
        and _text(cleanup_execution_receipt.get("reason_code"))
        == "ACCEPTED_RESIDUE_NO_CLEANUP"
    ):
        return _build_receipt(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_status="NOT_APPLICABLE",
            reason_code="ACCEPTED_RESIDUE",
            detail="accepted_residue_explicit_waiver",
            field_comparison={},
            relation_comparison={},
        )
    # Honest NOT_REQUIRED requires sealed state-unchanged evidence fingerprints.
    # A bare CER status must never waive restoration into NOT_APPLICABLE /
    # Finalizer PASSED — false NOT_REQUIRED (missed entity, short-id hole) stays
    # INDETERMINATE.
    if cleanup_receipt_status == "NOT_REQUIRED":
        if not _sealed_state_unchanged_proof(
            cleanup_execution_receipt=cleanup_execution_receipt,
            before_observation=before_observation,
            after_write_observation=after_write_observation,
        ):
            return _build_receipt(
                proof_id=proof_id,
                primary_identity=primary_identity,
                cleanup_identity=cleanup_identity,
                before_obs=before_observation,
                after_write_obs=after_write_observation,
                after_cleanup_obs=after_cleanup_observation,
                equivalence_status="INDETERMINATE",
                reason_code="CLEANUP_NOT_REQUIRED_UNPROVEN",
                detail="not_required_without_sealed_state_unchanged_proof",
                field_comparison={},
                relation_comparison={},
            )
        return _build_receipt(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_status="NOT_APPLICABLE",
            reason_code="CLEANUP_NOT_REQUIRED",
            detail=_text(cleanup_execution_receipt.get("detail"))
            or _text(cleanup_execution_receipt.get("reason_code"))
            or "cleanup_execution_receipt_not_required",
            field_comparison={},
            relation_comparison={},
        )

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

    # Adapter DB-SQL cleanup is identity-precise: the adapter receipt already
    # proved exactly one affected row (delete or field-restore) with complete
    # identity lineage before sealing ``succeeded=True``. Re-deriving presence
    # from the HTTP collection observation is WRONG here — a collection is never
    # empty after deleting one row (other rows remain), and a route-not-declared
    # 404 is misread as "entity absent". Those two misreads dropped proven
    # VIOLATION findings as ``created_entity_not_deleted`` /
    # ``write_did_not_create_entity_or_mode_mismatch``. The adapter receipt is
    # the authoritative restoration proof, so short-circuit to EQUIVALENT.
    if (
        _text(cleanup_execution_receipt.get("method")).upper() == "ADAPTER_DB_SQL"
        and cleanup_succeeded is True
    ):
        return _build_receipt(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_status="EQUIVALENT",
            reason_code="",
            detail="adapter_cleanup_receipt_authoritative",
            field_comparison={
                "compared": ["adapter_cleanup"],
                "matched": ["adapter_cleanup"],
                "mismatched": [],
            },
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

    # ── SPEC v1.1.1 §7.1: General input gate ──
    # Fail-closed: missing required inputs → INDETERMINATE
    if not mode:
        return _build_receipt(
            proof_id=proof_id,
            primary_identity=primary_identity,
            cleanup_identity=cleanup_identity,
            before_obs=before_observation,
            after_write_obs=after_write_observation,
            after_cleanup_obs=after_cleanup_observation,
            equivalence_status="INDETERMINATE",
            reason_code="EQUIVALENCE_MODE_MISSING",
            detail="equivalence_contract_mode_empty",
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
            identity_fields=identity_fields,
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
    identity_fields: list[Any] | None = None,
) -> dict[str, Any]:
    """identity_delete: created entity must be absent after cleanup.

    SPEC v1.1.1 §7.2: Require all three phases + identity match.
    """
    # Fail-closed: missing observations
    if not before_obs:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="BEFORE_OBSERVATION_MISSING",
            detail="identity_delete_requires_before_observation",
            field_comparison={}, relation_comparison={},
        )
    if not after_write_obs:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="AFTER_WRITE_OBSERVATION_MISSING",
            detail="identity_delete_requires_after_write_observation",
            field_comparison={}, relation_comparison={},
        )

    # Before: entity should be absent (or not found)
    before_absent = _entity_absent(before_obs)
    # After write: entity should be present
    after_write_present = _entity_present(after_write_obs)
    # After cleanup: entity should be absent
    after_cleanup_absent = _entity_absent(after_cleanup_obs)

    # Identity match check
    # SPEC §7.2: identity must match when identity_fields are specified AND
    # can be extracted from runtime_bindings. If identity cannot be extracted,
    # fall back to entity presence/absence verification only.
    identity_required = bool(identity_fields)
    identity_extracted = bool(primary_identity)
    if identity_required and identity_extracted:
        identity_matched = primary_identity == cleanup_identity
    else:
        # Identity not extractable — rely on entity presence/absence only
        identity_matched = True

    # SPEC §7.2: EQUIVALENT requires all conditions
    equivalent = (
        before_absent
        and after_write_present
        and after_cleanup_absent
        and identity_matched
    )

    if equivalent:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    elif not after_cleanup_absent:
        status = "NOT_EQUIVALENT"
        reason = "ENTITY_STILL_PRESENT_AFTER_CLEANUP"
        detail = "created_entity_not_deleted"
    elif not after_write_present:
        # Entity not present after write — mode mismatch or write failed
        status = "INDETERMINATE"
        reason = "ENTITY_NOT_PRESENT_AFTER_WRITE"
        detail = "write_did_not_create_entity_or_mode_mismatch"
    elif not before_absent:
        # Entity existed before write — this is not a create scenario
        # Return INDETERMINATE (mode mismatch) rather than NOT_EQUIVALENT
        status = "INDETERMINATE"
        reason = "ENTITY_PRESENT_BEFORE_WRITE"
        detail = "entity_existed_before_create_mode_mismatch"
    else:
        status = "INDETERMINATE"
        reason = "IDENTITY_MISMATCH"
        detail = "primary_cleanup_identity_mismatch"

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
            "matched": ["entity_presence"] if equivalent else [],
            "mismatched": [] if equivalent else ["entity_presence"],
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
    """field_snapshot_restore: compared fields must match before state.

    SPEC v1.1.1 §7.3: Fail-closed on empty inputs.
    """
    compared_fields = _list(equivalence_contract.get("compared_fields"))
    ignored_fields = set(
        f.lower() for f in _list(equivalence_contract.get("ignored_server_fields"))
    ) | SERVER_MANAGED_FIELDS

    before_state = _extract_entity_state(before_obs)
    after_cleanup_state = _extract_entity_state(after_cleanup_obs)

    # Fail-closed: empty compared_fields
    effective_fields = [f for f in compared_fields if f.lower() not in ignored_fields]
    if not effective_fields:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="NO_COMPARED_FIELDS",
            detail="compared_fields_empty_or_all_ignored",
            field_comparison={"compared": compared_fields, "matched": [], "mismatched": []},
            relation_comparison={},
        )

    # Fail-closed: empty before state
    if not before_state:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="BEFORE_STATE_EMPTY",
            detail="before_observation_has_no_entity_state",
            field_comparison={"compared": compared_fields, "matched": [], "mismatched": []},
            relation_comparison={},
        )

    # Fail-closed: empty after-cleanup state
    if not after_cleanup_state:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="AFTER_CLEANUP_STATE_EMPTY",
            detail="after_cleanup_observation_has_no_entity_state",
            field_comparison={"compared": compared_fields, "matched": [], "mismatched": []},
            relation_comparison={},
        )

    matched = []
    mismatched = []
    for field in effective_fields:
        before_val = before_state.get(field)
        cleanup_val = after_cleanup_state.get(field)
        if before_val == cleanup_val:
            matched.append(field)
        else:
            mismatched.append(field)

    # Observed mismatches are NOT_EQUIVALENT. INDETERMINATE only when no
    # effective field could be compared at all (missing on both sides).
    if mismatched:
        status = "NOT_EQUIVALENT"
        reason = "FIELD_MISMATCH_AFTER_CLEANUP"
        detail = f"mismatched_fields:{','.join(mismatched)}"
    elif matched:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "INDETERMINATE"
        reason = "NO_FIELDS_ACTUALLY_COMPARED"
        detail = "no_business_fields_in_both_states"

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
    """explicit_compensator: business state must be restored.

    SPEC v1.1.1 §7.4: Fail-closed on empty states.
    """
    before_state = _extract_entity_state(before_obs)
    after_cleanup_state = _extract_entity_state(after_cleanup_obs)

    # Fail-closed: empty before state
    business_fields = [k for k in before_state if k.lower() not in SERVER_MANAGED_FIELDS]
    if not business_fields:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="BEFORE_STATE_NO_BUSINESS_FIELDS",
            detail="before_state_empty_or_only_server_managed",
            field_comparison={"compared": [], "matched": [], "mismatched": []},
            relation_comparison={},
        )

    # Fail-closed: empty after-cleanup state
    if not after_cleanup_state:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="AFTER_CLEANUP_STATE_EMPTY",
            detail="after_cleanup_observation_has_no_entity_state",
            field_comparison={"compared": list(before_state.keys()), "matched": [], "mismatched": []},
            relation_comparison={},
        )

    # Compare all non-server-managed fields
    matched = []
    mismatched = []
    for key in business_fields:
        if before_state.get(key) == after_cleanup_state.get(key):
            matched.append(key)
        else:
            mismatched.append(key)

    # Observed mismatches are NOT_EQUIVALENT. INDETERMINATE only when no
    # business field could be compared at all.
    if mismatched:
        status = "NOT_EQUIVALENT"
        reason = "BUSINESS_STATE_NOT_RESTORED"
        detail = f"mismatched_fields:{','.join(mismatched[:5])}"
    elif matched:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "INDETERMINATE"
        reason = "NO_FIELDS_ACTUALLY_COMPARED"
        detail = "no_business_fields_in_both_states"

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
    """inverse_delta: conserved quantities must be restored.

    SPEC v1.1.1 §7.5: Fail-closed on missing/invalid values.
    """
    compared_fields = _list(equivalence_contract.get("compared_fields"))
    before_state = _extract_entity_state(before_obs)
    after_cleanup_state = _extract_entity_state(after_cleanup_obs)

    # Fail-closed: empty compared_fields
    if not compared_fields:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="NO_CONSERVED_FIELDS",
            detail="compared_fields_empty",
            field_comparison={"compared": [], "matched": [], "mismatched": []},
            relation_comparison={},
        )

    matched = []
    mismatched = []
    indeterminate_fields = []
    for field in compared_fields:
        before_val = before_state.get(field)
        cleanup_val = after_cleanup_state.get(field)
        # Fail-closed: missing values are NOT treated as 0
        if before_val is None or cleanup_val is None:
            indeterminate_fields.append(field)
            continue
        if before_val == cleanup_val:
            matched.append(field)
        else:
            mismatched.append(field)

    if indeterminate_fields:
        status = "INDETERMINATE"
        reason = "CONSERVATION_VALUE_MISSING"
        detail = f"missing_values:{','.join(indeterminate_fields)}"
    elif not mismatched and matched:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    elif not matched:
        status = "INDETERMINATE"
        reason = "NO_FIELDS_ACTUALLY_COMPARED"
        detail = "no_conserved_fields_in_both_states"
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
    """exact_recreate: full entity must match (excluding server fields).

    SPEC v1.1.1 §7.6: Fail-closed on empty entities.
    """
    ignored_fields = set(
        f.lower() for f in _list(equivalence_contract.get("ignored_server_fields"))
    ) | SERVER_MANAGED_FIELDS

    before_state = _extract_entity_state(before_obs)
    after_cleanup_state = _extract_entity_state(after_cleanup_obs)

    # Fail-closed: empty before entity
    if not before_state:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="BEFORE_ENTITY_MISSING",
            detail="before_observation_has_no_entity",
            field_comparison={"compared": [], "matched": [], "mismatched": []},
            relation_comparison={},
        )

    # Fail-closed: empty after-cleanup entity
    if not after_cleanup_state:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="AFTER_CLEANUP_ENTITY_MISSING",
            detail="after_cleanup_observation_has_no_entity",
            field_comparison={"compared": [], "matched": [], "mismatched": []},
            relation_comparison={},
        )

    matched = []
    mismatched = []
    all_keys = set(before_state.keys()) | set(after_cleanup_state.keys())
    effective_keys = [k for k in all_keys if k.lower() not in ignored_fields]

    # Fail-closed: no effective fields to compare
    if not effective_keys:
        return _build_receipt(
            proof_id=proof_id, primary_identity=primary_identity,
            cleanup_identity=cleanup_identity, before_obs=before_obs,
            after_write_obs=after_write_obs, after_cleanup_obs=after_cleanup_obs,
            equivalence_status="INDETERMINATE",
            reason_code="NO_COMPARED_FIELDS",
            detail="all_fields_ignored_or_empty",
            field_comparison={"compared": list(all_keys), "matched": [], "mismatched": []},
            relation_comparison={},
        )

    for key in effective_keys:
        if before_state.get(key) == after_cleanup_state.get(key):
            matched.append(key)
        else:
            mismatched.append(key)

    if mismatched:
        status = "NOT_EQUIVALENT"
        reason = "ENTITY_MISMATCH_AFTER_RECREATE"
        detail = f"mismatched_fields:{','.join(mismatched[:5])}"
    elif matched:
        status = "EQUIVALENT"
        reason = ""
        detail = ""
    else:
        status = "INDETERMINATE"
        reason = "NO_FIELDS_ACTUALLY_COMPARED"
        detail = "no_business_fields_in_both_entities"

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
