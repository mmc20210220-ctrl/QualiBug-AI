"""Oracle Input Contract — compile-time verification of oracle data requirements.

SPEC v1.2 §11: Oracle Input Completeness Recovery

This module verifies at compile time that all assertion-required inputs
can be provided by the planned observers, preventing runtime surprises.

Output: qualibug.oracle-input-contract.v1
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


# ─── Assertion Kind → Required Inputs ────────────────────────────────────────

_ASSERTION_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "authorization": {
        "required_phases": ["control", "treatment"],
        "required_fields": ["status_code"],
        "required_entities": [],
        "requires_control": True,
    },
    "isolation": {
        "required_phases": ["control", "treatment"],
        "required_fields": ["status_code", "body"],
        "required_entities": [],
        "requires_control": True,
    },
    "visibility": {
        "required_phases": ["control", "treatment"],
        "required_fields": ["status_code", "body"],
        "required_entities": [],
        "requires_control": True,
    },
    "state_transition": {
        "required_phases": ["before", "after_write"],
        "required_fields": ["state_field"],
        "required_entities": ["primary"],
        "requires_control": False,
    },
    "conservation": {
        "required_phases": ["before", "after_write", "after_cleanup"],
        "required_fields": ["conserved_field"],
        "required_entities": ["primary"],
        "requires_control": False,
    },
    "idempotency": {
        "required_phases": ["after_write"],
        "required_fields": ["status_code", "entity_state"],
        "required_entities": ["primary"],
        "requires_control": False,
    },
    "validation_rejection": {
        "required_phases": ["treatment"],
        "required_fields": ["status_code", "error_message"],
        "required_entities": [],
        "requires_control": False,
    },
    "concurrency": {
        "required_phases": ["control", "treatment"],
        "required_fields": ["status_code", "body"],
        "required_entities": ["primary"],
        "requires_control": True,
    },
    "cross_surface_consistency": {
        "required_phases": ["after_write"],
        "required_fields": ["entity_state"],
        "required_entities": ["primary", "related"],
        "requires_control": False,
    },
}


# ─── Contract Builder ─────────────────────────────────────────────────────────


def build_oracle_input_contract(
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate the oracle input contract for an experiment.

    Verifies that assertion required inputs ⊆ observer produced inputs.

    Returns:
        qualibug.oracle-input-contract.v1
    """
    exp = _dict(experiment)
    ir = _dict(behavior_ir)

    # Collect all assertions
    assertions = _list(exp.get("assertions"))
    observers = _list(exp.get("observers"))
    safety = _dict(exp.get("safety_contract"))
    is_write = safety.get("governed_write")

    contracts: list[dict[str, Any]] = []
    all_missing: list[str] = []

    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        assertion_kind = _text(assertion.get("kind"))
        assertion_id = _text(assertion.get("assertion_id") or assertion.get("id"))

        # Get requirements for this assertion kind
        requirements = _ASSERTION_REQUIREMENTS.get(assertion_kind, {
            "required_phases": ["after_write"],
            "required_fields": ["status_code"],
            "required_entities": [],
            "requires_control": False,
        })

        required_phases = list(requirements.get("required_phases") or [])
        required_fields = list(requirements.get("required_fields") or [])
        required_entities = list(requirements.get("required_entities") or [])
        requires_control = requirements.get("requires_control", False)

        # Check observer coverage
        observer_ids = {_text(o.get("observer_id")) for o in observers if isinstance(o, dict)}
        observer_kinds = {_text(o.get("kind")) for o in observers if isinstance(o, dict)}

        missing_inputs: list[str] = []

        # Check phase coverage
        if "before" in required_phases and is_write:
            has_before = any(
                k in ("before_state", "entity_state", "state_read")
                for k in observer_kinds | observer_ids
            )
            if not has_before:
                missing_inputs.append("phase:before")

        if "after_write" in required_phases and is_write:
            has_after = any(
                k in ("after_state", "entity_state", "business_effect", "state_read")
                for k in observer_kinds | observer_ids
            )
            if not has_after:
                missing_inputs.append("phase:after_write")

        if "after_cleanup" in required_phases and is_write:
            has_cleanup = any(
                k in ("final_state", "after_cleanup", "entity_state")
                for k in observer_kinds | observer_ids
            )
            if not has_cleanup:
                missing_inputs.append("phase:after_cleanup")

        # Check control plan
        if requires_control:
            control_plan = _list(exp.get("control_plan"))
            if not control_plan:
                missing_inputs.append("control_plan_missing")

        # Coverage status
        coverage_status = "COMPLETE" if not missing_inputs else "INCOMPLETE"
        all_missing.extend(missing_inputs)

        # Fingerprint
        fp_content = {
            "assertion_kind": assertion_kind,
            "required_phases": required_phases,
            "required_fields": required_fields,
            "missing": missing_inputs,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:32]

        contracts.append({
            "schema_version": "qualibug.oracle-input-contract.v1",
            "assertion_id": assertion_id,
            "assertion_kind": assertion_kind,
            "required_entities": required_entities,
            "required_fields": required_fields,
            "required_phases": required_phases,
            "required_relations": [],
            "required_identity_fields": [],
            "required_time_window": None,
            "observer_plan_refs": list(observer_ids),
            "coverage_status": coverage_status,
            "missing_inputs": missing_inputs,
            "fingerprint": fingerprint,
        })

    # Overall status
    overall_status = "COMPLETE" if not all_missing else "INCOMPLETE"

    return {
        "schema_version": "qualibug.oracle-input-contract-batch.v1",
        "experiment_id": _text(exp.get("experiment_id")),
        "obligation_id": _text(exp.get("obligation_id")),
        "contracts": contracts,
        "overall_status": overall_status,
        "total_missing": len(all_missing),
        "missing_inputs": all_missing,
        "reason_code": "" if overall_status == "COMPLETE" else "BLOCKED_MISSING_OBSERVER",
        "detail": "" if overall_status == "COMPLETE" else f"oracle_input_missing:{';'.join(all_missing[:5])}",
    }
