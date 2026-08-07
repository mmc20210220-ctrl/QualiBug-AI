"""Write Reversibility Contract — single authority for cleanup proof validation.

Every write experiment must carry a verifiable WriteReversibilityProof before
its primary write reaches transport. This module centralizes the proof schema,
allowed cleanup authorities, and validation logic.

Schema: qualibug.write-reversibility-proof.v1.1

SPEC v1.1: 写可逆性证明主链接线与语义验证
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


# ─── Allowed cleanup authorities (SPEC §5.3 / §7) ────────────────────────────

CLEANUP_AUTHORITIES = frozenset({
    "identity_delete",
    "explicit_compensator",
    "field_snapshot_restore",
    "inverse_delta",
    "exact_recreate",
    "verified_environment_reset",
    # V1.6.1: admit declared adapter cleanup for sandbox write reversibility.
    # Authority remains fail-closed on missing ownership/scope legs
    # (_validate_declared_adapter_cleanup). Without this, RESOLVED field-level
    # write rules compile to unknown_cleanup_authority and never reach Field
    # Oracle Trace — the V1.6.0 Stage B breakpoint.
    "declared_adapter_cleanup",
    # restore_deleted_resource: same-path PATCH/PUT restore of a soft-deleted
    # row. Identity is preserved (same row), the restore body is source-declared
    # (_validate_restore_deleted_resource), and the executor falls back to the
    # collection recreate when the row is gone.
    "restore_deleted_resource",
    # accepted_residue: the write is NOT reversed. Admitted only for a target the
    # operator explicitly declared non-production AND when no real compensator
    # (API/DB/UI) resolved. It is a coverage-over-cleanup decision, never a claim
    # that cleanup happened: proof_kind stays "accepted_residue", reversibility is
    # "none", and the executor emits a residue receipt so the leftover resource is
    # visible for later environment reset. Production never reaches this branch.
    "accepted_residue",
})

# ── declared_adapter_cleanup: built, tested, and NOT yet authorised ──────────
#
# A row delete through an adapter the operator declared, for a target whose API offers
# no compensator. On a live 11-service system only 2 of 17 writes had one, so this is
# the difference between testing most writes and refusing to.
#
# Admitting it into CLEANUP_AUTHORITIES was tried and MEASURED, and it is switched off
# again for one reason: the cleanup executor's adapter branch produced ZERO receipts, so
# there is no evidence it ever ran. The compiled plans were correct -- 784 steps carried
# adapter db_sql, table and identity column -- and the gate movement was real
# (BLOCKED_NON_REVERSIBLE_WRITE 668 -> 151, BLOCKED_INVALID_CLEANUP_PLAN 517 -> 0), but a
# compensator with no execution evidence is not a compensator.
#
# Attribution, corrected: the 517 unblocked obligations reached PLANNING, not execution --
# they landed on OBLIGATION_BUDGET_REACHED. The run performed 14 treatment writes and 3
# cleanups. Target residue observed at the time (qb_auto products, cart_items) is
# substantially from the test-data BOOTSTRAP, which creates one probe product per
# campaign and was already leaving them before any of this work -- the first benchmark
# run recorded qb_auto_sku_QBBOOTSTRAP_* rows with status DELETED still visible in the
# catalogue. An earlier version of this comment blamed the residue on this change; that
# causal claim was not supported and is withdrawn.
#
# The caution stands on its own ground: authorising a write whose compensator has never
# been observed to execute converts "this cannot be tested safely" into "this was tested
# and may have left residue", and the customer would discover it in their own database.
#
# What is already in place and does not depend on this switch:
#   - cleanup_adapter_ladder: the tier resolution, the ownership proof, the guarded
#     executor, all covered by tests including "a customer row never reaches SQL";
#   - _validate_declared_adapter_cleanup below, which refuses any plan missing a leg;
#   - the compiler emitting the plan.
#
# Flip this on only after the cleanup executor demonstrably deletes the row -- the check
# is the residue count in the target after a run, not a passing unit test.
#
# THE CHECK WAS RUN, twice, and the honest state is UNEXERCISED rather than broken.
#
# With the authority on, dependency-ordered deletion wired and the branch proven
# reachable in isolation (tests/test_adapter_cleanup_reaches_the_executor.py), a live run
# produced zero adapter cleanup receipts. Tracing why: the run carried 13 governance
# receipts, all accepted -- 10 treatment writes and 3 cleanups -- so the cleanup loop DID
# run. Those three cleanups took the HTTP path because their plans were HTTP plans. The
# 784 db_sql plans belong to experiments that never executed at all: they sit on
# OBLIGATION_BUDGET_REACHED, 652 of them. No write, no cleanup, no receipt.
#
# So the tier has never had an experiment with both an accepted write and a db_sql plan
# reach it, and the target residue in that run came from the ten HTTP-path writes, not
# from this. An earlier version of this note said the branch was not reached because
# something upstream was unsatisfied; that reading was wrong and is corrected here.
#
# THIRD CHECK, with the slice-budget fix in place so obligations actually execute
# (budget 1200, OBLIGATION_BUDGET_REACHED no longer the wall it was): still ZERO adapter
# cleanup receipts, and CONTRACT_ORACLE_HARNESS_FAILED returned to 99. So raising the
# budget was not the missing piece either.
#
# Three checks, three zeros. Whatever prevents an experiment carrying a db_sql plan from
# reaching an accepted governed write has not been found, and each hypothesis so far --
# the branch is unreachable, the guard is unsatisfied, the budget starves it -- has been
# disproved by the next measurement. "Built and tested, never exercised" remains the
# whole claim, and the next attempt should start by instrumenting which experiments carry
# a db_sql plan and where each one actually terminates, rather than by proposing another
# cause.
ADAPTER_CLEANUP_AUTHORITY = "declared_adapter_cleanup"

# Server-managed fields that must never appear in restore bodies
SERVER_MANAGED_FIELDS = frozenset({
    "id", "uuid", "createdat", "updatedat", "created_at", "updated_at",
    "version", "revision", "etag", "sequence", "audit", "createdby",
    "updatedby", "created_by", "updated_by", "deletedat", "deleted_at",
})


# ─── Proof Fingerprint (SPEC §6) ─────────────────────────────────────────────


def build_proof_fingerprint(proof: dict[str, Any]) -> str:
    """Compute a stable, content-addressed fingerprint for a proof.

    Covers all semantic fields. Excludes time, random UUIDs, runtime responses,
    execution IDs, and campaign IDs to guarantee deterministic output.
    """
    content = {
        "primary_write": _dict(proof.get("primary_write")),
        "cleanup_authority": _dict(proof.get("cleanup_authority")),
        "identity_contract": _dict(proof.get("identity_contract")),
        "before_observation_contract": _dict(proof.get("before_observation_contract")),
        "after_write_observation_contract": _dict(proof.get("after_write_observation_contract")),
        "after_cleanup_observation_contract": _dict(proof.get("after_cleanup_observation_contract")),
        "cleanup_request_contract": _dict(proof.get("cleanup_request_contract")),
        "equivalence_contract": _dict(proof.get("equivalence_contract")),
        "compile_context": _dict(proof.get("compile_context")),
    }
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def verify_proof_fingerprint(proof: dict[str, Any]) -> tuple[bool, str]:
    """Verify that a proof's stored fingerprint matches its content.

    Returns (valid, detail). If invalid, detail explains the mismatch.
    """
    stored = _text(proof.get("fingerprint"))
    if not stored:
        return False, "proof_fingerprint_empty"
    computed = build_proof_fingerprint(proof)
    if computed != stored:
        return False, f"proof_content_fingerprint_mismatch:stored={stored}:computed={computed}"
    return True, ""


# ─── Proof Builder ────────────────────────────────────────────────────────────


def build_reversibility_proof(
    *,
    primary_operation_ref: str,
    primary_method: str,
    primary_path: str,
    cleanup_plan: list[dict[str, Any]],
    source_refs: list[dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
    experiment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a WriteReversibilityProof v1.1 for a compiled experiment.

    Returns a proof dict with proof_status PROVEN or BLOCKED.
    """
    ir = _dict(behavior_ir)
    ops = {
        _text(op.get("id")): op
        for op in _list(ir.get("operations"))
        if isinstance(op, dict)
    }
    relations = _list(ir.get("relations"))
    exp = _dict(experiment)

    # Classify cleanup authority with semantic validation
    authority_result = _classify_cleanup_authority_v11(
        primary_operation_ref=primary_operation_ref,
        primary_method=primary_method,
        primary_path=primary_path,
        cleanup_plan=cleanup_plan,
        ops=ops,
        relations=relations,
        experiment=exp,
        entities=_list(ir.get("entities")),
    )

    authority_kind = authority_result["kind"]

    # Build primary_write block
    primary_op = _dict(ops.get(primary_operation_ref))
    primary_write = {
        "operation_ref": primary_operation_ref,
        "method": primary_method,
        "path": primary_path,
        "entity_ref": _text(primary_op.get("entity_ref")),
        "request_schema_fingerprint": _sha256_stable(
            primary_op.get("request_example") or primary_op.get("request_schema")
        ),
        "source_refs": _list(primary_op.get("source_refs")) or source_refs or [],
    }

    if authority_kind == "none":
        proof = {
            "schema_version": "qualibug.write-reversibility-proof.v1.1",
            "proof_id": _proof_id(primary_operation_ref, primary_method, primary_path, "none"),
            "proof_status": "BLOCKED",
            "proof_kind": "unproven",
            "primary_write": primary_write,
            "cleanup_authority": {"kind": "none"},
            "identity_contract": {},
            "before_observation_contract": {},
            "after_write_observation_contract": {},
            "after_cleanup_observation_contract": {},
            "cleanup_request_contract": {},
            "equivalence_contract": {},
            "compile_context": _build_compile_context(
                ir,
                exp,
                primary_operation_ref=primary_operation_ref,
                cleanup_plan=cleanup_plan,
            ),
            "fingerprint": "",
            "reason_code": "BLOCKED_NON_REVERSIBLE_WRITE",
            "reason_detail": authority_result.get("detail")
                or _nr_reason_detail(primary_method, primary_path, cleanup_plan),
        }
        return proof

    # Build full proof with contracts
    cleanup_authority_block = authority_result["authority_block"]
    identity_contract = authority_result.get("identity_contract", {})
    before_obs = authority_result.get("before_observation_contract", {})
    after_write_obs = authority_result.get("after_write_observation_contract", {})
    after_cleanup_obs = authority_result.get("after_cleanup_observation_contract", {})
    cleanup_request = authority_result.get("cleanup_request_contract", {})
    equivalence = authority_result.get("equivalence_contract", {})

    proof = {
        "schema_version": "qualibug.write-reversibility-proof.v1.1",
        "proof_id": _proof_id(primary_operation_ref, primary_method, primary_path, authority_kind),
        "proof_status": "PROVEN",
        "proof_kind": authority_kind,
        "primary_write": primary_write,
        "cleanup_authority": cleanup_authority_block,
        "identity_contract": identity_contract,
        "before_observation_contract": before_obs,
        "after_write_observation_contract": after_write_obs,
        "after_cleanup_observation_contract": after_cleanup_obs,
        "cleanup_request_contract": cleanup_request,
        "equivalence_contract": equivalence,
        "compile_context": _build_compile_context(
            ir,
            exp,
            primary_operation_ref=primary_operation_ref,
            cleanup_plan=cleanup_plan,
        ),
        "fingerprint": "",
        "reason_code": "",
        "reason_detail": "",
    }
    proof["fingerprint"] = build_proof_fingerprint(proof)
    return proof


