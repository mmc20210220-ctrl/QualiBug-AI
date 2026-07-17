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
from .sandbox_write_executor import (
    _restore_payload,
    execute_governed_control_write,
    sandbox_write_allowed,
)


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
        synthetic_step = {
            "phase": "treatment",
            "operation_ref": _text(attempt.get("operation_ref")),
            "governance_receipt": attempt,
            "body": _dict(attempt.get("write")).get("body"),
            "status_code": int(_dict(attempt.get("write")).get("status") or 0),
        }
        for path_template in delete_cleanup_templates:
            targets, missing = _runtime_cleanup_paths(path_template, [synthetic_step])
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
        for cleanup_index in reversed(range(len(cleanup_plan))):
            cleanup = cleanup_plan[cleanup_index]
            cleanup_subject_id = (
                cleanup_subjects[cleanup_index]
                if cleanup_index < len(cleanup_subjects)
                else f"cleanup:operation:{cleanup_index + 1}"
            )
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
                actor_ref = ""
                for planned_step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
                    if isinstance(planned_step, dict) and _text(planned_step.get("actor_ref")):
                        actor_ref = _text(planned_step.get("actor_ref"))
                        break
                actor = actors.get(actor_ref) or {}
                token = _resolve_token(actor, tokens)
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
                for source_step in reversed(source_steps):
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
                    }
                    steps_out.append(cleanup_observation)
                    if not (200 <= int(cleanup_observation.get("status_code") or 0) < 300):
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                    elif not cleanup_failures:
                        observations["cleanup_status"] = "completed"
                continue
            if cleanup_action in {"restore_before_snapshot", "inverse_delta_compensation"}:
                actor_ref = ""
                for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
                    if isinstance(step, dict) and _text(step.get("actor_ref")):
                        actor_ref = _text(step.get("actor_ref"))
                        break
                actor = actors.get(actor_ref) or {}
                token = _resolve_token(actor, tokens)
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
            # Prefer first control/treatment actor token for cleanup.
            actor_ref = ""
            for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
                if isinstance(step, dict) and _text(step.get("actor_ref")):
                    actor_ref = _text(step.get("actor_ref"))
                    break
            actor = actors.get(actor_ref) or {}
            token = _resolve_token(actor, tokens)
            cleanup_method = method
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
            for path, target_bindings in reversed(cleanup_targets):
                if not path.startswith("/") or path_has_placeholders(path) or method not in {"DELETE", "POST", "PUT", "PATCH"}:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_compensation_unresolved"
                    continue
                cleanup_bindings = {**runtime_bindings, **target_bindings}
                observation_path = _declared_observation_path(
                    path_template,
                    ops,
                    runtime_bindings=cleanup_bindings,
                    request_body=_dict(cleanup).get("body"),
                )
                if not observation_path:
                    # Reuse the governed write's already-declared effect observer
                    # when the cleanup op itself has no separate GET mapping
                    # (common for create→DELETE identity cleanup).
                    compensates_ref = _text(
                        _dict(cleanup).get("compensates_operation_ref")
                    )
                    for step in reversed(steps_out):
                        if _text(step.get("phase")) not in {"control", "treatment"}:
                            continue
                        if compensates_ref and _text(step.get("operation_ref")) != compensates_ref:
                            continue
                        candidate = _text(step.get("observation_path"))
                        if (
                            candidate.startswith("/")
                            and not path_has_placeholders(candidate)
                        ):
                            observation_path = candidate
                            break
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

    return {
        "steps_out": steps_out,
        "observations": observations,
        "contract_evidence_receipts": contract_evidence_receipts,
        "cleanup_failures": cleanup_failures,
        "accepted_governed_writes": accepted_governed_writes,
    }
