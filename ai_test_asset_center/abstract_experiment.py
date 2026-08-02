"""Abstract experiment + materialization receipt contracts.

Phase-2 Fact→Experiment: retain business verification intent when concrete IDs
are missing, then materialize capabilities before concrete COMPILED status.

Schemas:
  qualibug.abstract-experiment.v1
  qualibug.experiment-materialization-receipt.v1
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

ABSTRACT_SCHEMA = "qualibug.abstract-experiment.v1"
MATERIALIZATION_SCHEMA = "qualibug.experiment-materialization-receipt.v1"

# Capability gaps that must become ABSTRACT (intent retained), not silent loss.
CAPABILITY_GAP_REASONS = frozenset(
    {
        "BLOCKED_MISSING_OPERATION",
        "BLOCKED_MISSING_ACTOR",
        "BLOCKED_MISSING_FIXTURE",
        "BLOCKED_MISSING_BINDING",
        "BLOCKED_MISSING_OBSERVER",
        "BLOCKED_CONTROL_ARM_NOT_PROVEN",
        "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE",
        "BLOCKED_NON_REVERSIBLE_WRITE",
        "BLOCKED_INVALID_CLEANUP_PLAN",
        "BLOCKED_STEP_CLEANUP_UNCOVERED",
        "BLOCKED_PRECONDITION_UNREACHABLE",
        "BLOCKED_BINDING_LOCATION_NOT_MATERIALIZABLE",
        "STATE_RULE_PRECONDITION_NOT_ESTABLISHED",
        "DB_CLEANUP_AUTHORITY_NOT_DECLARED",
        "DB_ROW_IDENTITY_NOT_BOUND",
    }
)

MATERIALIZATION_STATUSES = frozenset(
    {
        "MATERIALIZED",
        "NOT_MATERIALIZED",
        "PARTIAL",
        "SKIPPED_DIRECT_COMPILE",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in _list(values):
        item = _text(value)
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def is_capability_gap_reason(reason_code: Any) -> bool:
    return _text(reason_code) in CAPABILITY_GAP_REASONS


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(part, (dict, list, tuple))
        else _text(part)
        for part in parts
    )
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def extract_control_treatment_arms(obligation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    prop = _dict(obligation.get("property"))
    control = {
        "actor_ref": _text(
            prop.get("control_actor_ref")
            or prop.get("owner_actor_ref")
            or prop.get("actor_ref")
        ),
        "operation_refs": _unique(obligation.get("required_operations")),
    }
    treatment = {
        "actor_ref": _text(
            prop.get("treatment_actor_ref")
            or prop.get("viewer_actor_ref")
            or prop.get("actor_ref")
        ),
        "operation_refs": _unique(obligation.get("required_operations")),
        "risk_family": _text(obligation.get("risk_family")),
    }
    return control, treatment


def build_required_capabilities(obligation: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "operations": _unique(obligation.get("required_operations")),
        "actors": _unique(obligation.get("required_actors")),
        "fixtures": _unique(obligation.get("required_fixtures")),
        "observers": _unique(obligation.get("required_observers")),
        "cleanup": (
            ["required"]
            if bool(_dict(obligation.get("cleanup_requirement")).get("required"))
            or _text(obligation.get("cleanup_requirement")).lower()
            in {"required", "true", "1", "yes"}
            else []
        ),
    }


def promote_blocked_to_abstract(
    experiment: dict[str, Any],
    obligation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retain blocked capability-gap experiments as ABSTRACT (intent preserved)."""

    obl = _dict(obligation)
    receipt = dict(_dict(experiment.get("compile_receipt")))
    reason = _text(receipt.get("reason_code"))
    detail = _text(receipt.get("detail"))
    control, treatment = extract_control_treatment_arms(obl or experiment)
    capabilities = build_required_capabilities(obl) if obl else {
        "operations": [],
        "actors": [],
        "fixtures": [],
        "observers": [],
        "cleanup": [],
    }
    abstract = dict(experiment)
    abstract["experiment_phase"] = "ABSTRACT"
    abstract["abstract_experiment"] = {
        "schema_version": ABSTRACT_SCHEMA,
        "experiment_id": _text(experiment.get("experiment_id")),
        "fact_refs": _unique(
            [
                *(_list(obl.get("fact_refs"))),
                *(_list(experiment.get("fact_refs"))),
            ]
        ),
        "hypothesis_ref": _text(obl.get("hypothesis_id") or obl.get("candidate_id")),
        "obligation_id": _text(
            experiment.get("obligation_id") or obl.get("obligation_id")
        ),
        "control_arm": control,
        "treatment_arm": treatment,
        "required_pre_state": _dict(obl.get("property")).get("required_pre_state")
        or _dict(obl.get("property")).get("pre_state")
        or {},
        "expected_difference": {
            "risk_family": _text(obl.get("risk_family") or experiment.get("risk_family")),
            "property": _dict(obl.get("property")),
        },
        "disprover": {
            "risk_family": _text(obl.get("risk_family") or experiment.get("risk_family")),
            "blocker_reason": reason,
            "detail": detail,
        },
        "required_capabilities": capabilities,
    }
    abstract["compile_receipt"] = {
        **receipt,
        "status": "ABSTRACT",
        "reason_code": reason,
        "detail": detail,
        "abstract_retained": True,
        "awaiting_materialization": True,
    }
    return abstract


