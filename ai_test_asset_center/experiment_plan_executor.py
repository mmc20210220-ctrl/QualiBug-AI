"""Sequential control/treatment plan execution for experiments.

Extracted from ``experiment_executor.execute_one_experiment``. Executes
non-barrier plan steps after ``execute_barrier_plans``, preserving
control-body capture for treatment reuse and pre-transport binding blocks.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

from .contract_oracles import build_contract_evidence_receipt
from .experiment_runtime_support import (
    _WRITE_METHODS,
    _declared_observation_path,
    _dict,
    _has_response_bound_create_observers,
    _list,
    _observation_state,
    _resolve_token,
    _response_bound_observation_path,
    _run_http_step,
    _sha256,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
)
from .real_id_resolver import (
    infer_path_params,
)
from .real_id_resolver_base import normalize_path_placeholders
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
)
from .sandbox_write_executor import (
    _http_request,
    execute_governed_control_write,
    sandbox_write_allowed,
)
from .sandbox_write_executor_base import evaluator_request_trace


# ── P0-4: Route resolution with service-direct fallback ──
import os as _os

def _resolve_route_with_fallback(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str,
) -> dict[str, Any]:
    """Execute HTTP step; on 404 try alternative service base URLs.

    Returns the observation dict with an added ``resolved_route`` field.
    """
    obs = _run_http_step(base_url=base_url, method=method, path=path, token=token)
    status = int(obs.get("status_code") or 0)
    resolved_route = {
        "strategy": "gateway",
        "base_url": base_url,
        "path": path,
        "status_code": status,
    }
    if status == 404:
        # Try alternative service URLs from environment
        alt_urls = [
            u.strip()
            for u in _os.environ.get("QUALIBUG_SERVICE_URLS", "").split(",")
            if u.strip()
        ]
        for alt_url in alt_urls:
            if alt_url.rstrip("/") == base_url.rstrip("/"):
                continue
            retry = _run_http_step(base_url=alt_url, method=method, path=path, token=token)
            retry_status = int(retry.get("status_code") or 0)
            if retry_status != 404 and retry_status > 0:
                obs = retry
                resolved_route = {
                    "strategy": "service_direct",
                    "base_url": alt_url,
                    "path": path,
                    "status_code": retry_status,
                    "original_gateway_status": 404,
                }
                break
    obs["resolved_route"] = resolved_route
    return obs


def execute_non_barrier_plans(
    *,
    control_plan: list[Any],
    treatment_plan: list[Any],
    consumed_barrier_steps: set[int],
    actors: dict[str, dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    runtime_bindings: dict[str, Any],
    activation_requirements: dict[str, Any],
    observations: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    cleanup_failures: int = 0,
) -> dict[str, Any]:
    """Execute sequential control/treatment steps not consumed by barriers.

    Mutates ``observations`` in place. Returns steps and evidence bags for the
    caller to merge before cleanup compensation.
    """
    contract_evidence_receipts: list[dict[str, Any]] = []
    request_bodies_for_cleanup: dict[str, Any] = {}
    pre_transport_block_reasons: list[str] = []

    source_observed_control_bodies: dict[str, Any] = {}
    source_body_control_blocked = False

    def _identity_block(
        *,
        phase: str,
        subject_id: str,
        step: dict[str, Any],
        reason_code: str,
        detail: str,
    ) -> dict[str, Any]:
        pre_transport_block_reasons.append(f"{reason_code}:{detail}")
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind=phase,
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=subject_id,
            status="BLOCKED",
            evidence={
                "request_reached_transport": False,
                "reason_code": reason_code,
                "detail": detail,
            },
        ))
        return {
            "phase": phase,
            "step_id": subject_id,
            "status": "blocked_request",
            "reason": reason_code,
            "detail": detail,
            "method": _text(step.get("method")).upper(),
            "path": _text(step.get("path") or step.get("path_template")),
            "status_code": 0,
            "actor_ref": _text(step.get("actor_ref")),
            "operation_ref": _text(step.get("operation_ref")),
        }

    def _exec_plan(plan: list[Any], *, phase: str) -> list[dict[str, Any]]:
        nonlocal cleanup_failures, source_body_control_blocked
        results = []
        planned_subjects = activation_requirements.get(phase) or []
        for index, step in enumerate(plan):
            if not isinstance(step, dict):
                continue
            actor_ref = _text(step.get("actor_ref"))
            op_ref = _text(step.get("operation_ref"))
            # Contract subject identity is step_id-authoritative, not positional.
            #
            # This fixes a live misalignment, not just a future N-step concern.
            # `planned_subjects` is activation_requirements[phase], built by
            # contract_oracles._plan_subjects over the UNFILTERED plan and then de-duplicated
            # with dict.fromkeys. But `plan` here is the barrier-FILTERED list built at the
            # two _exec_plan call sites, which drop every step already consumed as a barrier
            # participant. So any phase mixing barrier and non-barrier steps mislabels every
            # step after the first barrier step, filing its contract evidence receipt under
            # another step's identity.
            #
            # Provably identical for every existing plan: _plan_subjects derives each subject
            # as `step_id or id`, and every built-in protocol branch emits a literal
            # 'control_1' / 'treatment_1'. For a one-step phase planned_subjects is exactly
            # ['control_1'] or ['treatment_1'] and the step's own step_id is that same
            # literal, so the declared branch returns what planned_subjects[index] returned.
            # The positional and generated fallbacks are retained unchanged for a step that
            # declares no id.
            declared_step_id = _text(step.get("step_id"))
            subject_id = (
                declared_step_id
                if declared_step_id and declared_step_id in planned_subjects
                else (
                    planned_subjects[index]
                    if index < len(planned_subjects)
                    else declared_step_id
                    or f"{phase}:{op_ref or 'operation'}:{index + 1}"
                )
            )
            if phase == "treatment" and source_body_control_blocked:
                reason = "control_body_binding_blocked"
                pre_transport_block_reasons.append(reason)
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={
                        "write_reached_transport": False,
                        "reason_code": reason,
                    },
                ))
                results.append({
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_BINDING",
                    "detail": reason,
                    "method": _text(step.get("method") or "POST").upper(),
                    "path": _text(step.get("path") or step.get("path_template")),
                    "status_code": 0,
                })
                continue
            if not actor_ref or actor_ref not in actors:
                results.append(_identity_block(
                    phase=phase,
                    subject_id=subject_id,
                    step=step,
                    reason_code="BLOCKED_MISSING_ACTOR",
                    detail=f"actor_identity_missing:{actor_ref or '<empty>'}",
                ))
                continue
            if not op_ref or op_ref not in ops:
                results.append(_identity_block(
                    phase=phase,
                    subject_id=subject_id,
                    step=step,
                    reason_code="BLOCKED_MISSING_OPERATION",
                    detail=f"operation_identity_missing:{op_ref or '<empty>'}",
                ))
                continue
            actor = actors[actor_ref]
            op = ops[op_ref]
            method = _text(op.get("method") or "GET").upper()
            path_template = _text(
                step.get("path") or op.get("path") or op.get("raw_path")
                or op.get("normalized_path") or op.get("path_template")
            )
            if not path_template:
                # SPEC §8.1: path missing → BLOCKED, no guessing from operation_id
                results.append(_identity_block(
                    phase=phase,
                    subject_id=subject_id,
                    step=step,
                    reason_code="BLOCKED_MISSING_OPERATION",
                    detail=f"source_declared_path_missing:{op_ref}",
                ))
                continue
            path = _materialize_path(
                path_template,
                runtime_bindings,
            )
            unresolved_path_tokens = _unresolved_path_placeholders(path)
            query_spec = _dict(step.get("query"))
            if query_spec and not unresolved_path_tokens:
                materialized_query: dict[str, str] = {}
                unresolved_query_tokens: list[str] = []
                for key, raw_value in query_spec.items():
                    name = _text(key)
                    if not name:
                        continue
                    value = raw_value
                    if isinstance(raw_value, str):
                        token_match = re.fullmatch(
                            r"\s*\{([A-Za-z_][A-Za-z0-9_]*)\}\s*",
                            raw_value,
                        )
                        if token_match:
                            token = _text(token_match.group(1))
                            bound = runtime_bindings.get(token)
                            if bound in (None, "", [], {}):
                                unresolved_query_tokens.append(token)
                                continue
                            value = bound
                    if value in (None, "", [], {}):
                        unresolved_query_tokens.append(name)
                        continue
                    materialized_query[name] = str(value)
                if unresolved_query_tokens:
                    unresolved_path_tokens = list(dict.fromkeys(
                        [*unresolved_path_tokens, *unresolved_query_tokens]
                    ))
                elif materialized_query:
                    separator = "&" if "?" in path else "?"
                    path = f"{path}{separator}{urlencode(materialized_query)}"
            if unresolved_path_tokens:
                # ── P0-PLACEHOLDER: BLOCK instead of force-strip ──
                # Unresolved path placeholders mean no real entity exists.
                # Executing with placeholder IDs (e.g. "1") guarantees 404/400
                # failures and wastes compute. Block the experiment until real
                # fixture data exists via Bootstrap.
                results.append({
                    "phase": phase,
                    "subject_id": subject_id,
                    "method": method,
                    "path": path,
                    "status_code": 0,
                    "skipped_reason": f"BLOCKED_UNRESOLVED_PATH_PLACEHOLDERS:{','.join(unresolved_path_tokens[:6])}",
                    "request": {},
                    "response": {"status_code": 0, "body": {}},
                })
                pre_transport_block_reasons.append(f"unresolved_path_placeholders:{','.join(unresolved_path_tokens[:6])}")
                continue
            request_body = (
                step.get("body")
                if "body" in step
                else op.get("request_example")
                if method in _WRITE_METHODS and op.get("request_example")
                else None
            )
            request_body = _materialize_body_template(
                request_body,
                runtime_bindings,
            )
            unresolved_body_tokens = _unresolved_body_placeholders(
                request_body,
                runtime_bindings,
            )
            if method in _WRITE_METHODS and unresolved_body_tokens:
                # ── P0-PLACEHOLDER: BLOCK instead of force-fill ──
                # Unresolved body placeholders mean fixture data is missing.
                # Force-filling with generated values creates fake data that
                # violates the Non-Production Execution Contract.
                results.append({
                    "phase": phase,
                    "subject_id": subject_id,
                    "method": method,
                    "path": path,
                    "status_code": 0,
                    "skipped_reason": f"BLOCKED_UNRESOLVED_BODY_PLACEHOLDERS:{','.join(unresolved_body_tokens[:6])}",
                    "request": {"body": request_body} if request_body else {},
                    "response": {"status_code": 0, "body": {}},
                })
                pre_transport_block_reasons.append(f"unresolved_body_placeholders:{','.join(unresolved_body_tokens[:6])}")
                continue
            runtime_body_plan = deepcopy(_dict(step.get("runtime_body_plan")))
            if runtime_body_plan:
                identity_fields = infer_path_params(path_template)
                runtime_body_plan["identity_bindings"] = {
                    field: runtime_bindings[field]
                    for field in identity_fields
                    if field in runtime_bindings
                    and runtime_bindings[field] not in (None, "")
                }
                if phase == "treatment" and op_ref in source_observed_control_bodies:
                    request_body = deepcopy(source_observed_control_bodies[op_ref])
                    runtime_body_plan = {}
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
            is_write = method in _WRITE_METHODS
            response_bound_observation: dict[str, Any] = {}
            runtime_body_blocked = False
            if is_write:
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    policy_reason = f"BLOCKED_TARGET_POLICY:{_text(reason)}"
                    pre_transport_block_reasons.append(policy_reason)
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind=phase,
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=subject_id,
                            status="BLOCKED",
                            evidence={"reason_code": _text(reason)},
                        )
                    )
                    results.append({
                        "phase": phase,
                        "step_id": subject_id,
                        "status": "blocked_write",
                        "reason": reason,
                        "method": method,
                        "path": path,
                    })
                    continue
                observation_path = _declared_observation_path(
                    path_template,
                    ops,
                    runtime_bindings=runtime_bindings,
                    request_body=request_body,
                )
                # Collection creates often only declare identity GETs
                # (…/{id}). Those cannot materialize before the write; governance
                # may observe the create collection, and effect proof comes from
                # _response_bound_observation_path after a 2xx response.
                if not observation_path and _has_response_bound_create_observers(op, ops):
                    observation_path = normalize_path_placeholders(path_template)
                # The observation path must be a source-declared read. Deriving
                # one from the write path assumes a URL convention the source
                # never stated, and reading back the write path itself makes the
                # write its own evidence.
                if not observation_path:
                    reason_code = "BLOCKED_MISSING_OBSERVER"
                    pre_transport_block_reasons.append(reason_code)
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind=phase,
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=subject_id,
                            status="BLOCKED",
                            evidence={
                                "write_reached_transport": False,
                                "reason_code": reason_code,
                            },
                        )
                    )
                    results.append({
                        "phase": phase,
                        "step_id": subject_id,
                        "status": "blocked_write",
                        "reason": reason_code,
                        "method": method,
                        "path": path,
                        "status_code": 0,
                    })
                    continue
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
                    runtime_body_plan=runtime_body_plan or None,
                )
                runtime_body_receipt = _dict(governed.get("runtime_body_receipt"))
                runtime_body_blocked = (
                    _text(runtime_body_receipt.get("status")).upper() == "BLOCKED"
                )
                if runtime_body_blocked:
                    reason_code = _text(
                        runtime_body_receipt.get("reason_code")
                        or governed.get("reason")
                    ) or "runtime_body_materialization_blocked"
                    pre_transport_block_reasons.append(reason_code)
                materialized_body = governed.get("materialized_request_body")
                if isinstance(materialized_body, dict) and materialized_body:
                    request_body = deepcopy(materialized_body)
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
                    if phase == "control":
                        source_observed_control_bodies[op_ref] = deepcopy(request_body)
                request_bodies_for_cleanup[subject_id] = request_body
                write_receipt = _dict(governed.get("write"))
                if 200 <= int(write_receipt.get("status") or 0) < 300:
                    response_bound_path = _response_bound_observation_path(
                        op,
                        ops,
                        write_receipt.get("body"),
                    )
                    if response_bound_path:
                        response_bound_raw = _http_request(
                            _text(response_bound_path.get("method") or "GET"),
                            base_url.rstrip("/") + _text(response_bound_path.get("path")),
                            token=token,
                        )
                        response_bound_observation = {
                            "method": _text(response_bound_path.get("method") or "GET"),
                            "path": _text(response_bound_path.get("path")),
                            "path_template": _text(response_bound_path.get("path_template")),
                            "status_code": int(response_bound_raw.get("status") or 0),
                            "status": int(response_bound_raw.get("status") or 0),
                            "body": response_bound_raw.get("body"),
                            "headers": response_bound_raw.get("headers") or {},
                            "duration_ms": response_bound_raw.get("duration_ms"),
                            "phase": f"{phase}_response_bound_effect_observation",
                            "step_id": f"{subject_id}:response_bound_effect",
                            "actor_ref": actor_ref,
                            "operation_ref": _text(response_bound_path.get("operation_ref")),
                            "source_operation_ref": op_ref,
                        }
                        governed["response_bound_after"] = {
                            "method": _text(response_bound_path.get("method") or "GET"),
                            "url": base_url.rstrip("/") + _text(response_bound_path.get("path")),
                            "status": int(response_bound_raw.get("status") or 0),
                            "body": response_bound_raw.get("body"),
                            "headers": response_bound_raw.get("headers") or {},
                            "duration_ms": response_bound_raw.get("duration_ms"),
                        }
                        governed["response_bound_after_ref"] = (
                            "response_bound_after:"
                            f"{_text(response_bound_path.get('path'))}:"
                            f"{int(response_bound_raw.get('status') or 0)}"
                        )
                        governed["response_bound_observer_operation_ref"] = _text(
                            response_bound_path.get("operation_ref")
                        )
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
                }
                if response_bound_observation:
                    obs["response_bound_observation"] = response_bound_observation
            else:
                obs = _resolve_route_with_fallback(base_url=base_url, method=method, path=path, token=token)
            obs["phase"] = phase
            obs["step_id"] = subject_id
            obs["actor_ref"] = actor_ref
            obs["operation_ref"] = op_ref
            obs["path_template"] = path_template
            obs["request_body_fingerprint"] = request_body_fingerprint
            obs["request_semantics_fingerprint"] = (
                request_semantics_fingerprint
            )
            obs["mutation_class"] = mutation_class
            obs["mutation_selector"] = mutation_selector
            obs["mutation_operator"] = mutation_operator
            if runtime_body_blocked:
                obs["status"] = "blocked_write"
                obs["reason"] = _text(
                    _dict(obs.get("governance_receipt")).get("reason")
                ) or "runtime_body_materialization_blocked"
            observed_status = int(obs.get("status_code") or 0)
            obs["response_observed"] = observed_status > 0
            if _text(step.get("protocol_step")) == "temporal_write":
                temporal_elapsed = int(obs.get("duration_ms") or 0)
                after_state = _observation_state(
                    _dict(obs.get("governance_receipt")).get("after")
                )
                observations.setdefault("temporal_timeline", []).extend([
                    {
                        "event": "trigger",
                        "phase": phase,
                        "step_id": subject_id,
                        "at_ms": 0,
                        "status_code": observed_status,
                    },
                    {
                        "event": "final_observed",
                        "phase": phase,
                        "step_id": subject_id,
                        "at_ms": temporal_elapsed,
                        "status_code": int(after_state.get("status") or 0),
                    },
                ])
            contract_status = (
                "BLOCKED"
                if runtime_body_blocked
                else "OBSERVED"
                if phase == "control" and observed_status > 0
                else "OBSERVED"
                if phase == "treatment" and observed_status > 0
                else "FAILED"
            )
            contract_evidence_receipts.append(build_contract_evidence_receipt(
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
                    "request_semantics_fingerprint": (
                        request_semantics_fingerprint
                    ),
                    "mutation_class": mutation_class,
                    "mutation_selector": mutation_selector,
                    "mutation_operator": mutation_operator,
                    "response_observed": observed_status > 0,
                    "control_succeeded": (
                        200 <= observed_status < 300
                        if phase == "control"
                        else None
                    ),
                },
            ))
            results.append(obs)
            if response_bound_observation:
                results.append(response_bound_observation)
            if phase == "control":
                observations["control_observation"] = obs
                observations["control_actor_ref"] = actor_ref
                if 200 <= int(obs.get("status_code") or 0) < 300:
                    observations["control_succeeded"] = True
                    observations["authorized_control"] = True
            if phase == "treatment":
                observations["treatment_observation"] = obs
                observations["treatment_result"] = obs
                observations["treatment_actor_ref"] = actor_ref
                observations["status_code"] = obs.get("status_code")
                observations["body"] = obs.get("body")

            # Per-step evidence channel, append-only and parallel to the single slots above.
            #
            # Every http-shaped observer and assertion reads control_observation /
            # treatment_observation, which this loop OVERWRITES on each step. So in a plan
            # with more than one step per phase, steps 1..N-1 leave no trace and the
            # experiment still reports a verdict from the last one. An additive channel
            # keyed by step_id is the only way to expose per-step evidence without changing
            # what any existing observer sees.
            #
            # Guarded on len(plan) > 1, so for every existing one-step-per-phase experiment
            # the observations dict is byte-identical and no observer's input changes.
            #
            # Deliberately NOT written into barrier_timeline: that observer returns
            # INDETERMINATE without a release-class event and two distinct participants, so
            # feeding ordinary sequential steps into it would manufacture a degraded
            # concurrency reading out of a process that has no concurrency in it. The shape
            # and placement copy the temporal_timeline writer above.
            if len(plan) > 1:
                observations.setdefault("process_timeline", []).append({
                    "event": "step_observed",
                    "phase": phase,
                    "step_id": subject_id,
                    "step_ordinal": index + 1,
                    "step_count": len(plan),
                    "operation_ref": op_ref,
                    "actor_ref": actor_ref,
                    "status_code": observed_status,
                    "after_state": _observation_state(
                        _dict(obs.get("governance_receipt")).get("after")
                    ),
                })
        return results

    steps: list[dict[str, Any]] = []
    steps.extend(_exec_plan(
        [
            step for step in control_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="control",
    ))
    steps.extend(_exec_plan(
        [
            step for step in treatment_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="treatment",
    ))
    return {
        "steps": steps,
        "contract_evidence_receipts": contract_evidence_receipts,
        "request_bodies_for_cleanup": request_bodies_for_cleanup,
        "pre_transport_block_reasons": pre_transport_block_reasons,
        "cleanup_failures": cleanup_failures,
    }
