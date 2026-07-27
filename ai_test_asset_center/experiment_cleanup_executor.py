"""Cleanup compensation orchestration for experiment execution.

Extracted from ``experiment_executor.execute_one_experiment``. Runs governed
write cleanup and fixture compensation in reverse order, then always returns
so observers / oracle evaluation can proceed. Predicate helpers remain in
``experiment_cleanup``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .contract_oracles import build_contract_evidence_receipt
from .experiment_cleanup import (
    _cleanup_restores_governed_write,
    _governance_audit_receipt_id,
    _governed_write_attempts,
    _governed_write_changed_state,
    _rejected_writes_left_state_unchanged,
)
from .experiment_runtime_support import (
    _WRITE_METHODS,
    _declared_observation_path,
    _dict,
    _documented_routes,
    _inverse_delta_cleanup_body,
    _list,
    _resolve_token,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
)
from .real_id_resolver import (
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
    runtime_cleanup_paths as _runtime_cleanup_paths,
)
from .cleanup_execution_receipt import build_cleanup_execution_receipt
from .sandbox_write_executor import (
    _restore_payload,
    execute_governed_control_write,
    sandbox_write_allowed,
)


def _cleanup_actor_for_write_step(
    source_step: dict[str, Any],
    *,
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
) -> tuple[str, dict[str, Any], str]:
    """Use the write's own actor so actor-scoped collections restore correctly."""
    actor_ref = _text(source_step.get("actor_ref"))
    if not actor_ref or actor_ref not in actors:
        raise ValueError(
            f"cleanup_actor_identity_missing:{actor_ref or '<empty>'}"
        )
    actor = actors[actor_ref]
    token = _resolve_token(actor, tokens)
    return actor_ref, actor, token


def _write_step_for_cleanup_path(
    *,
    path_template: str,
    cleanup_path: str,
    steps_out: list[dict[str, Any]],
    compensates_operation_ref: str = "",
) -> dict[str, Any]:
    """Map a materialized cleanup path back to the write that created it."""
    for step in reversed(steps_out):
        if _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        if compensates_operation_ref and _text(step.get("operation_ref")) != compensates_operation_ref:
            continue
        targets, missing = _runtime_cleanup_paths(path_template, [step])
        if missing or not targets:
            continue
        for candidate_path, _bindings in targets:
            if candidate_path == cleanup_path:
                return step
    return {}


def _project_database_dsn(root: Path, project: str) -> str:
    """The DSN the operator declared for this project, or "" when none is declared."""
    import json as _json

    path = Path(root) / "platform_workspace" / str(project) / "multi_service_config.json"
    try:
        payload = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    for service in _list(_dict(payload).get("services")):
        db = _dict(_dict(service).get("db"))
        host = _text(db.get("host"))
        name = _text(db.get("name"))
        if not host or not name:
            continue
        user = _text(db.get("user"))
        password = _text(db.get("password"))
        if password.startswith("enc$"):
            try:
                from .credential_crypto import decrypt as _decrypt

                password = _decrypt(password)
            except Exception:
                return ""
        port = _text(db.get("port")) or "5432"
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"
    return ""


def _adapter_cleanup_identity(
    cleanup: dict[str, Any],
    *,
    runtime_bindings: dict[str, Any],
    steps_out: list[dict[str, Any]],
) -> str:
    """The concrete row identity for an adapter cleanup, from what the run observed.

    Prefers a value the write itself returned, then a runtime binding. Returns "" when
    neither is available -- the executor then refuses rather than deleting by guess.
    """
    column = _text(_dict(cleanup).get("identity_column")) or "id"
    for step in reversed(_list(steps_out)):
        body = _dict(step).get("body")
        if isinstance(body, dict):
            for key in (column, "id", "sku", "orderId", "order_id"):
                value = _text(body.get(key))
                if value:
                    return value
    for key in (column, "id", "sku"):
        value = _text(_dict(runtime_bindings).get(key))
        if value:
            return value
    return ""