# ─── Semantic Authority Classification (SPEC §7) ─────────────────────────────


def _classify_cleanup_authority_v11(
    *,
    primary_operation_ref: str,
    primary_method: str,
    primary_path: str,
    cleanup_plan: list[dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    relations: list[Any],
    experiment: dict[str, Any],
    entities: list[Any] | None = None,
) -> dict[str, Any]:
    """Classify cleanup authority with full semantic validation.

    Returns {"kind": str, "detail": str, "authority_block": dict, ...contracts}
    """
    if not cleanup_plan:
        return {"kind": "none", "detail": "empty_cleanup_plan"}

    first = _dict(cleanup_plan[0])
    action = _text(first.get("action"))
    mode = _text(first.get("mode"))
    cleanup_op_ref = _text(first.get("operation_ref"))
    cleanup_op = _dict(ops.get(cleanup_op_ref))
    cleanup_method = _text(first.get("method") or cleanup_op.get("method")).upper()
    cleanup_path = _text(first.get("path") or cleanup_op.get("path") or cleanup_op.get("raw_path"))

    # ── identity_delete (SPEC §7.1) ──
    if cleanup_method == "DELETE" and mode in {"reverse_order", "identity_delete", "delete_created_resource"}:
        return _validate_identity_delete(
            primary_method=primary_method,
            primary_path=primary_path,
            cleanup_op_ref=cleanup_op_ref,
            cleanup_op=cleanup_op,
            cleanup_path=cleanup_path,
            ops=ops,
        )

    # ── explicit_compensator (SPEC §7.2) ──
    # Non-DELETE cleanup with mode "reverse_order" is a compensating action
    # (e.g. POST /payments/order/{id}/void compensating POST /payments/pay).
    if action == "source_declared_compensation" or mode == "compensator" or (
        mode == "reverse_order" and cleanup_method and cleanup_method != "DELETE"
    ):
        return _validate_explicit_compensator(
            primary_operation_ref=primary_operation_ref,
            cleanup_op_ref=cleanup_op_ref,
            cleanup_op=cleanup_op,
            cleanup_method=cleanup_method,
            cleanup_path=cleanup_path,
            relations=relations,
            ops=ops,
        )

    # ── field_snapshot_restore (SPEC §7.3) ──
    if mode in {"snapshot_restore", "restore_snapshot"} or action == "restore_before_snapshot":
        return _validate_field_snapshot_restore(
            primary_method=primary_method,
            primary_path=primary_path,
            primary_operation_ref=primary_operation_ref,
            cleanup_op=cleanup_op,
            cleanup_method=cleanup_method,
            cleanup_path=cleanup_path,
            experiment=experiment,
            ops=ops,
            entities=entities or [],
            relations=relations,
        )

    # ── inverse_delta (SPEC §7.4) ──
    if mode == "delta_inverse" or action == "inverse_delta_compensation":
        return _validate_inverse_delta(
            primary_operation_ref=primary_operation_ref,
            cleanup_op_ref=cleanup_op_ref,
            experiment=experiment,
            ops=ops,
        )

    # ── exact_recreate (SPEC §7.5) ──
    if mode == "recreate_compensated_resource":
        return _validate_exact_recreate(
            primary_method=primary_method,
            primary_path=primary_path,
            cleanup_op_ref=cleanup_op_ref,
            cleanup_op=cleanup_op,
            experiment=experiment,
            ops=ops,
        )

    # ── restore_deleted_resource (soft-delete compensation) ──
    if mode == "restore_deleted_resource":
        return _validate_restore_deleted_resource(
            primary_method=primary_method,
            primary_path=primary_path,
            cleanup_op_ref=cleanup_op_ref,
            cleanup_op=cleanup_op,
            experiment=experiment,
            ops=ops,
        )

    # ── verified_environment_reset (SPEC §7.6) ──
    if mode == "environment_reset" or action == "verified_environment_reset":
        return _validate_environment_reset(experiment=experiment)

    # ── declared_adapter_cleanup ──
    # A row delete through an adapter the operator declared, when the API offers no
    # compensator. Measured on a live target, only 2 of 17 writes had an API
    # compensator, so this branch is the difference between testing a system and
    # refusing to. It is the same authority as identity_delete -- remove the row the
    # write created -- reached through a different surface.
    #
    # It is admitted only with every leg present, and the ownership proof is NOT
    # optional: the executor re-checks the real row identity and refuses anything this
    # run did not create. Without that flag the plan is rejected here rather than
    # trusted.
    if (
        action == "declared_adapter_cleanup"
        or mode in {
            "adapter_row_delete",
            "row_delete",
            "field_restore",
            "adapter_field_restore",
        }
    ):
        return _validate_declared_adapter_cleanup(
            first,
            primary_method=primary_method,
            primary_path=primary_path,
            primary_op=_dict(ops.get(primary_operation_ref)),
            primary_operation_ref=primary_operation_ref,
            relations=relations,
        )

    # ── accepted_residue (non-production degradation) ──
    # The write is deliberately NOT reversed. This branch only ever fires when the
    # compiler attached an accepted-residue plan, which it does solely for a target
    # the operator declared non-production and only after every real compensator
    # (API/DB/UI) failed to resolve. The authority carries no operation_ref and no
    # cleanup contracts -- there is no cleanup request to make -- so the validator's
    # source-declared path checks pass vacuously. reversibility stays "none" and
    # residue_accepted stays True so nothing downstream mistakes this for a real
    # cleanup.
    if action == "accepted_residue" or mode == "accepted_residue_no_cleanup":
        return {
            "kind": "accepted_residue",
            "detail": "",
            "authority_block": {
                "kind": "accepted_residue",
                "reversibility": "none",
                "residue_accepted": True,
                "residue_notice": _text(first.get("residue_notice"))
                or "write_left_uncleaned_on_declared_non_production_target",
            },
            "identity_contract": {},
            "before_observation_contract": {},
            "after_write_observation_contract": {},
            "after_cleanup_observation_contract": {},
            "cleanup_request_contract": {},
            "equivalence_contract": {},
        }

    return {"kind": "none", "detail": "cleanup_authority_unrecognized"}


def _relation_kind(rel: dict[str, Any]) -> str:
    return _text(
        rel.get("kind") or rel.get("type") or rel.get("relation") or rel.get("relation_type")
    ).lower()


def _operation_refs_match(rel: dict[str, Any], operation_ref: str) -> bool:
    op = _text(operation_ref)
    if not op:
        return False
    candidates = {
        _text(rel.get("operation_ref")),
        _text(rel.get("from_ref")),
        _text(rel.get("from")),
        _text(rel.get("source")),
        _text(rel.get("op_ref")),
    }
    return op in candidates


def _operation_produces_entity(
    operation_ref: str,
    relations: list[Any] | None,
) -> bool:
    """True when Behavior IR declares the write produces/creates an entity."""
    for rel in _list(relations):
        if not isinstance(rel, dict):
            continue
        kind = _relation_kind(rel)
        if kind not in {"produces", "creates", "create"}:
            continue
        if _operation_refs_match(rel, operation_ref):
            return True
    return False


def _operation_mutates_entity(
    operation_ref: str,
    relations: list[Any] | None,
) -> bool:
    """True when Behavior IR declares the write mutates an existing entity."""
    for rel in _list(relations):
        if not isinstance(rel, dict):
            continue
        kind = _relation_kind(rel)
        if kind not in {
            "mutates",
            "updates",
            "modifies",
            "transitions",
            "state_transition",
            "affects",
        }:
            continue
        if _operation_refs_match(rel, operation_ref):
            return True
    return False


def _adapter_cleanup_is_field_restore(
    step: dict[str, Any],
    *,
    primary_method: str,
    primary_path: str,
    primary_op: dict[str, Any],
    primary_operation_ref: str = "",
    relations: list[Any] | None = None,
) -> bool:
    """True when adapter cleanup must restore mutated fields, not delete a created row.

    Classification authority is source cleanup mode / produces-entity vs
    mutates-entity — not empty-body heuristics alone. Create-under-parent POSTs
    that produce a child must stay on row_delete even when the request example
    is empty.

    Mutates/transitions wins over a co-declared produces tag: IR often attaches
    both to identity-bound action POSTs (ship/pay/confirm). Preferring produces
    left WRP on created_entity_absent while runtime field_restore kept the row,
    which falsely failed as ENTITY_STILL_PRESENT_AFTER_CLEANUP.
    """
    from .real_id_resolver_base import path_has_placeholders

    step_mode = _text(step.get("mode")).lower()
    if step_mode in {"field_restore", "adapter_field_restore", "snapshot_restore"}:
        return True

    op_ref = _text(primary_operation_ref) or _text(primary_op.get("id"))
    # Mutates/transitions first — co-declared produces must not force row_delete.
    if _operation_mutates_entity(op_ref, relations):
        return True
    if _operation_produces_entity(op_ref, relations):
        return False

    method = _text(primary_method).upper()
    if method in {"PUT", "PATCH"} and path_has_placeholders(primary_path):
        return True
    # Explicit row_delete without produces/mutates stays row_delete.
    # Empty-body identity POST alone is not enough to force field_restore.
    if step_mode in {"row_delete", "adapter_row_delete"}:
        return False
    return False


def _validate_declared_adapter_cleanup(
    step: dict[str, Any],
    *,
    primary_method: str = "",
    primary_path: str = "",
    primary_op: dict[str, Any] | None = None,
    primary_operation_ref: str = "",
    relations: list[Any] | None = None,
) -> dict[str, Any]:
    """Admit a declared-adapter cleanup as authority, or say what is missing.

    Always emits an equivalence_contract. A PROVEN adapter proof with an empty mode
    leaves cleanup equivalence permanently INDETERMINATE (EQUIVALENCE_MODE_MISSING)
    even after a successful field_restore / row delete.
    """
    row = _dict(step)
    adapter = _text(row.get("adapter"))
    table = _text(row.get("table"))
    identity_column = _text(row.get("identity_column"))
    if adapter != "db_sql":
        return {"kind": "none", "detail": f"adapter_cleanup_unsupported_adapter:{adapter or 'none'}"}
    if not table or not identity_column:
        return {"kind": "none", "detail": "adapter_cleanup_table_or_identity_not_declared"}
    if row.get("requires_ownership_proof") is not True:
        # A data-layer delete without a runtime ownership check could remove customer
        # data. The flag is what makes the executor enforce it, so its absence is fatal.
        return {"kind": "none", "detail": "adapter_cleanup_ownership_proof_not_required"}
    if _text(row.get("scope")) != "run_created_only":
        return {"kind": "none", "detail": "adapter_cleanup_scope_not_run_created_only"}

    field_restore = _adapter_cleanup_is_field_restore(
        row,
        primary_method=primary_method,
        primary_path=primary_path,
        primary_op=_dict(primary_op),
        primary_operation_ref=primary_operation_ref,
        relations=relations,
    )
    authority_block = {
        "kind": "declared_adapter_cleanup",
        "operation_ref": "",
        "method": "UPDATE" if field_restore else "DELETE",
        "path": f"db://{table}",
        "adapter": adapter,
        "source_refs": [],
        "authority_relation_ref": "",
        "cleanup_surface": "field_restore" if field_restore else "row_delete",
    }
    identity_contract = {
        "identity_fields": [identity_column],
        "primary_identity_source": "primary_write_response",
        "cleanup_identity_targets": [f"row.{identity_column}"],
        "same_entity_required": True,
        "identity_preservation_required": True,
        "ownership_proof_required": True,
    }
    if field_restore:
        return {
            "kind": "declared_adapter_cleanup",
            "detail": f"{adapter}:{table}.{identity_column}:field_restore",
            "authority_block": authority_block,
            "identity_contract": identity_contract,
            "before_observation_contract": {
                "required": True,
                "observer_kind": "entity_read",
                "proof_semantics": "field_values_before",
            },
            "after_write_observation_contract": {
                "required": True,
                "observer_kind": "entity_read",
                "proof_semantics": "field_values_changed",
            },
            "after_cleanup_observation_contract": {
                "required": True,
                "observer_kind": "entity_read",
                "proof_semantics": "field_values_restored",
            },
            "cleanup_request_contract": {
                "body_strategy": "before_snapshot_fields",
                "allowed_fields": [],
                "required_bindings": [identity_column],
            },
            # business_state_restored compares all non-server-managed fields from the
            # sealed before vs after-cleanup observations — no compile-time field list
            # required, and no waiver of missing after-cleanup proof.
            "equivalence_contract": {
                "mode": "business_state_restored",
                "identity_required": True,
                "compared_fields": [],
                "ignored_server_fields": sorted(SERVER_MANAGED_FIELDS),
            },
        }

    return {
        "kind": "declared_adapter_cleanup",
        "detail": f"{adapter}:{table}.{identity_column}",
        "authority_block": authority_block,
        "identity_contract": identity_contract,
        "before_observation_contract": {
            "required": True,
            "observer_kind": "collection_membership",
            "proof_semantics": "entity_absent",
        },
        "after_write_observation_contract": {
            "required": True,
            "observer_kind": "identity_read",
            "proof_semantics": "entity_present",
        },
        "after_cleanup_observation_contract": {
            "required": True,
            "observer_kind": "identity_read",
            "proof_semantics": "entity_absent",
        },
        "cleanup_request_contract": {
            "body_strategy": "none",
            "allowed_fields": [],
            "required_bindings": [identity_column],
        },
        "equivalence_contract": {
            "mode": "created_entity_absent",
            "identity_required": True,
            "compared_fields": [],
            "ignored_server_fields": [],
        },
    }


def _validate_identity_delete(
    *,
    primary_method: str,
    primary_path: str,
    cleanup_op_ref: str,
    cleanup_op: dict[str, Any],
    cleanup_path: str,
    ops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """SPEC §7.1: identity_delete semantic validation."""
    # Primary must be collection create (POST without identity placeholder)
    if primary_method != "POST":
        return {"kind": "none", "detail": "identity_delete_primary_not_collection_create"}
    if "{" in primary_path:
        return {"kind": "none", "detail": "identity_delete_primary_not_collection_create"}

    # Cleanup must be DELETE
    cleanup_method = _text(cleanup_op.get("method")).upper()
    if cleanup_method != "DELETE":
        return {"kind": "none", "detail": "identity_delete_cleanup_not_delete"}

    # DELETE path must be in same collection
    primary_collection = primary_path.rstrip("/").rsplit("/", 1)[0] if "/" in primary_path else primary_path
    cleanup_collection = cleanup_path.rstrip("/").rsplit("/", 1)[0] if "/" in cleanup_path else cleanup_path
    # Normalize: /orders vs /orders/{id} → both under /orders
    if primary_collection and cleanup_collection:
        # Allow /orders and /orders/{orderId} to match
        pc = primary_path.rstrip("/")
        cc_base = cleanup_path.split("{")[0].rstrip("/") if "{" in cleanup_path else cleanup_collection
        if not cc_base.startswith(pc.rstrip("/")) and not pc.rstrip("/").startswith(cc_base):
            return {"kind": "none", "detail": "identity_delete_collection_mismatch"}

    # Extract identity fields from cleanup path
    import re
    identity_fields = re.findall(r"\{(\w+)\}", cleanup_path)
    if not identity_fields:
        return {"kind": "none", "detail": "identity_delete_response_identity_unavailable"}

    authority_block = {
        "kind": "identity_delete",
        "operation_ref": cleanup_op_ref,
        "method": "DELETE",
        "path": cleanup_path,
        "source_refs": _list(cleanup_op.get("source_refs")),
        "authority_relation_ref": "",
    }
    identity_contract = {
        "identity_fields": identity_fields,
        "primary_identity_source": "primary_write_response",
        "cleanup_identity_targets": [f"path.{f}" for f in identity_fields],
        "same_entity_required": True,
        "identity_preservation_required": True,
    }
    return {
        "kind": "identity_delete",
        "detail": "",
        "authority_block": authority_block,
        "identity_contract": identity_contract,
        "before_observation_contract": {
            "required": True,
            "observer_kind": "collection_membership",
            "proof_semantics": "entity_absent",
        },
        "after_write_observation_contract": {
            "required": True,
            "observer_kind": "identity_read",
            "proof_semantics": "entity_present",
        },
        "after_cleanup_observation_contract": {
            "required": True,
            "observer_kind": "identity_read",
            "proof_semantics": "entity_absent",
        },
        "cleanup_request_contract": {
            "body_strategy": "none",
            "allowed_fields": [],
            "required_bindings": identity_fields,
        },
        "equivalence_contract": {
            "mode": "created_entity_absent",
            "identity_required": True,
            "compared_fields": [],
            "ignored_server_fields": [],
        },
    }


def _relation_endpoint_refs(rel: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Return (left/from refs, right/to refs) covering IR field aliases."""
    left = {
        _text(rel.get("source")),
        _text(rel.get("from")),
        _text(rel.get("from_ref")),
        _text(rel.get("source_operation_ref")),
        _text(rel.get("operation_ref")),
    }
    right = {
        _text(rel.get("target")),
        _text(rel.get("to")),
        _text(rel.get("to_ref")),
        _text(rel.get("target_operation_ref")),
    }
    left.discard("")
    right.discard("")
    return left, right


def _validate_explicit_compensator(
    *,
    primary_operation_ref: str,
    cleanup_op_ref: str,
    cleanup_op: dict[str, Any],
    cleanup_method: str,
    cleanup_path: str,
    relations: list[Any],
    ops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """SPEC §7.2: explicit_compensator requires source-declared relation."""
    # Must have explicit compensates relation in Behavior IR.
    # Canonical derivation stamps from_ref=compensator, to_ref=primary, but
    # historical rows also use from/to/source/target aliases — accept both
    # directions when the pair matches cleanup↔primary.
    has_relation = False
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if _text(rel.get("kind") or rel.get("relation_type")) not in {
            "compensates",
            "inverse",
            "compensation",
        }:
            continue
        left, right = _relation_endpoint_refs(rel)
        pair_ok = (
            (cleanup_op_ref in left and primary_operation_ref in right)
            or (primary_operation_ref in left and cleanup_op_ref in right)
        )
        if not pair_ok:
            # effects[].cleanup_target_operation_ref names the compensated primary
            for effect in _list(rel.get("effects")):
                if not isinstance(effect, dict):
                    continue
                target = _text(effect.get("cleanup_target_operation_ref"))
                if target == primary_operation_ref and cleanup_op_ref in left:
                    pair_ok = True
                    break
                if target == cleanup_op_ref and primary_operation_ref in left:
                    pair_ok = True
                    break
        if pair_ok:
            has_relation = True
            break
    if not has_relation:
        return {"kind": "none", "detail": "explicit_compensator_no_source_relation"}

    authority_block = {
        "kind": "explicit_compensator",
        "operation_ref": cleanup_op_ref,
        "method": cleanup_method,
        "path": cleanup_path,
        "source_refs": _list(cleanup_op.get("source_refs")),
        "authority_relation_ref": f"relation:{primary_operation_ref}:{cleanup_op_ref}",
    }
    return {
        "kind": "explicit_compensator",
        "detail": "",
        "authority_block": authority_block,
        "identity_contract": {
            "identity_fields": [],
            "primary_identity_source": "primary_request_or_response",
            "cleanup_identity_targets": [],
            "same_entity_required": True,
            "identity_preservation_required": True,
        },
        "before_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "state_before"},
        "after_write_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "state_changed"},
        "after_cleanup_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "state_restored"},
        "cleanup_request_contract": {"body_strategy": "from_source_schema", "allowed_fields": [], "required_bindings": []},
        "equivalence_contract": {"mode": "business_state_restored", "identity_required": True, "compared_fields": [], "ignored_server_fields": []},
    }


def _source_declared_writable_fields(
    primary_op: dict[str, Any],
    *,
    ops: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Writable field names declared on the operation (never entity-schema inference).

    PATCH/PUT docs often omit a body while the unique collection POST documents
    the same resource fields — reuse that source example, matching compile-time
    ``_source_request_example`` sibling resolution.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _add(field: Any) -> None:
        text = _text(field)
        key = text.lower()
        if not text or key in seen or key in SERVER_MANAGED_FIELDS:
            return
        seen.add(key)
        names.append(text)

    direct = primary_op.get("request_example")
    if isinstance(direct, dict):
        for key in direct:
            _add(key)

    # Nested OpenAPI request_schema.content.*.schema.properties
    request_schema = _dict(primary_op.get("request_schema"))
    properties = _dict(request_schema.get("properties"))
    if not properties:
        for media in _dict(request_schema.get("content")).values():
            if not isinstance(media, dict):
                continue
            properties = _dict(_dict(media.get("schema")).get("properties"))
            if properties:
                break
    for key in properties:
        _add(key)

    for key in (
        list(_list(primary_op.get("parameters")))
        + list(_list(primary_op.get("affected_fields")))
        + list(_list(primary_op.get("field_dictionary")))
    ):
        if isinstance(key, dict):
            _add(key.get("name") or key.get("field") or key.get("in"))
        else:
            _add(key)

    if names:
        return names

    # Sibling collection POST example (sku/qty on cart create → cart PATCH)
    try:
        from .experiment_compiler_support import _source_request_example
    except Exception:
        return names
    sibling_example = _source_request_example(
        primary_op,
        sibling_operations=list(_dict(ops).values()) if ops else None,
    )
    if isinstance(sibling_example, dict):
        for key in sibling_example:
            _add(key)
    return names


def _validate_field_snapshot_restore(
    *,
    primary_method: str,
    primary_path: str,
    primary_operation_ref: str,
    cleanup_op: dict[str, Any],
    cleanup_method: str,
    cleanup_path: str,
    experiment: dict[str, Any],
    ops: dict[str, dict[str, Any]],
    entities: list[Any] | None = None,
    relations: list[Any] | None = None,
) -> dict[str, Any]:
    """SPEC §7.3: field_snapshot_restore semantic validation."""
    # Empty body action POST cannot be snapshot_restore
    primary_op = _dict(ops.get(primary_operation_ref))
    restore_fields = _source_declared_writable_fields(primary_op, ops=ops)
    body_is_empty = not restore_fields

    if primary_method == "POST" and "{" in primary_path and body_is_empty:
        return {"kind": "none", "detail": "empty_body_action_without_explicit_inverse"}

    # Must have non-empty body for field restore
    if body_is_empty and primary_method == "POST":
        return {"kind": "none", "detail": "empty_body_action_without_explicit_inverse"}

    # Method must be PUT/PATCH or POST with field body
    if primary_method not in {"PUT", "PATCH", "POST"}:
        return {"kind": "none", "detail": "field_snapshot_restore_method_invalid"}

    if not restore_fields:
        return {"kind": "none", "detail": "field_snapshot_restore_no_writable_fields"}

    authority_block = {
        "kind": "field_snapshot_restore",
        "operation_ref": primary_operation_ref,
        "method": primary_method,
        "path": cleanup_path or primary_path,
        "source_refs": _list(primary_op.get("source_refs")),
        "authority_relation_ref": "",
    }
    return {
        "kind": "field_snapshot_restore",
        "detail": "",
        "authority_block": authority_block,
        "identity_contract": {
            "identity_fields": [],
            "primary_identity_source": "path_identity",
            "cleanup_identity_targets": ["path_identity"],
            "same_entity_required": True,
            "identity_preservation_required": True,
        },
        "before_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "field_values_before"},
        "after_write_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "field_values_changed"},
        "after_cleanup_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "field_values_restored"},
        "cleanup_request_contract": {
            "body_strategy": "before_snapshot_fields",
            "allowed_fields": restore_fields,
            "required_bindings": [],
        },
        "equivalence_contract": {
            "mode": "field_comparison",
            "identity_required": True,
            "compared_fields": restore_fields,
            "ignored_server_fields": sorted(SERVER_MANAGED_FIELDS),
        },
    }


def _validate_inverse_delta(
    *,
    primary_operation_ref: str,
    cleanup_op_ref: str,
    experiment: dict[str, Any],
    ops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """SPEC §7.4: inverse_delta semantic validation."""
    primary_op = _dict(ops.get(primary_operation_ref))
    request_example = _dict(primary_op.get("request_example"))

    # Find delta field
    delta_fields = [
        k for k, v in request_example.items()
        if isinstance(v, (int, float)) and "delta" in k.lower()
    ]
    if not delta_fields:
        return {"kind": "none", "detail": "inverse_delta_no_numeric_delta_field"}

    authority_block = {
        "kind": "inverse_delta",
        "operation_ref": cleanup_op_ref or primary_operation_ref,
        "method": _text(primary_op.get("method")).upper(),
        "path": _text(primary_op.get("path") or primary_op.get("raw_path")),
        "source_refs": _list(primary_op.get("source_refs")),
        "authority_relation_ref": "",
    }
    return {
        "kind": "inverse_delta",
        "detail": "",
        "authority_block": authority_block,
        "identity_contract": {"identity_fields": [], "primary_identity_source": "request_body", "cleanup_identity_targets": ["request_body"], "same_entity_required": True, "identity_preservation_required": True},
        "before_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "conservation_before"},
        "after_write_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "conservation_after_write"},
        "after_cleanup_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "conservation_restored"},
        "cleanup_request_contract": {"body_strategy": "inverse_delta", "allowed_fields": delta_fields, "required_bindings": []},
        "equivalence_contract": {"mode": "conservation_check", "identity_required": True, "compared_fields": delta_fields, "ignored_server_fields": []},
    }


def _validate_exact_recreate(
    *,
    primary_method: str,
    primary_path: str,
    cleanup_op_ref: str,
    cleanup_op: dict[str, Any],
    experiment: dict[str, Any],
    ops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """SPEC §7.5: exact_recreate — highest risk, default blocked."""
    # Primary must be DELETE
    if primary_method != "DELETE":
        return {"kind": "none", "detail": "exact_recreate_primary_not_delete"}

    # Must have explicit source declaration allowing recreate. Compiler sets
    # business_equivalence_allows_new_identity when DELETE binds a unique
    # source-declared collection create with a request body. Also admit when
    # the recreate operation itself carries a source request example/schema —
    # identity may change; business fields are restored from that body.
    safety = _dict(experiment.get("safety_contract"))
    recreate_op = _dict(cleanup_op) or _dict(ops.get(cleanup_op_ref))
    recreate_body = _source_declared_writable_fields(recreate_op, ops=ops)
    allows_new_identity = bool(
        safety.get("business_equivalence_allows_new_identity") or recreate_body
    )
    if not allows_new_identity:
        # Default: identity changed → invalid
        return {"kind": "none", "detail": "exact_recreate_identity_not_preserved"}

    authority_block = {
        "kind": "exact_recreate",
        "operation_ref": cleanup_op_ref,
        "method": _text(cleanup_op.get("method")).upper(),
        "path": _text(cleanup_op.get("path") or cleanup_op.get("raw_path")),
        "source_refs": _list(cleanup_op.get("source_refs")),
        "authority_relation_ref": "",
    }
    return {
        "kind": "exact_recreate",
        "detail": "",
        "authority_block": authority_block,
        "identity_contract": {"identity_fields": [], "primary_identity_source": "before_snapshot", "cleanup_identity_targets": ["recreate_body"], "same_entity_required": False, "identity_preservation_required": False},
        "before_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "full_entity_snapshot"},
        "after_write_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "entity_absent"},
        "after_cleanup_observation_contract": {"required": True, "observer_kind": "entity_read", "proof_semantics": "entity_restored"},
        "cleanup_request_contract": {"body_strategy": "full_snapshot_restore", "allowed_fields": [], "required_bindings": []},
        "equivalence_contract": {"mode": "full_entity_comparison", "identity_required": False, "compared_fields": [], "ignored_server_fields": sorted(SERVER_MANAGED_FIELDS)},
    }


def _validate_environment_reset(*, experiment: dict[str, Any]) -> dict[str, Any]:
    """SPEC §7.6: verified_environment_reset — requires explicit contract."""
    reset_contract = _dict(experiment.get("environment_reset_contract"))
    if not reset_contract.get("verified"):
        return {"kind": "none", "detail": "environment_reset_contract_unverified"}
    if _text(reset_contract.get("scope")) != "per_experiment":
        return {"kind": "none", "detail": "environment_reset_scope_not_per_experiment"}

    authority_block = {
        "kind": "verified_environment_reset",
        "operation_ref": _text(reset_contract.get("reset_operation_ref")),
        "method": "",
        "path": "",
        "source_refs": [],
        "authority_relation_ref": "",
    }
    return {
        "kind": "verified_environment_reset",
        "detail": "",
        "authority_block": authority_block,
        "identity_contract": {},
        "before_observation_contract": {"required": True, "observer_kind": "environment_snapshot", "proof_semantics": "environment_before"},
        "after_write_observation_contract": {"required": False},
        "after_cleanup_observation_contract": {"required": True, "observer_kind": "environment_snapshot", "proof_semantics": "environment_restored"},
        "cleanup_request_contract": {"body_strategy": "adapter_managed", "allowed_fields": [], "required_bindings": []},
        "equivalence_contract": {"mode": "environment_fingerprint_comparison", "identity_required": False, "compared_fields": [], "ignored_server_fields": []},
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _validate_restore_deleted_resource(
    *,
    primary_method: str,
    primary_path: str,
    cleanup_op_ref: str,
    cleanup_op: dict[str, Any],
    experiment: dict[str, Any],
    ops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """restore_deleted_resource — same-path restore write on the deleted row.

    A soft delete keeps the row in the target: the compensation is the
    source-declared PATCH/PUT on the deleted resource itself, which restores
    the row in place (identity preserved — unlike a recreate, which produces
    a new row). The restore body must come from the source example; the
    harness never invents fields for a restore it cannot ground.
    """
    if primary_method != "DELETE":
        return {"kind": "none", "detail": "restore_deleted_primary_not_delete"}
    restore_op = _dict(cleanup_op) or _dict(ops.get(cleanup_op_ref))
    restore_method = _text(restore_op.get("method")).upper()
    if restore_method not in {"PATCH", "PUT"}:
        return {
            "kind": "none",
            "detail": "restore_deleted_compensator_not_restore_write",
        }
    if not _source_declared_writable_fields(restore_op, ops=ops):
        return {
            "kind": "none",
            "detail": "restore_deleted_body_not_source_declared",
        }
    authority_block = {
        "kind": "restore_deleted_resource",
        "operation_ref": cleanup_op_ref,
        "method": restore_method,
        "path": _text(restore_op.get("path") or restore_op.get("raw_path")),
        "source_refs": _list(restore_op.get("source_refs")),
        "authority_relation_ref": "",
    }
    return {
        "kind": "restore_deleted_resource",
        "detail": "",
        "authority_block": authority_block,
        "identity_contract": {
            "identity_fields": [],
            "primary_identity_source": "cleanup_path_binding",
            "cleanup_identity_targets": [],
            "same_entity_required": True,
            "identity_preservation_required": True,
        },
        "before_observation_contract": {
            "required": True,
            "observer_kind": "entity_read",
            "proof_semantics": "full_entity_snapshot",
        },
        "after_write_observation_contract": {
            "required": True,
            "observer_kind": "entity_read",
            "proof_semantics": "entity_absent",
        },
        "after_cleanup_observation_contract": {
            "required": True,
            "observer_kind": "entity_read",
            "proof_semantics": "entity_restored",
        },
        "cleanup_request_contract": {
            "body_strategy": "source_example_restore",
            "allowed_fields": [],
            "required_bindings": [],
        },
        "equivalence_contract": {
            "mode": "full_entity_comparison",
            "identity_required": True,
            "compared_fields": [],
            "ignored_server_fields": sorted(SERVER_MANAGED_FIELDS),
        },
    }


def _nr_reason_detail(method: str, path: str, cleanup_plan: list[dict[str, Any]]) -> str:
    """Generate a specific reason detail for non-reversible writes."""
    if method == "POST" and "{" in path:
        return "empty_body_action_without_explicit_inverse"
    if method == "POST" and "{" not in path:
        return "domain_action_without_cleanup_authority"
    if method == "DELETE":
        return "delete_without_recreate_proof"
    return "cleanup_authority_unresolved"


def _proof_id(op_ref: str, method: str, path: str, authority: str) -> str:
    """Content-addressed proof identity."""
    content = f"{op_ref}:{method}:{path}:{authority}"
    return "wrp_" + hashlib.sha256(content.encode()).hexdigest()[:24]


def _sha256_stable(obj: Any) -> str:
    """Stable SHA256 of a JSON-serializable object."""
    if obj is None:
        return ""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _relation_mentions_operation(
    relation: dict[str, Any], operation_refs: set[str]
) -> bool:
    """Keep only source relations that can affect this write proof.

    Runtime Behavior IR may append unrelated source links or observed
    relationships after an experiment is compiled. Hashing the whole IR made
    those legitimate additions look like cleanup-contract drift. The proof
    still binds every operation/relation that can classify its primary write or
    compensator; unrelated graph growth is intentionally outside this proof's
    authority.
    """
    for key in (
        "operation_ref",
        "op_ref",
        "source",
        "target",
        "from",
        "to",
        "source_ref",
        "target_ref",
        "from_ref",
        "to_ref",
        "source_operation_ref",
        "target_operation_ref",
    ):
        value = relation.get(key)
        if isinstance(value, list):
            if any(_text(item) in operation_refs for item in value):
                return True
        elif _text(value) in operation_refs:
            return True
    return False


def _build_compile_context(
    ir: dict[str, Any],
    experiment: dict[str, Any],
    *,
    primary_operation_ref: str = "",
    cleanup_plan: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a proof context from the exact source graph used by cleanup.

    The full Behavior IR is allowed to grow during runtime expansion. A write
    proof must remain sensitive to changes in its own operation and cleanup
    authority, but not to unrelated operations/relations appended for another
    obligation. This projection is the single source of truth for that scope.
    """
    operation_refs = {
        _text(primary_operation_ref),
        *(
            _text(row.get("operation_ref"))
            for phase in ("control_plan", "treatment_plan", "precondition_plan")
            for row in _list(experiment.get(phase))
            if isinstance(row, dict)
        ),
        *(
            _text(row.get("operation_ref"))
            for row in _list(cleanup_plan)
            if isinstance(row, dict)
        ),
    }
    operation_refs.discard("")
    all_operations = _list(ir.get("operations"))
    all_relations = _list(ir.get("relations"))
    if operation_refs:
        operations = [
            row
            for row in all_operations
            if isinstance(row, dict)
            and _text(row.get("id")) in operation_refs
        ]
        relations = [
            row
            for row in all_relations
            if isinstance(row, dict)
            and _relation_mentions_operation(row, operation_refs)
        ]
    else:
        # Direct callers without an operation identity retain the old
        # fail-closed context rather than silently hashing an empty graph.
        operations = all_operations
        relations = all_relations
    return {
        "behavior_ir_fingerprint": _sha256_stable(
            operations + relations
        ),
        "experiment_semantic_fingerprint": _sha256_stable({
            "obligation_id": experiment.get("obligation_id"),
            "risk_family": experiment.get("risk_family"),
        }),
        "policy_version": "",
        "compiler_version": "v1.1",
    }
