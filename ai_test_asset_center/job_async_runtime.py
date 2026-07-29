"""Bridge source-backed Job adapters into the existing formal execution spine.

This module owns only asynchronous adapter transport.  It deliberately reuses the
existing ProcessStepLedger, contract evidence, cleanup/restoration receipts and
Finalizer.  It does not create a second planner, executor, Oracle, finding gate or
receipt ledger.

The first formal slice is intentionally narrow: only source-declared READ_ONLY
Jobs may execute autonomously.  Jobs that can write business state remain blocked
until their existing cleanup/restoration contract is executable through the normal
QualiBug cleanup chain.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from .contract_oracles import build_contract_evidence_receipt
from .job_platform_contract import ASYNC_OPERATION_KIND, get_job_platform_adapter
from .process_step_execution import ProcessStepLedger, attach_ledger_refs_to_observations

JOB_ASYNC_RUNTIME_SCHEMA = "qualibug.job-async-runtime.v1"
JOB_RUNTIME_INTEGRITY_ORACLE_SCHEMA = "qualibug.job-runtime-integrity-oracle.v1"

JOB_CONNECTOR_IDENTITY_UNRESOLVED = "JOB_CONNECTOR_IDENTITY_UNRESOLVED"
JOB_CONNECTOR_NOT_FOUND = "JOB_CONNECTOR_NOT_FOUND"
JOB_PLATFORM_ADAPTER_NOT_REGISTERED = "JOB_PLATFORM_ADAPTER_NOT_REGISTERED"
JOB_OPERATION_NOT_EXECUTION_READY = "JOB_OPERATION_NOT_EXECUTION_READY"
JOB_SUCCESS_STATE_CONTRACT_UNRESOLVED = "JOB_SUCCESS_STATE_CONTRACT_UNRESOLVED"
JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED = "JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED"
JOB_TRIGGER_FAILED = "JOB_TRIGGER_FAILED"
JOB_RUN_IDENTITY_NOT_RETURNED = "JOB_RUN_IDENTITY_NOT_RETURNED"
JOB_RUN_STATUS_NOT_OBSERVED = "JOB_RUN_STATUS_NOT_OBSERVED"
JOB_ADAPTER_TRANSPORT_RECEIPT_INVALID = "JOB_ADAPTER_TRANSPORT_RECEIPT_INVALID"
JOB_ASYNC_CONVERGENCE_TIMEOUT = "JOB_ASYNC_CONVERGENCE_TIMEOUT"
JOB_TERMINAL_FAILURE = "JOB_TERMINAL_FAILURE"
JOB_ASYNC_PLAN_SHAPE_UNSUPPORTED = "JOB_ASYNC_PLAN_SHAPE_UNSUPPORTED"

_GENERIC_SUCCESS_STATES = frozenset(
    {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "DONE", "FINISHED", "OK"}
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(part, (dict, list, tuple, set))
        else _text(part)
        for part in parts
    )
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _normalized_states(values: Any) -> list[str]:
    rows: list[str] = []
    for value in _list(values):
        state = _text(value).upper()
        if state and state not in rows:
            rows.append(state)
    return rows


def _status_code(payload: dict[str, Any]) -> int:
    row = _dict(payload)
    transport = _dict(row.get("transport"))
    for value in (
        row.get("status_code"),
        row.get("http_status"),
        transport.get("status_code"),
        transport.get("status"),
    ):
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            continue
        if parsed:
            return parsed
    return 0


def _status_text(payload: dict[str, Any]) -> str:
    row = _dict(payload)
    body = _dict(row.get("body"))
    data = _dict(row.get("data"))
    for value in (
        row.get("status"),
        row.get("state"),
        row.get("run_status"),
        body.get("status"),
        body.get("state"),
        data.get("status"),
        data.get("state"),
    ):
        text = _text(value).upper()
        if text:
            return text
    return ""


def _job_run_id(payload: dict[str, Any]) -> str:
    row = _dict(payload)
    candidates: list[str] = []
    for source in (row, _dict(row.get("body")), _dict(row.get("data")), _dict(row.get("result"))):
        for key in ("job_run_id", "run_id", "execution_id"):
            value = _text(source.get(key))
            if value and value not in candidates:
                candidates.append(value)
    return candidates[0] if len(candidates) == 1 else ""


def _connector_id(operation: dict[str, Any]) -> str:
    contract = _dict(operation.get("async_contract"))
    runtime = _dict(contract.get("runtime"))
    candidates: list[str] = []
    for value in (contract.get("connector_id"), runtime.get("connector_id")):
        text = _text(value)
        if text and text not in candidates:
            candidates.append(text)
    for evidence in _list(operation.get("evidence")):
        if not isinstance(evidence, dict):
            continue
        text = _text(evidence.get("connector_id"))
        if text and text not in candidates:
            candidates.append(text)
    return candidates[0] if len(candidates) == 1 else ""


def _terminal_contract(operation: dict[str, Any]) -> tuple[list[str], list[str]]:
    contract = _dict(operation.get("async_contract"))
    runtime = _dict(contract.get("runtime"))
    terminal_states = _normalized_states(runtime.get("terminal_states"))
    success_states = _normalized_states(
        runtime.get("success_states") or contract.get("success_states")
    )
    if not success_states:
        # Classification is limited to generic adapter execution semantics.  The
        # terminal values themselves still have to be source declared.
        success_states = [state for state in terminal_states if state in _GENERIC_SUCCESS_STATES]
    if success_states and not terminal_states:
        terminal_states = list(success_states)
    return terminal_states, success_states


def _receipt(
    receipt_id: str,
    schema_version: str,
    *,
    step_id: str,
    payload: dict[str, Any],
    status: str = "VALID",
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "receipt_id": receipt_id,
        "step_id": step_id,
        "status": status,
        **dict(payload),
    }


def _blocked(reason_code: str, detail: str, *, platform_reached: bool = False) -> dict[str, Any]:
    return {
        "schema_version": JOB_ASYNC_RUNTIME_SCHEMA,
        "ok": False,
        "reason_code": reason_code,
        "detail": detail,
        "platform_reached": platform_reached,
        "status_code": 0,
        "receipts": {},
        "adapter_call_count": 0,
        "formal_business_finding_eligible": False,
    }


def execute_registered_job_operation(
    *,
    operation: dict[str, Any],
    connector_registry: dict[str, Any],
    parameters: dict[str, Any] | None = None,
    step_id: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Trigger one registered Job once, then observe it to a declared terminal.

    No mutating trigger is retried after possible acceptance.  Polling is bounded
    and only status reads are repeated.
    """
    op = _dict(operation)
    contract = _dict(op.get("async_contract"))
    testability = _dict(contract.get("testability"))
    if _text(op.get("operation_kind")) != ASYNC_OPERATION_KIND or not contract:
        return _blocked(JOB_OPERATION_NOT_EXECUTION_READY, "operation_is_not_async_job")
    if _text(testability.get("execution_status")) != "EXECUTION_READY":
        return _blocked(
            JOB_OPERATION_NOT_EXECUTION_READY,
            _text(testability.get("execution_status")) or "execution_status_missing",
        )
    write_set = [_text(value) for value in _list(contract.get("write_set")) if _text(value)]
    safety_level = _text(testability.get("safety_level"))
    if write_set or safety_level != "READ_ONLY":
        return _blocked(
            JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED,
            f"safety_level={safety_level or 'UNKNOWN'}:write_set={len(write_set)}",
        )

    connector_id = _connector_id(op)
    if not connector_id:
        return _blocked(JOB_CONNECTOR_IDENTITY_UNRESOLVED, "exactly_one_connector_id_required")
    connectors = [row for row in _list(connector_registry.get("connectors")) if isinstance(row, dict)]
    connector = next(
        (
            row
            for row in connectors
            if _text(row.get("connector_id")) == connector_id and bool(row.get("enabled", True))
        ),
        None,
    )
    if connector is None:
        return _blocked(JOB_CONNECTOR_NOT_FOUND, connector_id)

    platform_type = _text(contract.get("platform_type")).lower()
    adapter = get_job_platform_adapter(platform_type)
    if adapter is None:
        return _blocked(JOB_PLATFORM_ADAPTER_NOT_REGISTERED, platform_type)

    terminal_states, success_states = _terminal_contract(op)
    if not terminal_states or not success_states:
        return _blocked(
            JOB_SUCCESS_STATE_CONTRACT_UNRESOLVED,
            f"terminal={terminal_states}:success={success_states}",
        )

    platform_job_id = _text(contract.get("platform_job_id"))
    policy = _dict(contract.get("execution_policy"))
    runtime = _dict(contract.get("runtime"))
    try:
        max_attempts = max(1, min(60, int(policy.get("max_attempts") or 12)))
    except (TypeError, ValueError):
        max_attempts = 12
    try:
        backoff_seconds = max(0.0, min(5.0, float(policy.get("backoff_ms") or 250) / 1000.0))
    except (TypeError, ValueError):
        backoff_seconds = 0.25

    call_count = 0
    try:
        trigger = adapter.trigger_job(connector, platform_job_id, dict(parameters or {}))
        call_count += 1
    except Exception as exc:
        result = _blocked(JOB_TRIGGER_FAILED, f"{type(exc).__name__}: {exc}")
        result["adapter_call_count"] = call_count
        return result
    trigger = _dict(trigger)
    trigger_status = _status_code(trigger)
    if not (200 <= trigger_status < 400):
        result = _blocked(
            JOB_TRIGGER_FAILED,
            f"trigger_status_code={trigger_status}",
            platform_reached=trigger_status > 0,
        )
        result["adapter_call_count"] = call_count
        return result
    run_id = _job_run_id(trigger)
    if not run_id:
        result = _blocked(
            JOB_RUN_IDENTITY_NOT_RETURNED,
            "adapter_trigger_response_missing_unambiguous_job_run_id",
            platform_reached=True,
        )
        result["adapter_call_count"] = call_count
        return result

    poll_rows: list[dict[str, Any]] = []
    terminal_run: dict[str, Any] = {}
    terminal_status = ""
    terminal_success: bool | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            run = _dict(adapter.get_job_run(connector, platform_job_id, run_id))
            call_count += 1
        except Exception as exc:
            result = _blocked(
                JOB_RUN_STATUS_NOT_OBSERVED,
                f"{type(exc).__name__}: {exc}",
                platform_reached=True,
            )
            result.update({"job_run_id": run_id, "adapter_call_count": call_count})
            return result
        code = _status_code(run)
        status = _status_text(run)
        poll_rows.append(
            {
                "attempt": attempt,
                "status_code": code,
                "job_status": status,
                "terminal": bool(run.get("terminal")) or status in terminal_states,
            }
        )
        if not (200 <= code < 400) or not status:
            result = _blocked(
                JOB_RUN_STATUS_NOT_OBSERVED,
                f"poll_attempt={attempt}:status_code={code}:job_status={status or '<empty>'}",
                platform_reached=True,
            )
            result.update({"job_run_id": run_id, "polls": poll_rows, "adapter_call_count": call_count})
            return result
        is_terminal = bool(run.get("terminal")) or status in terminal_states
        if is_terminal:
            explicit_success = run.get("success")
            if not isinstance(explicit_success, bool):
                explicit_success = run.get("successful")
            terminal_success = (
                explicit_success
                if isinstance(explicit_success, bool)
                else status in success_states
            )
            terminal_run = run
            terminal_status = status
            break
        if attempt < max_attempts and backoff_seconds:
            sleeper(backoff_seconds)

    if not terminal_run:
        cancel_receipt: dict[str, Any] = {}
        if _text(runtime.get("cancel_ref")):
            try:
                cancel_receipt = _dict(adapter.cancel_job(connector, platform_job_id, run_id))
                call_count += 1
            except Exception as exc:
                cancel_receipt = {"error": f"{type(exc).__name__}: {exc}"}
        result = _blocked(
            JOB_ASYNC_CONVERGENCE_TIMEOUT,
            f"poll_attempts_exhausted={max_attempts}",
            platform_reached=True,
        )
        result.update(
            {
                "job_run_id": run_id,
                "polls": poll_rows,
                "cancel_receipt": cancel_receipt,
                "adapter_call_count": call_count,
            }
        )
        return result

    observed_steps: list[dict[str, Any]] = []
    if _text(runtime.get("step_query_ref")):
        try:
            observed_steps = [
                dict(row)
                for row in adapter.list_job_steps(connector, platform_job_id, run_id)
                if isinstance(row, dict)
            ]
            call_count += 1
        except Exception as exc:
            result = _blocked(
                JOB_RUN_STATUS_NOT_OBSERVED,
                f"step_observation_failed:{type(exc).__name__}: {exc}",
                platform_reached=True,
            )
            result.update({"job_run_id": run_id, "polls": poll_rows, "adapter_call_count": call_count})
            return result

    log_receipt: dict[str, Any] = {}
    if _text(runtime.get("log_query_ref")):
        try:
            log_receipt = _dict(adapter.get_job_log(connector, platform_job_id, run_id))
            call_count += 1
        except Exception as exc:
            log_receipt = {"error": f"{type(exc).__name__}: {exc}"}

    transport_id = _stable_id("job_transport", connector_id, platform_job_id, run_id)
    observation_id = _stable_id("job_observation", connector_id, platform_job_id, run_id, terminal_status)
    oracle_id = _stable_id("job_oracle", connector_id, platform_job_id, run_id, terminal_status)
    trace_id = _stable_id("job_oracle_trace", connector_id, platform_job_id, run_id, terminal_status)
    cleanup_exec_id = _stable_id("job_cleanup_exec", connector_id, platform_job_id, run_id)
    cleanup_ver_id = _stable_id("job_cleanup_ver", connector_id, platform_job_id, run_id)
    restoration_id = _stable_id("job_restoration", connector_id, platform_job_id, run_id)

    receipts = {
        "transport": _receipt(
            transport_id,
            "qualibug.transport-receipt.v1",
            step_id=step_id,
            payload={
                "connector_id": connector_id,
                "platform_type": platform_type,
                "platform_job_id": platform_job_id,
                "job_run_id": run_id,
                "trigger_status_code": trigger_status,
                "adapter_call_count": call_count,
                "mutating_trigger_retried": False,
            },
        ),
        "observation": _receipt(
            observation_id,
            "qualibug.observation-receipt.v1",
            step_id=step_id,
            payload={
                "job_run_id": run_id,
                "job_status": terminal_status,
                "terminal": True,
                "polls": poll_rows,
                "job_steps": observed_steps,
                "log_receipt": log_receipt,
            },
        ),
        "oracle_invocation": _receipt(
            oracle_id,
            JOB_RUNTIME_INTEGRITY_ORACLE_SCHEMA,
            step_id=step_id,
            payload={
                "assertion": "source_declared_job_terminal_success",
                "terminal_states": terminal_states,
                "success_states": success_states,
                "observed_status": terminal_status,
                "passed": bool(terminal_success),
                "formal_business_finding_eligible": False,
            },
            status="VALID" if terminal_success else "INCOMPLETE",
        ),
        "oracle_trace": _receipt(
            trace_id,
            "qualibug.oracle-trace-receipt.v1",
            step_id=step_id,
            payload={
                "trace_kind": "polling",
                "job_run_id": run_id,
                "poll_count": len(poll_rows),
                "terminal_status": terminal_status,
                "terminal_success": bool(terminal_success),
            },
            status="VALID" if terminal_success else "INCOMPLETE",
        ),
        "cleanup_execution": _receipt(
            cleanup_exec_id,
            "qualibug.cleanup-execution-receipt.v1",
            step_id=step_id,
            payload={
                "cleanup_status": "NOT_REQUIRED",
                "reason_code": "READ_ONLY_JOB_NO_BUSINESS_WRITE",
                "verified": True,
            },
        ),
        "cleanup_verification": _receipt(
            cleanup_ver_id,
            "qualibug.cleanup-verification-receipt.v1",
            step_id=step_id,
            payload={
                "equivalence_status": "NOT_APPLICABLE",
                "reason_code": "CLEANUP_NOT_REQUIRED",
                "verified": True,
            },
        ),
        "environment_restoration": _receipt(
            restoration_id,
            "qualibug.environment-restoration-receipt.v1",
            step_id=step_id,
            payload={
                "environment_restored": True,
                "restored": True,
                "reason_code": "READ_ONLY_JOB_NO_BUSINESS_WRITE",
            },
        ),
    }

    if not terminal_success:
        return {
            "schema_version": JOB_ASYNC_RUNTIME_SCHEMA,
            "ok": False,
            "reason_code": JOB_TERMINAL_FAILURE,
            "detail": f"terminal_status={terminal_status}",
            "platform_reached": True,
            "status_code": _status_code(terminal_run),
            "job_run_id": run_id,
            "terminal_status": terminal_status,
            "terminal_success": False,
            "polls": poll_rows,
            "receipts": receipts,
            "adapter_call_count": call_count,
            "formal_business_finding_eligible": False,
        }

    return {
        "schema_version": JOB_ASYNC_RUNTIME_SCHEMA,
        "ok": True,
        "reason_code": "",
        "detail": "",
        "platform_reached": True,
        "status_code": _status_code(terminal_run) or 200,
        "job_run_id": run_id,
        "terminal_status": terminal_status,
        "terminal_success": True,
        "polls": poll_rows,
        "job_steps": observed_steps,
        "receipts": receipts,
        "adapter_call_count": call_count,
        "formal_business_finding_eligible": False,
    }


