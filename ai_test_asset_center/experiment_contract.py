"""Executable experiment contract schema."""
from __future__ import annotations

import hashlib
from typing import Any


SCHEMA_VERSION = "qualibug.experiment.v1"

BLOCK_REASONS = (
    "BLOCKED_MISSING_OPERATION",
    "BLOCKED_MISSING_ACTOR",
    "BLOCKED_MISSING_FIXTURE",
    "BLOCKED_MISSING_BINDING",
    "BLOCKED_MISSING_OBSERVER",
    "BLOCKED_NON_REVERSIBLE_WRITE",
    "BLOCKED_CONFLICTING_SOURCE",
    "BLOCKED_UNSUPPORTED_ADAPTER",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def stable_experiment_id(obligation_id: str, *parts: Any) -> str:
    raw = "|".join([_text(obligation_id), *(_text(p) for p in parts if _text(p))])
    return f"exp_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def make_experiment(
    *,
    obligation_id: str,
    policy_version: str = "",
    control_plan: list[dict[str, Any]] | None = None,
    treatment_plan: list[dict[str, Any]] | None = None,
    binding_plan: list[dict[str, Any]] | None = None,
    setup_plan: list[dict[str, Any]] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    observers: list[dict[str, Any]] | None = None,
    async_observation_policy: dict[str, Any] | None = None,
    cleanup_plan: list[dict[str, Any]] | None = None,
    safety_contract: dict[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    compile_receipt: dict[str, Any] | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    eid = _text(experiment_id) or stable_experiment_id(obligation_id, "v1")
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": eid,
        "obligation_id": _text(obligation_id),
        "policy_version": _text(policy_version),
        "control_plan": list(control_plan or []),
        "treatment_plan": list(treatment_plan or []),
        "binding_plan": list(binding_plan or []),
        "setup_plan": list(setup_plan or []),
        "assertions": list(assertions or []),
        "observers": list(observers or []),
        "async_observation_policy": dict(async_observation_policy or {"mode": "bounded_backoff"}),
        "cleanup_plan": list(cleanup_plan or []),
        "safety_contract": dict(safety_contract or {"environment": "non_production_required"}),
        "source_refs": list(source_refs or []),
        "compile_receipt": dict(compile_receipt or {"status": "COMPILED"}),
    }


def blocked_experiment(obligation_id: str, reason_code: str, detail: str = "") -> dict[str, Any]:
    code = reason_code if reason_code in BLOCK_REASONS else "BLOCKED_UNSUPPORTED_ADAPTER"
    return make_experiment(
        obligation_id=obligation_id,
        compile_receipt={
            "status": "BLOCKED",
            "reason_code": code,
            "detail": _text(detail),
        },
        experiment_id=stable_experiment_id(obligation_id, code),
    )
