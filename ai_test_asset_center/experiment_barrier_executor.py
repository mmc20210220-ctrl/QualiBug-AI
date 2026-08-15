"""Barrier-group concurrency execution for experiments.

Extracted from ``experiment_executor.execute_one_experiment``. Runs control and
treatment participants that share a ``barrier_group`` concurrently, emits ready /
release / completed timeline events, and leaves non-barrier steps to sequential
``_exec_plan``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import contextvars
import threading
import time
from pathlib import Path
from typing import Any

from .contract_oracles import build_contract_evidence_receipt
from .experiment_runtime_support import (
    _WRITE_METHODS,
    _dict,
    _has_response_bound_create_observers,
    _list,
    _sha256,
    _text,
    _declared_observation_path,
    _resolve_token,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
)
from .real_id_resolver_base import normalize_path_placeholders
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
)
from .sandbox_write_executor import (
    execute_governed_control_write,
    sandbox_write_allowed,
)
from .sandbox_write_executor_base import _content_type


def execute_barrier_plans(
    *,
    control_plan: list[Any],
    treatment_plan: list[Any],
    actors: dict[str, dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    runtime_bindings: dict[str, Any],
    activation_requirements: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    observations: dict[str, Any],
) -> dict[str, Any]:
    """Execute barrier-grouped steps and return sequential leftovers identity.

    Mutates ``observations`` in place (control/treatment fields, harness_error,
    barrier_timeline). Returns steps plus evidence bags for the caller to merge.
    """
    contract_evidence_receipts: list[dict[str, Any]] = []
    request_bodies_for_cleanup: dict[str, Any] = {}
    pre_transport_block_reasons: list[str] = []

    def _barrier_timeline_event(
        events: list[dict[str, Any]],
        *,
        event: str,
        participant: str,
        started_at: float,
    ) -> None:
        events.append({
            "event": event,
            "participant": participant,
            "at_ms": int((time.perf_counter() - started_at) * 1000),
        })

    def _execute_barrier_step(
        *,
        step: dict[str, Any],
        phase: str,
        subject_id: str,
        barrier: threading.Barrier,
        timeline: list[dict[str, Any]],
        timeline_lock: threading.Lock,
        started_at: float,
    ) -> dict[str, Any]:
        actor_ref = _text(step.get("actor_ref"))
        op_ref = _text(step.get("operation_ref"))
        actor = actors.get(actor_ref) or {}
        op = ops.get(op_ref) or {}
        method = _text(op.get("method") or "GET").upper()
        path_template = _text(op.get("path") or op.get("raw_path"))
        path = _materialize_path(path_template, runtime_bindings)
        # SPEC §8.3: unresolved placeholders → block, no force-strip
        if "{" in path or "}" in path:
            return {
                "harness_error": False,
                "pre_transport_reason": "BLOCKED_MISSING_BINDING",
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={"reason_code": "BLOCKED_MISSING_BINDING",
                              "detail": f"unresolved_path_placeholders:{path}"},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_BINDING",
                    "detail": f"unresolved_path_placeholders:{path}",
                    "method": method,
                    "path": path,
                },
            }
        participant = _text(step.get("barrier_participant") or subject_id)
        request_body = (
            step.get("body")
            if "body" in step
            else op.get("request_example")
            if method in _WRITE_METHODS
            else None
        )
        request_body = _materialize_body_template(request_body, runtime_bindings)
        mutation = _dict(step.get("mutation"))
        mutation_class = _text(
            mutation.get("class")
            or mutation.get("constraint")
            or mutation.get("operator")
            or step.get("protocol_step")
            or step.get("intent")
            or f"{phase}_request"
        )
        mutation_selector = _text(
            mutation.get("json_path")
            or mutation.get("field_selector")
            or mutation.get("field")
        )
        mutation_operator = _text(
            mutation.get("operator") or mutation.get("constraint")
        )
        request_body_fingerprint = _sha256(request_body)
        request_semantics_fingerprint = _sha256({
            "operation_ref": op_ref,
            "method": method,
            "path_template": path_template,
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "request_body_fingerprint": request_body_fingerprint,
        })
        token = _resolve_token(actor, tokens)
        if method not in _WRITE_METHODS:
            return {
                "harness_error": True,
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="FAILED",
                    evidence={"reason_code": "BARRIER_WRITE_REQUIRED"},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BARRIER_WRITE_REQUIRED",
                    "method": method,
                    "path": path,
                },
            }
        allowed, reason = sandbox_write_allowed(
            root=root,
            project=project,
            runtime_contract=runtime_contract,
            actor_token=token,
            actor_identity=_text(actor.get("role") or actor_ref),
        )
        if not allowed:
            return {
                "harness_error": False,
                "pre_transport_reason": (
                    f"BLOCKED_TARGET_POLICY:{_text(reason)}"
                ),
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={"reason_code": _text(reason)},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": reason,
                    "method": method,
                    "path": path,
                },
            }
        observation_path = _declared_observation_path(
            path_template,
            ops,
            runtime_bindings=runtime_bindings,
            request_body=request_body,
        )
        if not observation_path and _has_response_bound_create_observers(op, ops):
            observation_path = normalize_path_placeholders(path_template)
        # Only a source-declared read can observe the effect; the write path is
        # not an observation path just because it is a URL — except for
        # collection creates whose effect proof is response-bound after 2xx.
        if not observation_path:
            return {
                "harness_error": False,
                "pre_transport_reason": "BLOCKED_MISSING_OBSERVER",
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={"reason_code": "BLOCKED_MISSING_OBSERVER"},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_OBSERVER",
                    "method": method,
                    "path": path,
                },
            }
        with timeline_lock:
            _barrier_timeline_event(
                timeline,
                event="ready",
                participant=participant,
                started_at=started_at,
            )
        try:
            barrier_index = barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            with timeline_lock:
                _barrier_timeline_event(
                    timeline,
                    event="broken",
                    participant=participant,
                    started_at=started_at,
                )
            return {
                "harness_error": True,
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="FAILED",
                    evidence={"reason_code": "BARRIER_RELEASE_FAILED"},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BARRIER_RELEASE_FAILED",
                    "method": method,
                    "path": path,
                },
            }
        if barrier_index == 0:
            with timeline_lock:
                _barrier_timeline_event(
                    timeline,
                    event="release",
                    participant="all",
                    started_at=started_at,
                )
        governed = execute_governed_control_write(
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            operation_phase=f"experiment_{phase}",
            actor_identity=_text(actor.get("role") or actor_ref),
            actor_token=token,
            method=method,
            path=path,
            body=request_body,
            observation_path=observation_path,
        )
        with timeline_lock:
            _barrier_timeline_event(
                timeline,
                event="completed",
                participant=participant,
                started_at=started_at,
            )
        write_receipt = _dict(governed.get("write"))
        obs = {
            "method": method,
            "path": path,
            "status_code": int(write_receipt.get("status") or 0),
            "body": write_receipt.get("body"),
            "headers": write_receipt.get("headers") or {},
            "duration_ms": write_receipt.get("duration_ms"),
            "error": write_receipt.get("error") or governed.get("reason") or "",
            "governance_receipt": governed,
            "observation_path": observation_path,
            "phase": phase,
            "step_id": subject_id,
            "actor_ref": actor_ref,
            "operation_ref": op_ref,
            "path_template": path_template,
            "request_body_fingerprint": request_body_fingerprint,
            "request_semantics_fingerprint": request_semantics_fingerprint,
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "protocol_step": _text(step.get("protocol_step")),
            "barrier_group": _text(step.get("barrier_group")),
            "barrier_participant": participant,
        }
        observed_status = int(obs.get("status_code") or 0)
        obs["response_observed"] = observed_status > 0
        _gov_for_status = _dict(obs.get("governance_receipt"))
        _write_reached = (
            int(_gov_for_status.get("write_request_attempt_count") or 0) > 0
        )
        _zero_transport_governed_block = (
            observed_status <= 0
            and bool(_gov_for_status)
            and not _write_reached
        )
        contract_status = (
            "BLOCKED"
            if _zero_transport_governed_block
            else "OBSERVED"
            if observed_status > 0
            else "FAILED"
        )
        return {
            "harness_error": False,
            "request_body": request_body,
            "contract_receipt": build_contract_evidence_receipt(
                kind=phase,
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=subject_id,
                status=contract_status,
                evidence={
                    "method": method,
                    "path": path,
                    "status_code": observed_status,
                    "operation_ref": op_ref,
                    "path_template": path_template,
                    "request_body_fingerprint": request_body_fingerprint,
                    "request_semantics_fingerprint": request_semantics_fingerprint,
                    "mutation_class": mutation_class,
                    "mutation_selector": mutation_selector,
                    "mutation_operator": mutation_operator,
                    "response_observed": observed_status > 0,
                    "response_content_type": _content_type(obs.get("headers")),
                    "write_reached_transport": _write_reached,
                    "request_reached_transport": (
                        observed_status > 0 or _write_reached
                    ),
                    "control_succeeded": (
                        200 <= observed_status < 300
                        if phase == "control"
                        else None
                    ),
                    "barrier_group": _text(step.get("barrier_group")),
                    "barrier_participant": participant,
                },
            ),
            "step": obs,
        }

    def _barrier_items(
        control_plan: list[Any],
        treatment_plan: list[Any],
    ) -> tuple[list[dict[str, Any]], set[int]]:
        items: list[dict[str, Any]] = []
        consumed: set[int] = set()
        for phase, plan in (("control", control_plan), ("treatment", treatment_plan)):
            planned_subjects = activation_requirements.get(phase) or []
            for index, step in enumerate(plan):
                if not isinstance(step, dict) or not _text(step.get("barrier_group")):
                    continue
                subject_id = (
                    planned_subjects[index]
                    if index < len(planned_subjects)
                    else _text(step.get("step_id"))
                    or f"{phase}:{index + 1}"
                )
                items.append({
                    "phase": phase,
                    "index": index,
                    "step": step,
                    "subject_id": subject_id,
                    "barrier_group": _text(step.get("barrier_group")),
                })
                consumed.add(id(step))
        return items, consumed

    def _barrier_block_row(
        item: dict[str, Any],
        *,
        reason: str,
        reason_code: str = "BLOCKED_MISSING_BINDING",
        unresolved_path_tokens: list[str] | None = None,
        unresolved_body_tokens: list[str] | None = None,
    ) -> dict[str, Any]:
        step = _dict(item.get("step"))
        phase = _text(item.get("phase"))
        subject_id = _text(item.get("subject_id"))
        actor_ref = _text(step.get("actor_ref"))
        op_ref = _text(step.get("operation_ref"))
        op = ops.get(op_ref) or {}
        method = _text(op.get("method") or "GET").upper()
        path_template = _text(op.get("path") or op.get("raw_path"))
        path = _materialize_path(path_template, runtime_bindings)
        participant = _text(step.get("barrier_participant") or subject_id)
        evidence: dict[str, Any] = {
            "method": method,
            "path": path,
            "status_code": 0,
            "operation_ref": op_ref,
            "path_template": path_template,
            "response_observed": False,
            "request_reached_transport": False,
            "reason_code": reason_code,
            "detail": reason,
            "barrier_group": _text(item.get("barrier_group")),
            "barrier_participant": participant,
        }
        if method in _WRITE_METHODS:
            evidence["write_reached_transport"] = False
        if unresolved_path_tokens:
            evidence["unresolved_path_tokens"] = unresolved_path_tokens
        if unresolved_body_tokens:
            evidence["unresolved_body_tokens"] = unresolved_body_tokens
        return {
            "harness_error": False,
            "contract_receipt": build_contract_evidence_receipt(
                kind=phase,
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=subject_id,
                status="BLOCKED",
                evidence=evidence,
            ),
            "step": {
                "phase": phase,
                "step_id": subject_id,
                "status": "blocked_write"
                if method in _WRITE_METHODS
                else "blocked_request",
                "reason": reason_code,
                "detail": reason,
                "method": method,
                "path": path,
                "path_template": path_template,
                "status_code": 0,
                "actor_ref": actor_ref,
                "operation_ref": op_ref,
                "protocol_step": _text(step.get("protocol_step")),
                "barrier_group": _text(item.get("barrier_group")),
                "barrier_participant": participant,
            },
        }

    def _barrier_pretransport_block(item: dict[str, Any]) -> dict[str, Any] | None:
        step = _dict(item.get("step"))
        actor_ref = _text(step.get("actor_ref"))
        if not actor_ref or actor_ref not in actors:
            reason = f"actor_identity_missing:{actor_ref or '<empty>'}"
            reason_code = "BLOCKED_MISSING_ACTOR"
            pre_transport_block_reasons.append(f"{reason_code}:{reason}")
            return _barrier_block_row(
                item,
                reason=reason,
                reason_code=reason_code,
            )
        op_ref = _text(step.get("operation_ref"))
        if not op_ref or op_ref not in ops:
            reason = f"operation_identity_missing:{op_ref or '<empty>'}"
            reason_code = "BLOCKED_MISSING_OPERATION"
            pre_transport_block_reasons.append(f"{reason_code}:{reason}")
            return _barrier_block_row(
                item,
                reason=reason,
                reason_code=reason_code,
            )
        op = ops[op_ref]
        method = _text(op.get("method") or "GET").upper()
        path = _materialize_path(
            _text(op.get("path") or op.get("raw_path")),
            runtime_bindings,
        )
        request_body = (
            step.get("body")
            if "body" in step
            else op.get("request_example")
            if method in _WRITE_METHODS
            else None
        )
        request_body = _materialize_body_template(request_body, runtime_bindings)
        unresolved_path_tokens = _unresolved_path_placeholders(path)
        unresolved_body_tokens = (
            _unresolved_body_placeholders(request_body, runtime_bindings)
            if method in _WRITE_METHODS
            else []
        )
        # Unresolved placeholders mean the fixture data does not exist. Blocking
        # here keeps a manufactured id off the target.
        reasons: list[str] = []
        if unresolved_path_tokens:
            reasons.append(
                "path_placeholder_unresolved:"
                + ",".join(unresolved_path_tokens)
            )
        if unresolved_body_tokens:
            reasons.append(
                "body_placeholder_unresolved:"
                + ",".join(unresolved_body_tokens)
            )
        if not reasons:
            return None
        reason = ";".join(reasons)
        pre_transport_block_reasons.append(reason)
        return _barrier_block_row(
            item,
            reason=reason,
            unresolved_path_tokens=unresolved_path_tokens,
            unresolved_body_tokens=unresolved_body_tokens,
        )

    def _exec_barrier_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault(_text(item.get("barrier_group")), []).append(item)
        executed_steps: list[dict[str, Any]] = []
        all_timeline_events: list[dict[str, Any]] = []
        for group_id, group_items in grouped.items():
            if len(group_items) < 2:
                observations["harness_error"] = True
                reason = f"barrier_group_participant_count_invalid:{group_id}:{len(group_items)}"
                pre_transport_block_reasons.append(reason)
                for item in group_items:
                    row = _barrier_block_row(item, reason=reason)
                    contract_evidence_receipts.append(_dict(row.get("contract_receipt")))
                    executed_steps.append(_dict(row.get("step")))
                continue
            timeline: list[dict[str, Any]] = []
            timeline_lock = threading.Lock()
            release_barrier = threading.Barrier(len(group_items))
            barrier_started = time.perf_counter()
            ordered_items = sorted(
                group_items,
                key=lambda item: (
                    0 if _text(item.get("phase")) == "control" else 1,
                    int(item.get("index") or 0),
                ),
            )
            preblocked_by_subject = {
                _text(item.get("subject_id")): row
                for item in ordered_items
                for row in [_barrier_pretransport_block(item)]
                if row is not None
            }
            if preblocked_by_subject:
                group_reason = f"barrier_group_pretransport_blocked:{group_id}"
                pre_transport_block_reasons.append(group_reason)
                completed = [
                    preblocked_by_subject.get(_text(item.get("subject_id")))
                    or _barrier_block_row(item, reason=group_reason)
                    for item in ordered_items
                ]
            else:
                with ThreadPoolExecutor(max_workers=len(ordered_items)) as pool:
                    futures = [
                        pool.submit(
                            contextvars.copy_context().run,
                            _execute_barrier_step,
                            step=_dict(item.get("step")),
                            phase=_text(item.get("phase")),
                            subject_id=_text(item.get("subject_id")),
                            barrier=release_barrier,
                            timeline=timeline,
                            timeline_lock=timeline_lock,
                            started_at=barrier_started,
                        )
                        for item in ordered_items
                    ]
                    completed = [future.result() for future in as_completed(futures)]
            completed_by_subject = {
                _text(_dict(row.get("step")).get("step_id")): row
                for row in completed
            }
            for item in ordered_items:
                row = completed_by_subject.get(_text(item.get("subject_id")))
                if not row:
                    observations["harness_error"] = True
                    continue
                if row.get("harness_error"):
                    observations["harness_error"] = True
                pre_transport_reason = _text(row.get("pre_transport_reason"))
                if pre_transport_reason:
                    pre_transport_block_reasons.append(pre_transport_reason)
                contract_evidence_receipts.append(_dict(row.get("contract_receipt")))
                step_out = _dict(row.get("step"))
                executed_steps.append(step_out)
                if "request_body" in row:
                    request_bodies_for_cleanup[_text(step_out.get("step_id"))] = row.get("request_body")
                if _text(step_out.get("phase")) == "control":
                    observations["control_observation"] = step_out
                    observations["control_actor_ref"] = _text(step_out.get("actor_ref"))
                    if 200 <= int(step_out.get("status_code") or 0) < 300:
                        observations["control_succeeded"] = True
                        observations["authorized_control"] = True
                if _text(step_out.get("phase")) == "treatment":
                    observations["treatment_observation"] = step_out
                    observations["treatment_result"] = step_out
                    observations["treatment_actor_ref"] = _text(step_out.get("actor_ref"))
                    observations["status_code"] = step_out.get("status_code")
                    observations["body"] = step_out.get("body")
            for event in timeline:
                event["barrier_group"] = group_id
            all_timeline_events.extend(timeline)
        if all_timeline_events:
            observations["barrier_timeline"] = [
                *_list(observations.get("barrier_timeline")),
                *all_timeline_events,
            ]
        return executed_steps

    barrier_plan_items, consumed_barrier_steps = _barrier_items(
        control_plan,
        treatment_plan,
    )
    executed_barrier_steps: list[dict[str, Any]] = []
    if barrier_plan_items:
        executed_barrier_steps.extend(_exec_barrier_groups(barrier_plan_items))
    return {
        "steps": executed_barrier_steps,
        "consumed_barrier_steps": consumed_barrier_steps,
        "contract_evidence_receipts": contract_evidence_receipts,
        "request_bodies_for_cleanup": request_bodies_for_cleanup,
        "pre_transport_block_reasons": pre_transport_block_reasons,
    }
