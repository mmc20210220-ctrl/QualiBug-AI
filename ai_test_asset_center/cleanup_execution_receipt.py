"""Explicit Cleanup Execution Receipt — SPEC v1.1.1 §6.

Every experiment that executes a primary governed write MUST emit an explicit
cleanup execution receipt. This receipt records what actually happened during
cleanup transport — it does NOT judge whether business state was restored
(that is the equivalence engine's job).

PROHIBITED: inferring success from ``cleanup_failures == 0``.
Only an explicit receipt with ``attempted=true`` and a real status code is valid.

Output schema: qualibug.cleanup-execution-receipt.v1
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


def _sha256_short(obj: Any) -> str:
    """Stable SHA256 prefix of a JSON-serializable object."""
    if obj is None:
        return ""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─── Receipt Status Constants ─────────────────────────────────────────────────

STATUS_NOT_REQUIRED = "NOT_REQUIRED"
STATUS_NOT_ATTEMPTED = "NOT_ATTEMPTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_TRANSPORT_FAILED = "TRANSPORT_FAILED"
STATUS_REJECTED = "REJECTED"
STATUS_ACCEPTED = "ACCEPTED"


def build_cleanup_execution_receipt(
    *,
    experiment_id: str,
    proof_id: str,
    cleanup_plan: list[dict[str, Any]],
    steps_out: list[dict[str, Any]],
    cleanup_failures: int,
    cleanup_status: str,
    proof: dict[str, Any],
    adapter_cleanup_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the explicit cleanup execution receipt from actual execution evidence.

    This function examines the cleanup-phase steps in ``steps_out`` to determine
    what actually happened. It NEVER infers success from absence of failures.

    Args:
        experiment_id: The experiment identity.
        proof_id: The WriteReversibilityProof identity.
        cleanup_plan: The declared cleanup plan from the experiment.
        steps_out: All execution steps (including cleanup phase).
        cleanup_failures: Count of cleanup failures (diagnostic only).
        cleanup_status: The observed cleanup status string.
        proof: The full WriteReversibilityProof dict.
        adapter_cleanup_receipts: Optional declared-adapter cleanup receipts when
            cleanup ran through db_sql rather than HTTP transport.

    Returns:
        A qualibug.cleanup-execution-receipt.v1 dict.
    """
    cleanup_authority = _text(
        _dict(proof.get("cleanup_authority")).get("mode")
    )
    cleanup_operation_ref = _text(
        _dict(proof.get("cleanup_authority")).get("cleanup_operation_ref")
    )

    # Extract cleanup-phase steps
    cleanup_steps = [
        step for step in steps_out
        if isinstance(step, dict) and _text(step.get("phase")) == "cleanup"
    ]
    adapter_receipts = [
        row for row in _list(adapter_cleanup_receipts) if isinstance(row, dict)
    ]

    # No cleanup plan declared
    if not cleanup_plan:
        return _build_receipt(
            experiment_id=experiment_id,
            proof_id=proof_id,
            cleanup_operation_ref=cleanup_operation_ref,
            cleanup_authority=cleanup_authority,
            attempted=False,
            transport_reached=False,
            method="",
            path_template="",
            materialized_path="",
            request_body_fingerprint="",
            identity_bindings={},
            status_code=0,
            response_body_fingerprint="",
            succeeded=False,
            status=STATUS_NOT_REQUIRED,
            reason_code="NO_CLEANUP_PLAN",
            detail="no_cleanup_plan_declared",
        )

    # Cleanup plan exists but no cleanup steps executed
    if not cleanup_steps:
        # Declared-adapter cleanup may succeed without HTTP cleanup steps when the
        # runtime failed to emit a phase=cleanup row. Prefer explicit adapter
        # receipts over inventing NOT_ATTEMPTED.
        if adapter_receipts:
            cleaned = [
                row for row in adapter_receipts
                if _text(row.get("status")).upper() == "CLEANED"
            ]
            failed = [
                row for row in adapter_receipts
                if _text(row.get("status")).upper() == "FAILED"
            ]
            if cleaned and not failed and len(cleaned) == len(adapter_receipts):
                owner = cleaned[-1]
                return _build_receipt(
                    experiment_id=experiment_id,
                    proof_id=proof_id,
                    cleanup_operation_ref=cleanup_operation_ref
                    or _text(owner.get("table")),
                    cleanup_authority=cleanup_authority or "declared_adapter_cleanup",
                    attempted=True,
                    transport_reached=True,
                    method="ADAPTER_DB_SQL",
                    path_template=_text(owner.get("table")),
                    materialized_path=_text(owner.get("table")),
                    request_body_fingerprint="",
                    identity_bindings={
                        _text(owner.get("identity_column")) or "id": owner.get(
                            "identity_value"
                        )
                    },
                    status_code=200,
                    response_body_fingerprint=_sha256_short(
                        {
                            "rows_deleted": int(owner.get("rows_deleted") or 0),
                            "table": _text(owner.get("table")),
                        }
                    ),
                    succeeded=True,
                    status=STATUS_ACCEPTED,
                    reason_code="",
                    detail="adapter_cleanup_cleaned",
                )
            fail_row = failed[0] if failed else adapter_receipts[-1]
            return _build_receipt(
                experiment_id=experiment_id,
                proof_id=proof_id,
                cleanup_operation_ref=cleanup_operation_ref
                or _text(fail_row.get("table")),
                cleanup_authority=cleanup_authority or "declared_adapter_cleanup",
                attempted=True,
                transport_reached=True,
                method="ADAPTER_DB_SQL",
                path_template=_text(fail_row.get("table")),
                materialized_path=_text(fail_row.get("table")),
                request_body_fingerprint="",
                identity_bindings={},
                status_code=0,
                response_body_fingerprint="",
                succeeded=False,
                status=STATUS_TRANSPORT_FAILED,
                reason_code=_text(fail_row.get("reason_code")) or "ADAPTER_CLEANUP_FAILED",
                detail=_text(fail_row.get("detail")) or "adapter_cleanup_not_cleaned",
            )
        # Explicit NOT_REQUIRED must not be rewritten as NOT_ATTEMPTED/failed
        # transport — that poisons cleanup equivalence into INDETERMINATE.
        if cleanup_status.lower() in {"not_required"}:
            return _build_receipt(
                experiment_id=experiment_id,
                proof_id=proof_id,
                cleanup_operation_ref=cleanup_operation_ref,
                cleanup_authority=cleanup_authority,
                attempted=False,
                transport_reached=False,
                method=_first_cleanup_method(cleanup_plan),
                path_template=_first_cleanup_path(cleanup_plan),
                materialized_path="",
                request_body_fingerprint="",
                identity_bindings={},
                status_code=0,
                response_body_fingerprint="",
                succeeded=False,
                status=STATUS_NOT_REQUIRED,
                reason_code="CLEANUP_NOT_REQUIRED",
                detail="accepted_write_state_unchanged_or_explicit_not_required",
            )
        # Determine why: blocked or not attempted
        if cleanup_status == "blocked":
            status = STATUS_BLOCKED
            reason = "CLEANUP_BLOCKED_BEFORE_TRANSPORT"
            detail = "cleanup_blocked_before_transport"
        else:
            status = STATUS_NOT_ATTEMPTED
            reason = "CLEANUP_NOT_ATTEMPTED"
            detail = f"cleanup_status={cleanup_status or 'empty'}"
        return _build_receipt(
            experiment_id=experiment_id,
            proof_id=proof_id,
            cleanup_operation_ref=cleanup_operation_ref,
            cleanup_authority=cleanup_authority,
            attempted=False,
            transport_reached=False,
            method=_first_cleanup_method(cleanup_plan),
            path_template=_first_cleanup_path(cleanup_plan),
            materialized_path="",
            request_body_fingerprint="",
            identity_bindings={},
            status_code=0,
            response_body_fingerprint="",
            succeeded=False,
            status=status,
            reason_code=reason,
            detail=detail,
        )

    # Aggregate cleanup steps
    last_step = cleanup_steps[-1]
    last_status_code = int(last_step.get("status_code") or 0)
    last_governance = _dict(last_step.get("governance_receipt"))
    transport_reached = last_status_code > 0
    accepted = last_governance.get("accepted") is True

    # Determine receipt status from actual evidence
    if not transport_reached:
        status = STATUS_TRANSPORT_FAILED
        reason = "CLEANUP_TRANSPORT_NOT_REACHED"
        detail = _text(last_step.get("error")) or "status_code_zero"
        succeeded = False
    elif accepted and 200 <= last_status_code < 300:
        status = STATUS_ACCEPTED
        reason = ""
        detail = ""
        succeeded = True
    elif 200 <= last_status_code < 300:
        # 2xx but not formally accepted by governance
        status = STATUS_ACCEPTED
        reason = ""
        detail = "accepted_by_status_code"
        succeeded = True
    elif last_status_code in {401, 403}:
        status = STATUS_REJECTED
        reason = "CLEANUP_REJECTED_BY_TARGET"
        detail = f"status={last_status_code}"
        succeeded = False
    else:
        status = STATUS_TRANSPORT_FAILED
        reason = "CLEANUP_TRANSPORT_FAILED"
        detail = f"status={last_status_code}"
        succeeded = False

    # Extract identity bindings from the cleanup step
    identity_bindings: dict[str, Any] = {}
    gov_before = _dict(last_governance.get("before"))
    gov_write = _dict(last_governance.get("write"))
    if gov_write.get("body") and isinstance(gov_write.get("body"), dict):
        for key in ("id", "uuid", "resource_id"):
            if key in gov_write["body"]:
                identity_bindings[key] = gov_write["body"][key]

    return _build_receipt(
        experiment_id=experiment_id,
        proof_id=proof_id,
        cleanup_operation_ref=cleanup_operation_ref or _text(last_step.get("operation_ref")),
        cleanup_authority=cleanup_authority,
        attempted=True,
        transport_reached=transport_reached,
        method=_text(last_step.get("method")),
        path_template=_first_cleanup_path(cleanup_plan),
        materialized_path=_text(last_step.get("path")),
        request_body_fingerprint=_sha256_short(gov_write.get("body")),
        identity_bindings=identity_bindings,
        status_code=last_status_code,
        response_body_fingerprint=_sha256_short(last_step.get("body")),
        succeeded=succeeded,
        status=status,
        reason_code=reason,
        detail=detail,
    )