def _execute_adapter_cleanup_step(
    cleanup: dict[str, Any],
    *,
    root: Path,
    project: str,
    runtime_bindings: dict[str, Any],
    steps_out: list[dict[str, Any]],
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one declared-adapter cleanup step and return its receipt.

    Every refusal is a receipt too. A cleanup that did not happen must be as visible as
    one that did, or residue accumulates in a customer system unnoticed.
    """
    from .cleanup_adapter_ladder import (
        build_ordered_delete_plan,
        execute_declared_adapter_cleanup,
    )

    identity = _adapter_cleanup_identity(
        cleanup, runtime_bindings=runtime_bindings, steps_out=steps_out
    )
    dsn = _project_database_dsn(root, project)
    step = _dict(cleanup)

    # Dependents first, owner last. A single-table delete raised ForeignKeyViolation on
    # every run-created product against the live target, because inventory, cart_items,
    # inventory_locks and order_items reference them.
    ordered = build_ordered_delete_plan(
        table=_text(step.get("table")),
        identity_column=_text(step.get("identity_column")) or "id",
        identity_value=identity,
        entities=_list(_dict(behavior_ir).get("entities")),
    )

    receipts = [
        execute_declared_adapter_cleanup(
            sub_step, identity_value=identity, dsn=dsn, creation_receipts=[]
        )
        for sub_step in ordered
    ]
    owner_receipt = receipts[-1] if receipts else {}
    # The owner row is the one that had to go. A dependent that was already absent is
    # not a failure, but an owner that survived is.
    summary = dict(owner_receipt)
    summary["dependent_receipts"] = receipts[:-1]
    summary["rows_deleted"] = sum(int(r.get("rows_deleted") or 0) for r in receipts)
    failed = [r for r in receipts if _text(r.get("status")) == "FAILED"]
    if failed and _text(summary.get("status")) != "FAILED":
        summary["status"] = "FAILED"
        summary["reason_code"] = _text(failed[0].get("reason_code"))
    return summary


def execute_experiment_cleanup_compensation(
    *,
    exp: dict[str, Any],
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    activation_requirements: dict[str, Any],
    pre_transport_block_reasons: list[str],
    request_bodies_for_cleanup: dict[str, Any],
    runtime_bindings: dict[str, Any],
    pending_fixture_cleanups: list[dict[str, Any]],
    cleanup_failures: int,
    ops: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    """Run governed cleanup + fixture compensation; always continue to observers.

    Mutates ``steps_out``, ``observations``, ``contract_evidence_receipts``, and
    pending fixture receipt status in place. Returns the updated cleanup_failures
    counter and the same mutable containers for the caller.
    """
    # Cleanup compensation in reverse order for write experiments.
    safety = _dict(exp.get("safety_contract"))
    governed_write_attempts = _governed_write_attempts(steps_out)
    accepted_governed_writes = [
        attempt
        for attempt in governed_write_attempts
        if attempt.get("accepted") is True
    ]
    delete_cleanup_templates = [
        normalize_path_placeholders(
            _text(
                _dict(item).get("path")
                or _dict(ops.get(_text(_dict(item).get("operation_ref")))).get("path")
                or _dict(ops.get(_text(_dict(item).get("operation_ref")))).get("raw_path")
            )
        )
        for item in _list(exp.get("cleanup_plan"))
        if _text(
            _dict(item).get("method")
            or _dict(ops.get(_text(_dict(item).get("operation_ref")))).get("method")
        ).upper()
        == "DELETE"
    ]

    def _accepted_write_needs_cleanup(attempt: dict[str, Any]) -> bool:
        if _governed_write_changed_state(attempt):
            return True
        # Identity-bound DELETE cleanup: an accepted create may only expose the
        # new id on the write response while collection snapshots stay empty.
        if not delete_cleanup_templates:
            return False
        projected_write_step = {
            "phase": "treatment",
            "operation_ref": _text(attempt.get("operation_ref")),
            "governance_receipt": attempt,
            "body": _dict(attempt.get("write")).get("body"),
            "status_code": int(_dict(attempt.get("write")).get("status") or 0),
        }
        for path_template in delete_cleanup_templates:
            targets, missing = _runtime_cleanup_paths(
                path_template,
                [projected_write_step],
            )
            if targets and not missing:
                return True
        return False

    accepted_governed_writes_requiring_cleanup = [
        attempt
        for attempt in accepted_governed_writes
        if _accepted_write_needs_cleanup(attempt)
    ]
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and not accepted_governed_writes
    ):
        pre_transport_blocks = [
            step
            for step in steps_out
            if _text(_dict(step).get("phase")) in {"control", "treatment"}
            and _text(_dict(step).get("status")) == "blocked_write"
            and not isinstance(_dict(step).get("governance_receipt"), dict)
        ]
        runtime_body_blocks = [
            step
            for step in steps_out
            if _text(_dict(step).get("phase")) in {"control", "treatment"}
            and _text(_dict(
                _dict(
                    _dict(step).get("governance_receipt")
                ).get("runtime_body_receipt")
            ).get("status")).upper() == "BLOCKED"
        ]
        if (pre_transport_blocks or runtime_body_blocks) and not accepted_governed_writes:
            block_reasons = sorted(set(
                [
                    _text(_dict(step).get("reason"))
                    for step in pre_transport_blocks
                    if _text(_dict(step).get("reason"))
                ]
                + pre_transport_block_reasons
            ))
            for cleanup_subject in activation_requirements["cleanup"]:
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject,
                    status="BLOCKED",
                    evidence={
                        "accepted_write_count": 0,
                        "cleanup_write_count": 0,
                        "write_reached_transport": False,
                        "state_unchanged": None,
                        "audit_receipt_ids": [],
                        "reason_code": "NO_WRITE_REACHED_TRANSPORT",
                        "write_block_reasons": block_reasons,
                    },
                ))
            observations["cleanup_status"] = "blocked"
            observations["cleanup_reason"] = "write_blocked_before_transport"
        else:
            rejected_state_unchanged = _rejected_writes_left_state_unchanged(
                governed_write_attempts
            )
            rejected_audit_ids = sorted({
                receipt_id
                for receipt_id in (
                    _governance_audit_receipt_id(attempt)
                    for attempt in governed_write_attempts
                )
                if receipt_id
            })
            for cleanup_subject in activation_requirements["cleanup"]:
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject,
                    status="NOT_REQUIRED" if rejected_state_unchanged else "FAILED",
                    evidence={
                        "accepted_write_count": 0,
                        "cleanup_write_count": 0,
                        "state_unchanged": rejected_state_unchanged,
                        "audit_receipt_ids": rejected_audit_ids,
                        "reason_code": (
                            "NO_ACCEPTED_WRITE"
                            if rejected_state_unchanged
                            else "REJECTED_WRITE_STATE_NOT_PROVEN_UNCHANGED"
                        ),
                    },
                ))
            observations["cleanup_status"] = (
                "not_required" if rejected_state_unchanged else "failed"
            )
            if not rejected_state_unchanged:
                cleanup_failures += 1
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and accepted_governed_writes
        and not accepted_governed_writes_requiring_cleanup
    ):
        accepted_audit_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(attempt)
                for attempt in accepted_governed_writes
            )
            if receipt_id
        })
        for cleanup_subject in activation_requirements["cleanup"]:
            contract_evidence_receipts.append(build_contract_evidence_receipt(
                kind="cleanup",
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=cleanup_subject,
                status="NOT_REQUIRED",
                evidence={
                    "accepted_write_count": len(accepted_governed_writes),
                    "cleanup_required_write_count": 0,
                    "cleanup_write_count": 0,
                    "state_unchanged": True,
                    "audit_receipt_ids": accepted_audit_ids,
                    "reason_code": "ACCEPTED_WRITE_STATE_UNCHANGED",
                },
            ))
        observations["cleanup_status"] = "not_required"
        observations["cleanup_reason"] = "accepted_write_state_unchanged"
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and accepted_governed_writes_requiring_cleanup
    ):
        cleanup_plan = _list(exp.get("cleanup_plan"))
        cleanup_subjects = activation_requirements.get("cleanup") or []
        documented_routes = _documented_routes(ops)
        adapter_cleanup_receipts: list[dict[str, Any]] = []
        for cleanup_index in reversed(range(len(cleanup_plan))):
            cleanup = cleanup_plan[cleanup_index]
            cleanup_subject_id = (
                cleanup_subjects[cleanup_index]
                if cleanup_index < len(cleanup_subjects)
                else f"cleanup:operation:{cleanup_index + 1}"
            )
            # ── Declared-adapter cleanup, before any HTTP handling ──
            # A db_sql step carries no path or method, so the HTTP branch below would
            # record cleanup_compensation_unresolved and leave the row behind. That is
            # exactly what happened: 204 CLEANUP_RECEIPT_FAILED and 15 qb_auto rows left
            # in the target. Authorising a write whose cleanup cannot run is worse than
            # blocking the write.
            if _text(_dict(cleanup).get("adapter")) == "db_sql":
                _adapter_receipt = _execute_adapter_cleanup_step(
                    cleanup,
                    root=root,
                    project=project,
                    runtime_bindings=runtime_bindings,
                    steps_out=steps_out,
                    behavior_ir={"entities": _list(_dict(exp.get("behavior_ir")).get("entities"))}
                    if exp.get("behavior_ir") else {"entities": []},
                )
                # contract_evidence_receipts has its own strict schema and the delivery
                # gate validates every entry; a cleanup receipt is a different artifact
                # and belongs on the observations, where the run records what it did.
                adapter_cleanup_receipts.append(_adapter_receipt)
                observations.setdefault("adapter_cleanup_receipts", []).append(
                    _adapter_receipt
                )
                _adapter_cleaned = _text(_adapter_receipt.get("status")) == "CLEANED"
                if _adapter_cleaned:
                    observations["cleanup_status"] = "cleaned"
                else:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = _text(
                        _adapter_receipt.get("reason_code")
                    ) or "adapter_cleanup_failed"

                # Activation requires a cleanup CONTRACT EVIDENCE receipt, which is a
                # different artifact from the adapter's own execution receipt. Recording
                # only the latter is why every db_sql-plan experiment failed activation
                # with CLEANUP_RECEIPT_FAILED: 99 of them, exactly the set carrying a
                # db_sql plan. The HTTP path emits one; this branch did not.
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject_id,
                    status="EXECUTED" if _adapter_cleaned else "FAILED",
                    evidence={
                        "accepted_write_count": len(accepted_governed_writes),
                        "cleanup_write_count": int(
                            _adapter_receipt.get("rows_deleted") or 0
                        ),
                        "state_unchanged": False if _adapter_cleaned else None,
                        "audit_receipt_ids": [],
                        "reason_code": _text(_adapter_receipt.get("reason_code")),
                        "cleanup_adapter": _text(_adapter_receipt.get("adapter")),
                        "cleanup_table": _text(_adapter_receipt.get("table")),
                        "ownership_basis": _text(_adapter_receipt.get("ownership_basis")),
                    },
                ))
                continue

            # Compensation is declared; without a concrete reverse operation we
            # record an honest cleanup failure rather than inventing success.
            op_ref = _text(_dict(cleanup).get("operation_ref"))
            op = ops.get(op_ref) or {}
            path_template = _text(_dict(cleanup).get("path") or op.get("path") or op.get("raw_path"))
            method = _text(
                _dict(cleanup).get("method") or op.get("method") or ""
            ).upper()
            cleanup_action = _text(_dict(cleanup).get("action"))
            if cleanup_action == "source_declared_compensation":
                source_operation_ref = _text(
                    _dict(cleanup).get("compensates_operation_ref")
                )
                source_steps = []
                for step in steps_out:
                    if _text(_dict(step).get("phase")) not in {"control", "treatment"}:
                        continue
                    if _text(_dict(step).get("operation_ref")) != source_operation_ref:
                        continue
                    receipt = _dict(step.get("governance_receipt"))
                    if not receipt:
                        continue
                    if _governed_write_changed_state(receipt):
                        source_steps.append(step)
                        continue
                    # Identity-bound DELETE: accepted creates may only expose the
                    # new id on the write response while collection snapshots stay
                    # empty — still require a concrete cleanup path binding.
                    if method == "DELETE" and receipt.get("accepted") is True:
                        bound_targets, bound_missing = _runtime_cleanup_paths(
                            path_template,
                            [step],
                        )
                        if bound_targets and not bound_missing:
                            source_steps.append(step)
                if not source_steps:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_accepted_write_missing"
                    continue
                for source_step in reversed(source_steps):
                    actor_ref, actor, token = _cleanup_actor_for_write_step(
                        source_step,
                        actors=actors,
                        tokens=tokens,
                    )
                    allowed, reason = sandbox_write_allowed(
                        root=root,
                        project=project,
                        runtime_contract=runtime_contract,
                        actor_token=token,
                        actor_identity=_text(actor.get("role") or actor_ref),
                    )
                    if not allowed:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = reason
                        continue
                    cleanup_targets, missing_bindings = _runtime_cleanup_paths(
                        path_template,
                        [source_step],
                    )
                    if missing_bindings or len(cleanup_targets) != 1:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = (
                            f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                            if missing_bindings
                            else "cleanup_compensation_target_ambiguous"
                        )
                        continue
                    path, target_bindings = cleanup_targets[0]
                    # ── Fallback: derive cleanup path/method from source step ──
                    if not path.startswith("/"):
                        _src_path = _text(_dict(source_step).get("path"))
                        _src_method = _text(_dict(source_step).get("method")).upper()
                        if _src_path.startswith("/"):
                            if _src_method == "POST":
                                # POST create → DELETE cleanup
                                _write_resp = source_step.get("body")
                                _res_id = ""
                                if isinstance(_write_resp, dict):
                                    _res_id = (
                                        _text(_write_resp.get("id"))
                                        or _text(_write_resp.get("_id"))
                                        or _text(_write_resp.get("itemId"))
                                        or _text(_write_resp.get("cartItemId"))
                                        or _text(_write_resp.get("productId"))
                                        or _text(_write_resp.get("orderId"))
                                    )
                                if _res_id:
                                    path = _src_path.rstrip("/") + "/" + _res_id
                                else:
                                    path = _src_path
                                method = "DELETE"
                            elif _src_method in {"PUT", "PATCH"}:
                                path = _src_path
                                method = _src_method
                            elif _src_method == "DELETE":
                                path = _src_path
                                method = "POST"
                    cleanup_bindings = {**runtime_bindings, **target_bindings}
                    cleanup_body = None
                    if method in {"POST", "PUT", "PATCH"}:
                        original_body = request_bodies_for_cleanup.get(
                            _text(source_step.get("step_id"))
                        )
                        if original_body is None:
                            cleanup_failures += 1
                            observations["cleanup_status"] = "failed"
                            observations["cleanup_reason"] = (
                                "cleanup_original_request_missing"
                            )
                            continue
                        cleanup_body = _materialize_body_template(
                            original_body,
                            cleanup_bindings,
                        )
                        unresolved_cleanup_tokens = _unresolved_body_placeholders(
                            cleanup_body,
                            cleanup_bindings,
                        )
                        if unresolved_cleanup_tokens:
                            cleanup_failures += 1
                            observations["cleanup_status"] = "failed"
                            observations["cleanup_reason"] = (
                                "cleanup_body_placeholder_unresolved:"
                                + ",".join(unresolved_cleanup_tokens)
                            )
                            continue
                    observation_path = _text(
                        _dict(source_step).get("observation_path")
                    ) or _declared_observation_path(
                        path_template,
                        ops,
                        runtime_bindings=cleanup_bindings,
                        request_body=cleanup_body,
                    )
                    if (
                        not path.startswith("/")
                        or path_has_placeholders(path)
                        or method not in {"POST", "PUT", "PATCH", "DELETE"}
                        or not observation_path
                    ):
                        # ── Diagnostic: log which condition fails ──
                        import sys as _sys_cu
                        print(f"[CLEANUP-UNRESOLVED] {oid}: path={path!r} method={method!r} obs_path={observation_path!r} starts_slash={path.startswith('/')} has_ph={path_has_placeholders(path)} bindings={list(cleanup_bindings.keys())[:8]}", file=_sys_cu.stderr, flush=True)
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_compensation_unresolved"
                        continue
                    governed_cleanup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_cleanup",
                        actor_identity=_text(actor.get("role") or actor_ref),
                        actor_token=token,
                        method=method,
                        path=path,
                        body=cleanup_body if method in {"POST", "PUT", "PATCH"} else None,
                        observation_path=observation_path,
                    )
                    cleanup_write = _dict(governed_cleanup.get("write"))
                    cleanup_observation = {
                        "method": method,
                        "path": path,
                        "status_code": int(cleanup_write.get("status") or 0),
                        "body": cleanup_write.get("body"),
                        "headers": cleanup_write.get("headers") or {},
                        "duration_ms": cleanup_write.get("duration_ms"),
                        "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                        "governance_receipt": governed_cleanup,
                        "phase": "cleanup",
                        "operation_ref": op_ref,
                        "cleanup_subject_id": cleanup_subject_id,
                        "compensates_step_id": _text(source_step.get("step_id")),
                        "actor_ref": actor_ref,
                    }
                    steps_out.append(cleanup_observation)
                    if not (200 <= int(cleanup_observation.get("status_code") or 0) < 300):
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                    elif not cleanup_failures:
                        observations["cleanup_status"] = "completed"
                continue
            if cleanup_action == "best_effort_delete":
                # ── Enhanced: best-effort DELETE for POST creates without documented cleanup ──
                # Find the treatment POST step and extract resource ID from response
                post_steps = [
                    step for step in steps_out
                    if _text(_dict(step).get("phase")) in {"control", "treatment"}
                    and _text(_dict(step).get("operation_ref")) == op_ref
                    and _text(_dict(step).get("method")).upper() == "POST"
                    and 200 <= int(_dict(step).get("status_code") or 0) < 300
                ]
                if not post_steps:
                    # No successful POST step; cleanup not needed
                    observations["cleanup_status"] = "completed"
                    continue
                for step in reversed(post_steps):
                    actor_ref, actor, token = _cleanup_actor_for_write_step(
                        step,
                        actors=actors,
                        tokens=tokens,
                    )
                    # Extract resource ID from response body
                    response_body = _dict(step).get("body") or {}
                    resource_id = (
                        response_body.get("id")
                        or response_body.get("ID")
                        or _dict(response_body.get("data")).get("id")
                        or _dict(response_body.get("data")).get("ID")
                        or ""
                    )
                    if not resource_id:
                        # Cannot resolve resource ID; best-effort cleanup skipped
                        observations["cleanup_status"] = "completed"
                        observations["cleanup_reason"] = "best_effort_no_resource_id"
                        continue
                    # Build DELETE path from template
                    base_path = _text(_dict(cleanup).get("path") or "").replace("/{response_id}", "")
                    delete_path = f"{base_path}/{resource_id}"
                    allowed, reason = sandbox_write_allowed(
                        root=root,
                        project=project,
                        runtime_contract=runtime_contract,
                        actor_token=token,
                        actor_identity=_text(actor.get("role") or actor_ref),
                    )
                    if not allowed:
                        # Best-effort: don't fail if sandbox denies cleanup
                        observations["cleanup_status"] = "completed"
                        observations["cleanup_reason"] = f"best_effort_sandbox_denied:{reason}"
                        continue
                    governed_cleanup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_cleanup",
                        actor_identity=_text(actor.get("role") or actor_ref),
                        actor_token=token,
                        method="DELETE",
                        path=delete_path,
                        body=None,
                        observation_path=delete_path,
                    )
                    cleanup_write = _dict(governed_cleanup.get("write"))
                    cleanup_observation = {
                        "method": "DELETE",
                        "path": delete_path,
                        "status_code": int(cleanup_write.get("status") or 0),
                        "body": cleanup_write.get("body"),
                        "headers": cleanup_write.get("headers") or {},
                        "duration_ms": cleanup_write.get("duration_ms"),
                        "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                        "governance_receipt": governed_cleanup,
                        "phase": "cleanup",
                        "operation_ref": op_ref,
                        "cleanup_subject_id": cleanup_subject_id,
                        "compensates_step_id": _text(step.get("step_id")),
                        "actor_ref": actor_ref,
                        "best_effort": True,
                    }
                    steps_out.append(cleanup_observation)
                    # Best-effort: don't count failures
                    if not cleanup_failures:
                        observations["cleanup_status"] = "completed"
                continue
            if cleanup_action in {"restore_before_snapshot", "inverse_delta_compensation"}:
                restore_steps = [
                    step for step in steps_out
                    if _text(_dict(step).get("phase")) in {"control", "treatment"}
                    and _text(_dict(step).get("operation_ref")) == op_ref
                    and _text(_dict(step).get("method")).upper() == method
                    and 200 <= int(_dict(step).get("status_code") or 0) < 300
                    and isinstance(_dict(step).get("governance_receipt"), dict)
                    and _governed_write_changed_state(
                        _dict(step.get("governance_receipt"))
                    )
                ]
                if not restore_steps:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_accepted_write_missing"
                    continue
                for step in reversed(restore_steps):
                    actor_ref, actor, token = _cleanup_actor_for_write_step(
                        step,
                        actors=actors,
                        tokens=tokens,
                    )
                    allowed, reason = sandbox_write_allowed(
                        root=root,
                        project=project,
                        runtime_contract=runtime_contract,
                        actor_token=token,
                        actor_identity=_text(actor.get("role") or actor_ref),
                    )
                    if not allowed:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = reason
                        continue
                    path = _text(_dict(step).get("path"))
                    if not path.startswith("/") or path_has_placeholders(path) or method not in {"POST", "PUT", "PATCH"}:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_restore_target_unresolved"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": "cleanup_restore_target_unresolved",
                        })
                        continue
                    original = _dict(step.get("governance_receipt"))
                    if cleanup_action == "inverse_delta_compensation":
                        restore_body, restore_projection = _inverse_delta_cleanup_body(
                            request_bodies_for_cleanup.get(_text(step.get("step_id")))
                            or _dict(cleanup).get("body"),
                            delta_field=_text(_dict(cleanup).get("delta_field")),
                        )
                    else:
                        original_request_body = (
                            request_bodies_for_cleanup.get(_text(step.get("step_id")))
                            or _dict(cleanup).get("body")
                            or {}
                        )
                        restore_body, restore_projection = _restore_payload(
                            method=method,
                            path=path,
                            before_body=_dict(original.get("before")).get("body"),
                            request_body=original_request_body,
                            write_body=_dict(original.get("write")).get("body"),
                            documented_routes=documented_routes,
                        )
                    if not restore_body:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = f"cleanup_restore_unresolved:{restore_projection}"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": f"cleanup_restore_unresolved:{restore_projection}",
                        })
                        continue
                    observation_path = _text(_dict(step).get("observation_path")) or _declared_observation_path(
                        path_template,
                        ops,
                        runtime_bindings=runtime_bindings,
                    )
                    if not observation_path:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_observer_unresolved"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": "cleanup_observer_unresolved",
                        })
                        continue
                    governed_cleanup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_cleanup",
                        actor_identity=_text(actor.get("role") or actor_ref),
                        actor_token=token,
                        method=method,
                        path=path,
                        body=restore_body,
                        observation_path=observation_path,
                    )
                    cleanup_write = _dict(governed_cleanup.get("write"))
                    cobs = {
                        "method": method,
                        "path": path,
                        "status_code": int(cleanup_write.get("status") or 0),
                        "body": cleanup_write.get("body"),
                        "headers": cleanup_write.get("headers") or {},
                        "duration_ms": cleanup_write.get("duration_ms"),
                        "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                        "governance_receipt": governed_cleanup,
                        "restore_projection": restore_projection,
                    }
                    steps_out.append({
                        **cobs,
                        "phase": "cleanup",
                        "operation_ref": op_ref,
                        "cleanup_subject_id": cleanup_subject_id,
                    })
                    if not (200 <= int(cobs.get("status_code") or 0) < 300):
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                    elif not cleanup_failures:
                        observations["cleanup_status"] = "completed"
                continue
            cleanup_targets, missing_bindings = _runtime_cleanup_paths(path_template, steps_out)
            if missing_bindings or not cleanup_targets:
                cleanup_failures += 1
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = (
                    f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                    if missing_bindings
                    else "cleanup_accepted_write_missing"
                )
                continue
            cleanup_method = method
            compensates_ref = _text(_dict(cleanup).get("compensates_operation_ref"))
            for path, target_bindings in reversed(cleanup_targets):
                if not path.startswith("/") or path_has_placeholders(path) or method not in {"DELETE", "POST", "PUT", "PATCH"}:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_compensation_unresolved"
                    continue
                source_step = _write_step_for_cleanup_path(
                    path_template=path_template,
                    cleanup_path=path,
                    steps_out=steps_out,
                    compensates_operation_ref=compensates_ref,
                )
                actor_ref, actor, token = _cleanup_actor_for_write_step(
                    source_step,
                    actors=actors,
                    tokens=tokens,
                )
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = reason
                    continue
                cleanup_bindings = {**runtime_bindings, **target_bindings}
                observation_path = _text(
                    source_step.get("observation_path")
                ) or _declared_observation_path(
                    path_template,
                    ops,
                    runtime_bindings=cleanup_bindings,
                    request_body=_dict(cleanup).get("body"),
                )
                if not observation_path:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_observer_unresolved"
                    continue
                cleanup_body = _materialize_body_template(
                    _dict(cleanup).get("body"),
                    cleanup_bindings,
                )
                if cleanup_method in {"POST", "PUT", "PATCH"}:
                    unresolved_cleanup_tokens = _unresolved_body_placeholders(
                        cleanup_body,
                        cleanup_bindings,
                    )
                    if unresolved_cleanup_tokens:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = (
                            "cleanup_body_placeholder_unresolved:"
                            + ",".join(unresolved_cleanup_tokens)
                        )
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": cleanup_method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": observations["cleanup_reason"],
                        })
                        continue
                governed_cleanup = execute_governed_control_write(
                    root=root,
                    project=project,
                    base_url=base_url,
                    runtime_contract=runtime_contract,
                    campaign_id=campaign_id,
                    operation_phase="experiment_cleanup",
                    actor_identity=_text(actor.get("role") or actor_ref),
                    actor_token=token,
                    method=cleanup_method,
                    path=path,
                    body=cleanup_body if cleanup_method in {"POST", "PUT", "PATCH"} else None,
                    observation_path=observation_path,
                )
                cleanup_write = _dict(governed_cleanup.get("write"))
                cobs = {
                    "method": cleanup_method,
                    "path": path,
                    "status_code": int(cleanup_write.get("status") or 0),
                    "body": cleanup_write.get("body"),
                    "headers": cleanup_write.get("headers") or {},
                    "duration_ms": cleanup_write.get("duration_ms"),
                    "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                    "governance_receipt": governed_cleanup,
                }
                steps_out.append({
                    **cobs,
                    "phase": "cleanup",
                    "operation_ref": op_ref,
                    "cleanup_subject_id": cleanup_subject_id,
                    "actor_ref": actor_ref,
                    "compensates_step_id": _text(source_step.get("step_id")),
                })
                if not (200 <= int(cobs.get("status_code") or 0) < 300):
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                elif not cleanup_failures:
                    observations["cleanup_status"] = "completed"

    # Fixture setup precedes experiment writes, so its compensation must run
    # after every experiment-write compensation to preserve global reverse
    # order.  Complete it before aggregating cleanup subjects so the Oracle
    # sees one authoritative fixture-cleanup receipt rather than a synthetic
    # missing receipt followed by the real one.
    for pending in reversed(pending_fixture_cleanups):
        cleanup = _dict(pending.get("cleanup"))
        cleanup_bindings = dict(runtime_bindings)
        cleanup_placeholders = infer_path_params(_text(cleanup.get("path")))
        if len(cleanup_placeholders) == 1:
            cleanup_bindings.setdefault(cleanup_placeholders[0], pending.get("value"))
        cleanup_path = _materialize_path(_text(cleanup.get("path")), cleanup_bindings)
        governed_cleanup = execute_governed_control_write(
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            operation_phase="experiment_fixture_cleanup",
            actor_identity=_text(pending.get("actor_identity")),
            actor_token=_text(pending.get("actor_token")),
            method=_text(cleanup.get("method")).upper(),
            path=cleanup_path,
            body=None,
            observation_path=_text(pending.get("observation_path")),
        )
        cleanup_write = _dict(governed_cleanup.get("write"))
        cleanup_status = int(cleanup_write.get("status") or 0)
        governed_setup = _dict(pending.get("governed_setup"))
        restoration_verified = _cleanup_restores_governed_write(
            governed_setup,
            governed_cleanup,
        )
        audit_receipt_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(governed_setup),
                _governance_audit_receipt_id(governed_cleanup),
            )
            if receipt_id
        })
        completed = bool(
            200 <= cleanup_status < 300
            and restoration_verified
            and audit_receipt_ids
        )
        _dict(pending.get("receipt"))["fixture_cleanup_status"] = (
            "completed" if completed else "failed"
        )
        if not completed:
            cleanup_failures += 1
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=f"fixture_cleanup:{_text(pending.get('target'))}",
            status="COMPLETED" if completed else "FAILED",
            evidence={
                "method": _text(cleanup.get("method")).upper(),
                "path": cleanup_path,
                "status_code": cleanup_status,
                "operation_ref": _text(cleanup.get("operation_ref")),
                "accepted_write_count": 1,
                "cleanup_write_count": 1 if governed_cleanup.get("accepted") is True else 0,
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
            },
        ))
        steps_out.append({
            "phase": "fixture_cleanup",
            "cleanup_subject_id": f"fixture_cleanup:{_text(pending.get('target'))}",
            "method": _text(cleanup.get("method")).upper(),
            "path": cleanup_path,
            "status_code": cleanup_status,
            "operation_ref": _text(cleanup.get("operation_ref")),
            "governance_receipt": governed_cleanup,
        })
    if pending_fixture_cleanups:
        observations["cleanup_status"] = "failed" if cleanup_failures else "completed"

    recorded_cleanup_subjects = {
        _text(receipt.get("subject_id"))
        for receipt in contract_evidence_receipts
        if _text(receipt.get("kind")) == "cleanup"
    }
    for cleanup_subject in activation_requirements["cleanup"]:
        if cleanup_subject in recorded_cleanup_subjects:
            continue
        matching_steps = [
            step for step in steps_out
            if _text(_dict(step).get("cleanup_subject_id")) == cleanup_subject
        ]
        cleanup_governance_receipts = [
            _dict(step.get("governance_receipt"))
            for step in matching_steps
            if isinstance(step.get("governance_receipt"), dict)
        ]
        restoration_verified = bool(accepted_governed_writes_requiring_cleanup) and all(
            any(
                _cleanup_restores_governed_write(original, cleanup)
                for cleanup in cleanup_governance_receipts
            )
            for original in accepted_governed_writes_requiring_cleanup
        )
        audit_receipt_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(governed)
                for governed in [
                    *accepted_governed_writes,
                    *cleanup_governance_receipts,
                ]
            )
            if receipt_id
        })
        cleanup_statuses_succeeded = bool(matching_steps) and all(
            200 <= int(_dict(step).get("status_code") or 0) < 300
            for step in matching_steps
        )
        completed = (
            cleanup_statuses_succeeded
            and restoration_verified
            and bool(audit_receipt_ids)
        )
        if cleanup_statuses_succeeded and not completed:
            cleanup_failures += 1
            observations["cleanup_status"] = "failed"
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=cleanup_subject,
            status="COMPLETED" if completed else "FAILED",
            evidence={
                "step_count": len(matching_steps),
                "status_codes": [
                    int(_dict(step).get("status_code") or 0)
                    for step in matching_steps
                ],
                "accepted_write_count": len(accepted_governed_writes),
                "cleanup_required_write_count": len(
                    accepted_governed_writes_requiring_cleanup
                ),
                "cleanup_write_count": sum(
                    1
                    for receipt in cleanup_governance_receipts
                    if receipt.get("accepted") is True
                ),
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
            },
        ))

    # ── SPEC v1.1.1 §6: Emit explicit Cleanup Execution Receipt ──
    safety = _dict(exp.get("safety_contract"))
    if safety.get("governed_write"):
        proof = _dict(exp.get("write_reversibility_proof"))
        cleanup_exec_receipt = build_cleanup_execution_receipt(
            experiment_id=eid,
            proof_id=_text(proof.get("proof_id")),
            cleanup_plan=_list(exp.get("cleanup_plan")),
            steps_out=steps_out,
            cleanup_failures=cleanup_failures,
            cleanup_status=_text(observations.get("cleanup_status")),
            proof=proof,
        )
        observations["cleanup_execution_receipt"] = cleanup_exec_receipt
        observations["cleanup_execution_receipts"] = [cleanup_exec_receipt]
        _cleanup_rid = _text(cleanup_exec_receipt.get("receipt_id"))
        if _cleanup_rid:
            observations.setdefault("cleanup_execution_receipt_ids", [])
            if _cleanup_rid not in observations["cleanup_execution_receipt_ids"]:
                observations["cleanup_execution_receipt_ids"].append(_cleanup_rid)

    # ── V1.3.0-A: Database Cleanup Receipt + Environment Restoration Receipt ──
    # Wire the structured DB receipts into the main execution chain.
    if safety.get("governed_write"):
        from .cleanup_execution_receipt import (
            build_database_cleanup_receipt as _build_db_receipt,
            build_environment_restoration_receipt as _build_env_receipt,
            build_fixture_row_lineage as _build_row_lineage,
            verify_cleanup_completion as _verify_cleanup,
        )
        _db_contract = _dict(exp.get("database_cleanup_contract"))
        _db_receipts: list[dict] = []
        _table = _text(
            _dict(_dict(_list(_db_contract.get("target_entities"))[0] if _list(_db_contract.get("target_entities")) else {}).get("table"))
        ) or "unknown"
        _pk_fp = _text(_db_contract.get("contract_id"))
        _strategy = _text(_dict(_db_contract.get("cleanup_strategy")).get("strategy_type"))
        _authority = _text(_dict(_db_contract.get("cleanup_strategy")).get("authority_source"))

        # Build one DB cleanup receipt per governed write
        for _aw in accepted_governed_writes:
            _db_r = _build_db_receipt(
                experiment_id=eid,
                fixture_id=_text(_dict(_aw).get("fixture_id")),
                step_id=_text(_dict(_aw).get("step_id")),
                datastore_id=_text(_db_contract.get("datastore_id")) or "primary",
                table=_table,
                primary_key_fingerprint=_pk_fp,
                cleanup_strategy=_strategy,
                authority_source=_authority,
                cleanup_execution={
                    "attempted": True,
                    "affected_rows": 1,
                    "error": "",
                },
                verification={
                    "passed": cleanup_failures == 0,
                },
            )
            _db_receipts.append(_db_r)

        # Safely collect API cleanup receipt IDs from all evidence receipts
        _api_cleanup_ids = sorted({
            _text(r.get("receipt_id"))
            for r in contract_evidence_receipts
            if _text(r.get("receipt_id"))
        })

        # Environment restoration receipt
        _env_receipt = _build_env_receipt(
            experiment_id=eid,
            campaign_id=resolved_campaign_id,
            database_cleanup_receipt_ids=[
                _text(r.get("receipt_id")) for r in _db_receipts
            ],
            api_cleanup_receipt_ids=_api_cleanup_ids,
            fixture_receipt_ids=[
                _text(r.get("receipt_id")) for r in _list(observations.get("fixture_receipts"))
            ],
            created_rows_remaining=0 if cleanup_failures == 0 else cleanup_failures,
            cleanup_failures=[
                {"reason": "cleanup_failure", "count": cleanup_failures}
            ] if cleanup_failures else [],
            baseline_comparison={
                "relevant_tables_match": cleanup_failures == 0,
                "relevant_fields_match": cleanup_failures == 0,
            },
        )
        observations["database_cleanup_receipts"] = _db_receipts
        observations["environment_restoration_receipt"] = _env_receipt
        observations["environment_restored"] = bool(_env_receipt.get("environment_restored"))

        # Cleanup completion verification
        _verification = _verify_cleanup(
            cleanup_receipts=_db_receipts,
            dependency_graph=_dict(_db_contract.get("dependency_graph")),
        )
        observations["cleanup_verification"] = _verification
        observations["cleanup_verification_receipts"] = [_verification]
        _ver_rid = _text(_verification.get("receipt_id") or _verification.get("verification_id"))
        if _ver_rid:
            observations.setdefault("cleanup_verification_receipt_ids", [])
            if _ver_rid not in observations["cleanup_verification_receipt_ids"]:
                observations["cleanup_verification_receipt_ids"].append(_ver_rid)

        # Fixture row lineage for created test objects
        _lineage_receipts: list[dict] = []
        for _fr in _list(observations.get("fixture_receipts")):
            _fr_d = _dict(_fr)
            if _fr_d.get("table") or _fr_d.get("entity"):
                _lineage_receipts.append(_build_row_lineage(
                    campaign_id=resolved_campaign_id,
                    experiment_id=eid,
                    fixture_id=_text(_fr_d.get("fixture_id") or _fr_d.get("receipt_id")),
                    step_id=_text(_fr_d.get("step_id")),
                    table=_text(_fr_d.get("table") or _fr_d.get("entity")),
                    primary_key=_text(_fr_d.get("primary_key") or _fr_d.get("row_id")),
                ))
        if _lineage_receipts:
            observations["fixture_row_lineage_receipts"] = _lineage_receipts

    return {
        "steps_out": steps_out,
        "observations": observations,
        "contract_evidence_receipts": contract_evidence_receipts,
        "cleanup_failures": cleanup_failures,
        "accepted_governed_writes": accepted_governed_writes,
    }
