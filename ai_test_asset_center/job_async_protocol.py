"""Register ASYNC_JOB planning through the existing experiment protocol registry.

The compiler produces the same treatment-plan contract consumed by the current
plan executor.  Job transport, ProcessStepLedger, Oracle evaluation and Finalizer
remain owned by their existing authorities.
"""
from __future__ import annotations

from typing import Any

from .job_platform_contract import ASYNC_OPERATION_KIND

TEMPLATE_ASYNC_JOB_EXECUTION = "source_declared_async_job_execution"
ASYNC_JOB_PROTOCOL_NOT_RESOLVED = "ASYNC_JOB_PROTOCOL_NOT_RESOLVED"
ASYNC_JOB_ACTOR_NOT_BOUND = "ASYNC_JOB_ACTOR_NOT_BOUND"
ASYNC_JOB_OPERATION_NOT_BOUND = "ASYNC_JOB_OPERATION_NOT_BOUND"
ASYNC_JOB_NOT_EXECUTION_READY = "ASYNC_JOB_NOT_EXECUTION_READY"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def compile_async_job_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    """Compile one source-backed Job into a one-step asynchronous treatment plan."""
    operation = _dict(envelope.get("operation"))
    operation_ref = _text(envelope.get("operation_ref"))
    actor_ref = _text(envelope.get("treatment_actor_ref") or envelope.get("control_actor_ref"))
    contract = _dict(operation.get("async_contract"))
    testability = _dict(contract.get("testability"))

    if not operation_ref:
        return {
            "status": "BLOCKED",
            "reason_code": ASYNC_JOB_OPERATION_NOT_BOUND,
            "detail": "operation_ref_missing",
        }
    if _text(operation.get("operation_kind")) != ASYNC_OPERATION_KIND or not contract:
        return {
            "status": "BLOCKED",
            "reason_code": ASYNC_JOB_PROTOCOL_NOT_RESOLVED,
            "detail": "operation_is_not_source_backed_async_job",
        }
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": ASYNC_JOB_ACTOR_NOT_BOUND,
            "detail": "treatment_actor_ref_missing",
        }
    if _text(testability.get("execution_status")) != "EXECUTION_READY":
        return {
            "status": "BLOCKED",
            "reason_code": ASYNC_JOB_NOT_EXECUTION_READY,
            "detail": _text(testability.get("execution_status")) or "execution_status_missing",
        }
    if not _list(operation.get("evidence")):
        return {
            "status": "BLOCKED",
            "reason_code": ASYNC_JOB_PROTOCOL_NOT_RESOLVED,
            "detail": "source_evidence_missing",
        }

    step_id = "job_treatment_1"
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": step_id,
                "step_ordinal": 1,
                "operation_ref": operation_ref,
                "actor_ref": actor_ref,
                "method": "JOB",
                "path": "",
                "intent": "source_declared_async_job_execution",
                "protocol_step": "async_job_treatment",
            }
        ],
        "cleanup_plan": [],
        "assertion": {
            "kind": "process_completion",
            "expected_steps": [step_id],
            "runtime_integrity_only": True,
            "formal_business_finding_eligible": False,
        },
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "after_state"},
        ],
        "per_step_evidence": True,
        "requires_state_precondition": False,
        "source_refs": [
            dict(row) for row in _list(operation.get("evidence")) if isinstance(row, dict)
        ],
        "_registry_protocol_id": f"process:{TEMPLATE_ASYNC_JOB_EXECUTION}",
    }


def register_job_async_protocol() -> str:
    """Register the Job compiler on the existing (family, template) authority."""
    # Install the already-existing process observer/assertion surfaces first.  This
    # is idempotent and avoids weakening protocol-registry validation merely to make
    # Job registration order-dependent.
    from .multi_step_protocol import register_v150_multi_step_protocols

    register_v150_multi_step_protocols()
    from .experiment_protocol_registry import (
        register_family_protocol,
        resolve_family_protocol,
    )

    existing = resolve_family_protocol("process", TEMPLATE_ASYNC_JOB_EXECUTION)
    if existing:
        return _text(existing.get("protocol_id"))
    return register_family_protocol(
        "process",
        TEMPLATE_ASYNC_JOB_EXECUTION,
        compiler=compile_async_job_protocol,
        observers=("http_response", "after_state"),
        assertion_kind="process_completion",
        emits_control=False,
        per_step_evidence=True,
    )


__all__ = [
    "TEMPLATE_ASYNC_JOB_EXECUTION",
    "ASYNC_JOB_PROTOCOL_NOT_RESOLVED",
    "ASYNC_JOB_ACTOR_NOT_BOUND",
    "ASYNC_JOB_OPERATION_NOT_BOUND",
    "ASYNC_JOB_NOT_EXECUTION_READY",
    "compile_async_job_protocol",
    "register_job_async_protocol",
]