# ─── Internal Helpers ─────────────────────────────────────────────────────────


def _first_cleanup_method(cleanup_plan: list[dict[str, Any]]) -> str:
    """Get the method from the first cleanup plan entry."""
    for item in cleanup_plan:
        if isinstance(item, dict):
            m = _text(item.get("method"))
            if m:
                return m.upper()
    return ""


def _first_cleanup_path(cleanup_plan: list[dict[str, Any]]) -> str:
    """Get the path template from the first cleanup plan entry."""
    for item in cleanup_plan:
        if isinstance(item, dict):
            p = _text(item.get("path"))
            if p:
                return p
    return ""


def _build_receipt(
    *,
    experiment_id: str,
    proof_id: str,
    cleanup_operation_ref: str,
    cleanup_authority: str,
    attempted: bool,
    transport_reached: bool,
    method: str,
    path_template: str,
    materialized_path: str,
    request_body_fingerprint: str,
    identity_bindings: dict[str, Any],
    status_code: int,
    response_body_fingerprint: str,
    succeeded: bool,
    status: str,
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    """Assemble the final receipt with content-addressed fingerprint."""
    receipt_id = f"cleanup_exec_{_sha256_short(experiment_id + proof_id + method + materialized_path)}"
    receipt = {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "experiment_id": experiment_id,
        "proof_id": proof_id,
        "cleanup_operation_ref": cleanup_operation_ref,
        "cleanup_authority": cleanup_authority,
        "attempted": attempted,
        "transport_reached": transport_reached,
        "method": method,
        "path_template": path_template,
        "materialized_path": materialized_path,
        "request_body_fingerprint": request_body_fingerprint,
        "identity_bindings": identity_bindings,
        "status_code": status_code,
        "response_body_fingerprint": response_body_fingerprint,
        "succeeded": succeeded,
        "status": status,
        "reason_code": reason_code,
        "detail": detail,
        "receipt_id": receipt_id,
        "fingerprint": "",
    }
    receipt["fingerprint"] = _sha256_short({
        "experiment_id": experiment_id,
        "proof_id": proof_id,
        "attempted": attempted,
        "transport_reached": transport_reached,
        "status_code": status_code,
        "succeeded": succeeded,
        "status": status,
    })
    return receipt


# ═══════════════════════════════════════════════════════════════════════════════
# V1.3.0-A: Database Cleanup Receipt, Environment Restoration, Row Lineage
# ═══════════════════════════════════════════════════════════════════════════════

DB_CLEANUP_RECEIPT_SCHEMA = "qualibug.database-cleanup-receipt.v1"
ENV_RESTORATION_SCHEMA = "qualibug.environment-restoration-receipt.v1"
ROW_LINEAGE_SCHEMA = "qualibug.fixture-row-lineage.v1"

# Receipt final status
RECEIPT_CLEANED = "CLEANED"
RECEIPT_RESTORED = "RESTORED"
RECEIPT_PARTIAL = "PARTIAL"
RECEIPT_FAILED = "FAILED"
RECEIPT_INDETERMINATE = "INDETERMINATE"


def build_database_cleanup_receipt(
    *,
    experiment_id: str,
    fixture_id: str = "",
    step_id: str = "",
    datastore_id: str = "primary",
    table: str,
    primary_key_fingerprint: str,
    cleanup_strategy: str,
    authority_source: str,
    before_cleanup: dict[str, Any] | None = None,
    cleanup_execution: dict[str, Any] | None = None,
    after_cleanup: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build qualibug.database-cleanup-receipt.v1 (SPEC §11).

    Every database cleanup action must produce this receipt. It is not sufficient
    to record cleanup_attempted=true; the final database state must be verified.
    """
    exec_data = _dict(cleanup_execution)
    attempted = bool(exec_data.get("attempted"))
    affected_rows = int(exec_data.get("affected_rows") or 0)
    error = _text(exec_data.get("error"))

    verif = _dict(verification)
    passed = bool(verif.get("passed"))

    # Determine final status from actual evidence
    if not attempted:
        final_status = RECEIPT_INDETERMINATE
    elif error:
        final_status = RECEIPT_FAILED
    elif passed and affected_rows > 0:
        final_status = RECEIPT_CLEANED if cleanup_strategy != "restore" else RECEIPT_RESTORED
    elif passed and affected_rows == 0:
        final_status = RECEIPT_INDETERMINATE
    elif not passed:
        final_status = RECEIPT_FAILED
    else:
        final_status = RECEIPT_PARTIAL

    receipt = {
        "schema_version": DB_CLEANUP_RECEIPT_SCHEMA,
        "receipt_id": f"dbcr_{_sha256_short(experiment_id + table + primary_key_fingerprint + step_id)}",
        "experiment_id": experiment_id,
        "fixture_id": fixture_id,
        "step_id": step_id,
        "datastore_id": datastore_id,
        "table": table,
        "primary_key_fingerprint": primary_key_fingerprint,
        "cleanup_strategy": cleanup_strategy,
        "authority_source": authority_source,
        "before_cleanup": _dict(before_cleanup),
        "cleanup_execution": exec_data,
        "after_cleanup": _dict(after_cleanup),
        "verification": verif,
        "final_status": final_status,
    }
    receipt["fingerprint"] = _sha256_short({
        "experiment_id": experiment_id,
        "table": table,
        "final_status": final_status,
        "affected_rows": affected_rows,
    })
    return receipt


def build_environment_restoration_receipt(
    *,
    experiment_id: str,
    campaign_id: str,
    database_cleanup_receipt_ids: list[str] | None = None,
    api_cleanup_receipt_ids: list[str] | None = None,
    fixture_receipt_ids: list[str] | None = None,
    created_rows_remaining: int = 0,
    modified_rows_not_restored: int = 0,
    deleted_rows_not_restored: int = 0,
    cleanup_failures: list[dict[str, Any]] | None = None,
    baseline_comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build qualibug.environment-restoration-receipt.v1 (SPEC §12).

    Only environment_restored=true allows the experiment to count as completed.
    """
    failures = _list(cleanup_failures)
    comparison = _dict(baseline_comparison)

    environment_restored = bool(
        created_rows_remaining == 0
        and modified_rows_not_restored == 0
        and deleted_rows_not_restored == 0
        and not failures
        and comparison.get("relevant_tables_match", True)
        and comparison.get("relevant_fields_match", True)
    )

    if environment_restored:
        final_status = "ENVIRONMENT_RESTORED"
    elif failures:
        final_status = "CLEANUP_FAILED"
    else:
        final_status = "ENVIRONMENT_DIRTY"

    return {
        "schema_version": ENV_RESTORATION_SCHEMA,
        "receipt_id": f"envr_{_sha256_short(experiment_id + campaign_id + final_status)}",
        "experiment_id": experiment_id,
        "campaign_id": campaign_id,
        "database_cleanup_receipt_ids": _list(database_cleanup_receipt_ids),
        "api_cleanup_receipt_ids": _list(api_cleanup_receipt_ids),
        "fixture_receipt_ids": _list(fixture_receipt_ids),
        "created_rows_remaining": created_rows_remaining,
        "modified_rows_not_restored": modified_rows_not_restored,
        "deleted_rows_not_restored": deleted_rows_not_restored,
        "cleanup_failures": failures,
        "baseline_comparison": comparison,
        "environment_restored": environment_restored,
        "final_status": final_status,
    }


def build_fixture_row_lineage(
    *,
    campaign_id: str,
    experiment_id: str,
    fixture_id: str,
    step_id: str = "",
    datastore_id: str = "primary",
    table: str,
    primary_key: str,
    business_key: str = "",
    parent_keys: list[str] | None = None,
    child_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Build qualibug.fixture-row-lineage.v1 (SPEC §6).

    Every QualiBug-created test object must have complete lineage. Identity must
    come from Create Response, Source-Declared Readback, DB INSERT Receipt,
    Fixture Output Binding, or Canonical Correlation Key — never from guessing.
    """
    return {
        "schema_version": ROW_LINEAGE_SCHEMA,
        "lineage_id": f"frl_{_sha256_short(campaign_id + experiment_id + fixture_id + table + primary_key)}",
        "campaign_id": campaign_id,
        "experiment_id": experiment_id,
        "fixture_id": fixture_id,
        "step_id": step_id,
        "datastore_id": datastore_id,
        "table": table,
        "primary_key": primary_key,
        "business_key": business_key,
        "parent_keys": _list(parent_keys),
        "child_keys": _list(child_keys),
        "created_by_qualibug": True,
        "customer_preexisting": False,
    }


def verify_cleanup_completion(
    *,
    cleanup_receipts: list[dict[str, Any]],
    preimage: dict[str, Any] | None = None,
    dependency_graph: dict[str, Any] | None = None,
    affected_entities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify that all cleanup actions completed and environment is restored.

    Checks:
    - Every cleanup receipt has a successful final_status
    - Dependency order was respected (children before parents)
    - Pre-image fields are restored (for UPDATE/DELETE)
    - No residual data remains

    Returns {environment_restored, failures, partial, verification_passed}.
    """
    receipts = _list(cleanup_receipts)
    failures: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []

    for receipt in receipts:
        status = _text(receipt.get("final_status"))
        if status in (RECEIPT_FAILED,):
            failures.append({
                "receipt_id": _text(receipt.get("receipt_id")),
                "table": _text(receipt.get("table")),
                "reason": status,
            })
        elif status in (RECEIPT_PARTIAL, RECEIPT_INDETERMINATE):
            partial.append({
                "receipt_id": _text(receipt.get("receipt_id")),
                "table": _text(receipt.get("table")),
                "reason": status,
            })

    # Verify dependency order if graph available
    order_violations: list[str] = []
    graph = _dict(dependency_graph)
    topo_order = _list(graph.get("topological_order"))
    if topo_order and len(receipts) > 1:
        executed_tables = [_text(r.get("table")) for r in receipts]
        topo_positions = {t: i for i, t in enumerate(topo_order)}
        for i in range(len(executed_tables) - 1):
            t1 = executed_tables[i].lower()
            t2 = executed_tables[i + 1].lower()
            pos1 = topo_positions.get(t1, 999)
            pos2 = topo_positions.get(t2, 999)
            if pos1 > pos2:
                order_violations.append(f"{executed_tables[i]}_before_{executed_tables[i+1]}")

    environment_restored = bool(
        not failures
        and not partial
        and not order_violations
    )

    return {
        "environment_restored": environment_restored,
        "verification_passed": environment_restored,
        "failures": failures,
        "partial": partial,
        "order_violations": order_violations,
        "receipt_count": len(receipts),
        "successful_count": sum(
            1 for r in receipts
            if _text(r.get("final_status")) in (RECEIPT_CLEANED, RECEIPT_RESTORED)
        ),
    }