def build_materialization_receipt(
    *,
    experiment_id: str,
    obligation_id: str,
    status: str,
    actor_bindings: dict[str, Any] | None = None,
    credential_bindings: dict[str, Any] | None = None,
    fixture_bindings: dict[str, Any] | None = None,
    state_establishment_steps: list[dict[str, Any]] | None = None,
    operation_bindings: dict[str, Any] | None = None,
    request_bindings: dict[str, Any] | None = None,
    observer_bindings: dict[str, Any] | None = None,
    cleanup_plan: dict[str, Any] | list[Any] | None = None,
    unresolved_requirements: list[dict[str, Any]] | None = None,
    source_blocker: str = "",
) -> dict[str, Any]:
    resolved_status = _text(status).upper() or "NOT_MATERIALIZED"
    if resolved_status not in MATERIALIZATION_STATUSES:
        resolved_status = "NOT_MATERIALIZED"
    unresolved = [
        dict(row) for row in _list(unresolved_requirements) if isinstance(row, dict)
    ]
    receipt = {
        "schema_version": MATERIALIZATION_SCHEMA,
        "receipt_id": _stable_id(
            "emat",
            experiment_id,
            obligation_id,
            resolved_status,
            unresolved,
        ),
        "experiment_id": _text(experiment_id),
        "obligation_id": _text(obligation_id),
        "status": resolved_status,
        "actor_bindings": dict(actor_bindings or {}),
        "credential_bindings": dict(credential_bindings or {}),
        "fixture_bindings": dict(fixture_bindings or {}),
        "state_establishment_steps": [
            dict(row) for row in _list(state_establishment_steps) if isinstance(row, dict)
        ],
        "operation_bindings": dict(operation_bindings or {}),
        "request_bindings": dict(request_bindings or {}),
        "observer_bindings": dict(observer_bindings or {}),
        "cleanup_plan": (
            dict(cleanup_plan)
            if isinstance(cleanup_plan, dict)
            else {"steps": list(cleanup_plan or [])}
        ),
        "unresolved_requirements": unresolved,
        "source_blocker": _text(source_blocker),
        "created_at": _now_iso(),
    }
    return receipt


def attach_passthrough_materialization(experiment: dict[str, Any]) -> dict[str, Any]:
    """Concrete experiments that compiled without gaps still carry a receipt."""

    if _dict(experiment.get("materialization_receipt")).get("status"):
        return experiment
    result = dict(experiment)
    result["materialization_receipt"] = build_materialization_receipt(
        experiment_id=_text(experiment.get("experiment_id")),
        obligation_id=_text(experiment.get("obligation_id")),
        status="SKIPPED_DIRECT_COMPILE",
        actor_bindings={},
        fixture_bindings={},
        observer_bindings={
            _text(row.get("observer_id") or row.get("id")): row
            for row in _list(experiment.get("observers"))
            if isinstance(row, dict)
            and _text(row.get("observer_id") or row.get("id"))
        },
        cleanup_plan={"steps": list(_list(experiment.get("cleanup_plan")))},
        unresolved_requirements=[],
        source_blocker="",
    )
    result["experiment_phase"] = _text(result.get("experiment_phase")) or "CONCRETE"
    return result


__all__ = [
    "ABSTRACT_SCHEMA",
    "MATERIALIZATION_SCHEMA",
    "CAPABILITY_GAP_REASONS",
    "MATERIALIZATION_STATUSES",
    "is_capability_gap_reason",
    "extract_control_treatment_arms",
    "build_required_capabilities",
    "promote_blocked_to_abstract",
    "build_materialization_receipt",
    "attach_passthrough_materialization",
]