def _job_steps(plan: list[Any], ops: dict[str, dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in plan:
        if not isinstance(raw, dict):
            continue
        operation = _dict(ops.get(_text(raw.get("operation_ref"))))
        if _text(operation.get("operation_kind")) == ASYNC_OPERATION_KIND:
            result.append((raw, operation))
    return result


def execute_async_job_plan(
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
    """Execute a pure one-step ASYNC_JOB plan through the existing ledger shape."""
    del tokens, runtime_contract, base_url
    control_jobs = _job_steps(control_plan, ops)
    treatment_jobs = _job_steps(treatment_plan, ops)
    required: list[str] = []
    for phase in ("control", "treatment"):
        for sid in _list(activation_requirements.get(phase)):
            text = _text(sid)
            if text and text not in required:
                required.append(text)
    fixture_id = _text(_dict(observations.get("disposable_fixture_contract")).get("fixture_id"))
    protocol_id = _text(observations.get("protocol_id") or _dict(observations.get("protocol")).get("protocol_id"))
    ledger = ProcessStepLedger(
        experiment_id=eid,
        fixture_id=fixture_id,
        campaign_id=_text(resolved_campaign_id or campaign_id),
        run_id=_text(resolved_execution_id),
        obligation_id=_text(oid),
        protocol_id=protocol_id,
        required_step_ids=required,
    )
    attach_ledger_refs_to_observations(observations, ledger)

    shape_valid = (
        not control_plan
        and len(treatment_jobs) == 1
        and len([row for row in treatment_plan if isinstance(row, dict)]) == 1
        and not consumed_barrier_steps
    )
    if not shape_valid:
        reason = JOB_ASYNC_PLAN_SHAPE_UNSUPPORTED
        return {
            "steps": [],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [reason],
            "cleanup_failures": cleanup_failures,
            "process_step_ledger": ledger,
            "process_step_ledger_id": ledger.ledger_id,
            "process_step_ledger_hash": ledger.compute_hash(),
            "process_timeline": ledger.build_timeline_receipt(),
            "required_step_ids": required,
            "planned_step_ids": required,
            "executed_step_ids": [],
        }

    step, operation = treatment_jobs[0]
    step_id = _text(step.get("step_id")) or (required[0] if len(required) == 1 else "")
    actor_ref = _text(step.get("actor_ref"))
    op_ref = _text(step.get("operation_ref"))
    preflight_reason = ""
    if not step_id or step_id not in required:
        preflight_reason = "JOB_REQUIRED_STEP_IDENTITY_MISMATCH"
    elif not actor_ref or actor_ref not in actors:
        preflight_reason = "BLOCKED_MISSING_ACTOR"
    elif not op_ref or op_ref not in ops:
        preflight_reason = "BLOCKED_MISSING_OPERATION"
    if preflight_reason:
        return {
            "steps": [],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [preflight_reason],
            "cleanup_failures": cleanup_failures,
            "process_step_ledger": ledger,
            "process_step_ledger_id": ledger.ledger_id,
            "process_step_ledger_hash": ledger.compute_hash(),
            "process_timeline": ledger.build_timeline_receipt(),
            "required_step_ids": required,
            "planned_step_ids": required,
            "executed_step_ids": [],
        }

    from .enterprise_pilot_runtime import load_connector_registry

    registry = load_connector_registry(project, root)
    result = execute_registered_job_operation(
        operation=operation,
        connector_registry=registry,
        parameters=dict(runtime_bindings),
        step_id=step_id,
    )
    receipts = _dict(result.get("receipts"))
    transport = _dict(receipts.get("transport"))
    observation = _dict(receipts.get("observation"))
    oracle = _dict(receipts.get("oracle_invocation"))
    oracle_trace = _dict(receipts.get("oracle_trace"))
    cleanup_exec = _dict(receipts.get("cleanup_execution"))
    cleanup_ver = _dict(receipts.get("cleanup_verification"))
    environment = _dict(receipts.get("environment_restoration"))

    for key, row in (
        ("transport_receipts", transport),
        ("observation_receipts", observation),
        ("oracle_invocation_receipts", oracle),
        ("oracle_trace_receipts", oracle_trace),
        ("cleanup_execution_receipts", cleanup_exec),
        ("cleanup_verification_receipts", cleanup_ver),
    ):
        if row:
            observations.setdefault(key, []).append(row)
    if environment:
        observations["environment_restoration_receipt"] = environment
    observations["job_runtime_receipt"] = dict(result)
    observations["formal_business_finding_eligible"] = False
    observations["state_precondition_established"] = True

    status_code = int(result.get("status_code") or 0)
    reached = bool(result.get("platform_reached"))
    successful = bool(result.get("ok"))
    transport_id = _text(transport.get("receipt_id"))
    observation_id = _text(observation.get("receipt_id"))
    oracle_id = _text(oracle.get("receipt_id"))
    cleanup_id = _text(cleanup_exec.get("receipt_id"))
    ledger.record_step_execution(
        step_id=step_id,
        phase="treatment",
        operation_ref=op_ref,
        actor_ref=actor_ref,
        runtime_identity={"job_run_id": result.get("job_run_id")},
        request_receipt_id=transport_id,
        response_receipt_id=observation_id,
        transport_receipt_id=transport_id,
        observer_receipt_ids=[observation_id] if observation_id else [],
        oracle_receipt_ids=[oracle_id] if oracle_id else [],
        cleanup_receipt_ids=[cleanup_id] if cleanup_id and successful else [],
        status_code=status_code,
        final_status="EXECUTED" if successful else "BLOCKED",
        mutation_occurred=False,
        target_reached=reached,
    )
    ledger.record_timeline_event(
        step_id=step_id,
        phase="treatment",
        event_type="STEP_COMPLETED" if successful else "STEP_FAILED",
        operation_ref=op_ref,
        actor_ref=actor_ref,
        receipt_id=transport_id,
    )
    attach_ledger_refs_to_observations(observations, ledger)

    step_result = {
        "phase": "treatment",
        "step_id": step_id,
        "operation_ref": op_ref,
        "actor_ref": actor_ref,
        "method": "JOB",
        "path": f"{_text(_dict(operation.get('async_contract')).get('platform_type'))}:{_text(_dict(operation.get('async_contract')).get('platform_job_id'))}",
        "status": "executed" if successful else "blocked_job_runtime",
        "status_code": status_code,
        "body": {
            "job_run_id": result.get("job_run_id"),
            "terminal_status": result.get("terminal_status"),
            "terminal_success": result.get("terminal_success"),
        },
        "error": "" if successful else _text(result.get("reason_code")),
        "governance_receipt": {
            "receipt_id": transport_id,
            "method": "ADAPTER_JOB_READ_ONLY",
            "http_attempt_count": int(result.get("adapter_call_count") or 0),
            "write_request_attempt_count": 0,
            "production_http_requests": 0,
            "accepted": False,
            "target_reached": reached,
        },
        "job_runtime_receipt": dict(result),
    }
    observations["treatment_observation"] = step_result
    observations["treatment_result"] = step_result
    observations["treatment_actor_ref"] = actor_ref
    observations["status_code"] = status_code
    observations["body"] = step_result["body"]
    if not successful:
        observations["harness_error"] = _text(result.get("reason_code"))

    contract_receipt = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
        subject_id=step_id,
        status="OBSERVED" if successful else "BLOCKED",
        evidence={
            "operation_ref": op_ref,
            "job_run_id": result.get("job_run_id"),
            "terminal_status": result.get("terminal_status"),
            "terminal_success": result.get("terminal_success"),
            "formal_business_finding_eligible": False,
            "reason_code": result.get("reason_code"),
        },
    )
    block_reasons = [] if successful else [_text(result.get("reason_code")) or JOB_RUN_STATUS_NOT_OBSERVED]
    return {
        "steps": [step_result],
        "contract_evidence_receipts": [contract_receipt],
        "request_bodies_for_cleanup": {},
        "pre_transport_block_reasons": block_reasons,
        "cleanup_failures": cleanup_failures,
        "process_step_ledger": ledger,
        "process_step_ledger_id": ledger.ledger_id,
        "process_step_ledger_hash": ledger.compute_hash(),
        "process_timeline": ledger.build_timeline_receipt(),
        "required_step_ids": required,
        "planned_step_ids": required,
        "executed_step_ids": ledger.executed_step_ids(),
    }


def install_job_async_execution_adapter() -> Callable[..., dict[str, Any]]:
    """Install one transport dispatch facade on the existing plan executor.

    Non-Job plans delegate byte-for-byte to the pre-existing implementation.  A
    pure ASYNC_JOB plan uses ``execute_async_job_plan`` and still returns the same
    result contract consumed by ``experiment_executor`` and the existing Finalizer.
    """
    from . import experiment_executor as executor_module
    from . import experiment_plan_executor as plan_module

    current = plan_module.execute_non_barrier_plans
    if getattr(current, "_qualibug_job_async_adapter", False):
        return current
    original = current

    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        ops = _dict(kwargs.get("ops"))
        control = _list(kwargs.get("control_plan"))
        treatment = _list(kwargs.get("treatment_plan"))
        has_job = bool(_job_steps(control, ops) or _job_steps(treatment, ops))
        if not has_job:
            return original(*args, **kwargs)
        return execute_async_job_plan(*args, **kwargs)

    wrapped._qualibug_job_async_adapter = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_executor = original  # type: ignore[attr-defined]
    plan_module.execute_non_barrier_plans = wrapped
    # experiment_executor imported the symbol directly; update that existing call
    # site as well so no second runtime entrypoint is introduced.
    executor_module.execute_non_barrier_plans = wrapped
    return wrapped


__all__ = [
    "JOB_ASYNC_RUNTIME_SCHEMA",
    "JOB_RUNTIME_INTEGRITY_ORACLE_SCHEMA",
    "JOB_CONNECTOR_IDENTITY_UNRESOLVED",
    "JOB_CONNECTOR_NOT_FOUND",
    "JOB_PLATFORM_ADAPTER_NOT_REGISTERED",
    "JOB_OPERATION_NOT_EXECUTION_READY",
    "JOB_SUCCESS_STATE_CONTRACT_UNRESOLVED",
    "JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED",
    "JOB_TRIGGER_FAILED",
    "JOB_RUN_IDENTITY_NOT_RETURNED",
    "JOB_RUN_STATUS_NOT_OBSERVED",
    "JOB_ADAPTER_TRANSPORT_RECEIPT_INVALID",
    "JOB_ASYNC_CONVERGENCE_TIMEOUT",
    "JOB_TERMINAL_FAILURE",
    "JOB_ASYNC_PLAN_SHAPE_UNSUPPORTED",
    "execute_registered_job_operation",
    "execute_async_job_plan",
    "install_job_async_execution_adapter",
]
