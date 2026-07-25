"""Write Reversibility Contract — single authority for cleanup proof validation.

Every write experiment must carry a verifiable WriteReversibilityProof before
its primary write reaches transport. This module centralizes the proof schema,
allowed cleanup authorities, and validation logic that was previously scattered
across compiler, runtime preflight, plan executor, and cleanup executor.

Schema: qualibug.write-reversibility-proof.v1
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


# ─── Allowed cleanup authorities (SPEC §5.3) ───────────────────────────────────

CLEANUP_AUTHORITIES = frozenset({
    "identity_delete",
    "explicit_compensator",
    "field_snapshot_restore",
    "inverse_delta",
    "exact_recreate",
    "verified_environment_reset",
})


def build_reversibility_proof(
    *,
    primary_operation_ref: str,
    primary_method: str,
    primary_path: str,
    cleanup_plan: list[dict[str, Any]],
    source_refs: list[dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a WriteReversibilityProof for a compiled experiment.

    Returns a proof dict with status PROVEN or BLOCKED.
    """
    ops = {
        _text(op.get("id")): op
        for op in _list(_dict(behavior_ir).get("operations"))
        if isinstance(op, dict)
    } if behavior_ir else {}

    cleanup_authority = _classify_cleanup_authority(
        primary_method=primary_method,
        primary_path=primary_path,
        cleanup_plan=cleanup_plan,
        ops=ops,
    )

    if cleanup_authority == "none":
        return {
            "schema_version": "qualibug.write-reversibility-proof.v1",
            "proof_id": _proof_id(primary_operation_ref, primary_method, primary_path, "none"),
            "primary_operation_ref": primary_operation_ref,
            "primary_method": primary_method,
            "primary_path": primary_path,
            "cleanup_authority": "none",
            "cleanup_operation_ref": "",
            "proof_kind": "unproven",
            "source_refs": source_refs or [],
            "status": "BLOCKED",
            "reason_code": "BLOCKED_NON_REVERSIBLE_WRITE",
            "reason_detail": _nr_reason_detail(primary_method, primary_path, cleanup_plan),
            "fingerprint": "",
        }

    cleanup_op_ref = _text(
        _dict(cleanup_plan[0] if cleanup_plan else {}).get("operation_ref")
    )
    proof_content = {
        "primary_operation_ref": primary_operation_ref,
        "primary_method": primary_method,
        "primary_path": primary_path,
        "cleanup_authority": cleanup_authority,
        "cleanup_operation_ref": cleanup_op_ref,
    }
    fingerprint = hashlib.sha256(
        json.dumps(proof_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.write-reversibility-proof.v1",
        "proof_id": _proof_id(primary_operation_ref, primary_method, primary_path, cleanup_authority),
        "primary_operation_ref": primary_operation_ref,
        "primary_method": primary_method,
        "primary_path": primary_path,
        "cleanup_authority": cleanup_authority,
        "cleanup_operation_ref": cleanup_op_ref,
        "proof_kind": "verified",
        "source_refs": source_refs or [],
        "status": "PROVEN",
        "reason_code": "",
        "reason_detail": "",
        "fingerprint": fingerprint,
    }


def _classify_cleanup_authority(
    *,
    primary_method: str,
    primary_path: str,
    cleanup_plan: list[dict[str, Any]],
    ops: dict[str, dict[str, Any]],
) -> str:
    """Classify the cleanup authority from the compiled cleanup plan."""
    if not cleanup_plan:
        return "none"

    first = _dict(cleanup_plan[0])
    action = _text(first.get("action"))
    mode = _text(first.get("mode"))
    cleanup_op_ref = _text(first.get("operation_ref"))
    cleanup_op = _dict(ops.get(cleanup_op_ref))
    cleanup_method = _text(first.get("method") or cleanup_op.get("method")).upper()

    # identity_delete: POST create → DELETE /collection/{created_id}
    if cleanup_method == "DELETE" and mode in {"reverse_order", "identity_delete"}:
        return "identity_delete"

    # explicit_compensator: source-declared inverse (reserve→release, lock→unlock)
    if action == "source_declared_compensation" and mode == "reverse_order":
        return "explicit_compensator"

    # field_snapshot_restore: PUT/PATCH in-place mutation with before observer
    if mode == "snapshot_restore" and primary_method in {"PUT", "PATCH"}:
        return "field_snapshot_restore"

    # field_snapshot_restore for POST with non-empty body (status change)
    if mode == "snapshot_restore" and primary_method == "POST":
        return "field_snapshot_restore"

    # inverse_delta: numeric delta compensation
    if mode == "delta_inverse" or action == "inverse_delta_compensation":
        return "inverse_delta"

    # exact_recreate: DELETE → POST recreate
    if mode == "recreate_compensated_resource":
        return "exact_recreate"

    # verified_environment_reset: explicit external adapter
    if mode == "environment_reset" or action == "verified_environment_reset":
        return "verified_environment_reset"

    return "none"


def _nr_reason_detail(
    method: str,
    path: str,
    cleanup_plan: list[dict[str, Any]],
) -> str:
    """Generate a specific reason detail for non-reversible writes."""
    if method == "POST" and "{" in path:
        # Identity-bound action POST
        return "empty_body_action_without_explicit_inverse"
    if method == "POST" and "{" not in path:
        # Collection POST or domain action
        return "domain_action_without_cleanup_authority"
    if method == "DELETE":
        return "delete_without_recreate_proof"
    return "cleanup_authority_unresolved"


def _proof_id(op_ref: str, method: str, path: str, authority: str) -> str:
    """Content-addressed proof identity."""
    content = f"{op_ref}:{method}:{path}:{authority}"
    return hashlib.sha256(content.encode()).hexdigest()[:24]
