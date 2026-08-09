"""Oracle Input Contract — compile-time verification of oracle data requirements.

SPEC v1.2 §11 + SPEC v1.2.1 §10: Field-level Oracle Input Verification

This module verifies at compile time that all assertion-required inputs
can be provided by the planned observers, using real subset validation:
    required_fields ⊆ produced_fields
    required_phases ⊆ produced_phases
    required_entities ⊆ produced_entities

New Observer Output Contract captures what observers actually produce.
New reason code: BLOCKED_ORACLE_INPUT_INCOMPLETE.

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


# ─── SPEC v1.2.1 §10.1: Observer Output Contract ────────────────────────────


def _build_observer_output_contract(observers: list[dict[str, Any]], exp: dict[str, Any]) -> dict[str, Any]:
    """Build the Observer Output Contract: what the observers actually produce."""
    produced_phases: set[str] = set()
    produced_entities: set[str] = set()
    produced_fields: set[str] = set()
    produced_identity_fields: set[str] = set()
    produced_relations: set[str] = set()

    for obs in observers:
        if not isinstance(obs, dict):
            continue
        kind = _text(obs.get("kind")) or _text(obs.get("observer_id"))
        entity = _text(obs.get("entity_ref") or obs.get("entity"))
        fields = obs.get("produced_fields") or obs.get("fields") or []
        identity_fields = obs.get("identity_fields") or []
        relations = obs.get("produced_relations") or []

        # Infer produced phases from observer kind
        if kind in ("before_state", "entity_state", "state_read"):
            produced_phases.add("before")
        if kind in ("after_state", "entity_state", "business_effect", "state_read"):
            produced_phases.add("after_write")
        if kind in ("final_state", "after_cleanup", "entity_state"):
            produced_phases.add("after_cleanup")
        if kind in ("collection_state", "collection_read"):
            produced_phases.add("after_write")

        # Infer abstract field categories from observer kind
        if kind in ("before_state", "after_state", "entity_state", "state_read", "final_state"):
            produced_fields.add("state_field")
            produced_fields.add("entity_state")
        if kind in ("after_state", "business_effect", "entity_state"):
            produced_fields.add("conserved_field")
        if kind in ("after_state", "entity_state", "business_effect"):
            produced_fields.add("error_message")

        # Explicit phases
        for phase in _list(obs.get("produced_phases")):
            produced_phases.add(_text(phase))

        if entity:
            produced_entities.add(entity)
        for f in _list(fields):
            produced_fields.add(_text(f))
        for f in _list(identity_fields):
            produced_identity_fields.add(_text(f))
        for r in _list(relations):
            produced_relations.add(_text(r))

    # Primary response always produces status_code and body
    produced_fields.add("status_code")
    produced_fields.add("body")
    # Treatment/control responses produce these
    if _list(exp.get("treatment_plan")):
        produced_phases.add("treatment")
    if _list(exp.get("control_plan")):
        produced_phases.add("control")

    return {
        "produced_phases": sorted(produced_phases),
        "produced_entities": sorted(produced_entities),
        "produced_fields": sorted(produced_fields),
        "produced_identity_fields": sorted(produced_identity_fields),
        "produced_relations": sorted(produced_relations),
    }


# ─── Contract Builder ─────────────────────────────────────────────────────────


def build_oracle_input_contract(
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate the oracle input contract for an experiment.

    SPEC v1.2.1 §10.2: Real subset validation:
        required_fields ⊆ produced_fields
        required_phases ⊆ produced_phases
        required_entities ⊆ produced_entities

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

    # SPEC v1.2.1 §10.1: Build Observer Output Contract
    observer_output = _build_observer_output_contract(observers, exp)
    produced_phases = set(observer_output["produced_phases"])
    produced_entities = set(observer_output["produced_entities"])
    produced_fields = set(observer_output["produced_fields"])
    produced_identity_fields = set(observer_output["produced_identity_fields"])
    produced_relations = set(observer_output["produced_relations"])

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
        required_identity_fields = list(requirements.get("required_identity_fields") or [])
        required_relations = list(requirements.get("required_relations") or [])
        requires_control = requirements.get("requires_control", False)

        # Check observer coverage
        observer_ids = {_text(o.get("observer_id")) for o in observers if isinstance(o, dict)}

        missing_inputs: list[str] = []

        # ── SPEC v1.2.1 §10.2: Real subset validation ──
        # For governed writes, validate fields/phases/entities against observer output.
        # For read-only experiments, the response itself provides needed data.
        if is_write:
            # required_fields ⊆ produced_fields
            missing_fields = set(required_fields) - produced_fields
            for mf in sorted(missing_fields):
                missing_inputs.append(f"field:{mf}")

            # required_phases ⊆ produced_phases
            missing_phases = set(required_phases) - produced_phases
            for mp in sorted(missing_phases):
                missing_inputs.append(f"phase:{mp}")

            # required_entities ⊆ produced_entities
            missing_entities = set(required_entities) - produced_entities
            # 'primary' entity is always available from primary operation
            missing_entities.discard("primary")
            for me in sorted(missing_entities):
                missing_inputs.append(f"entity:{me}")

            # required_identity_fields ⊆ produced_identity_fields
            missing_id_fields = set(required_identity_fields) - produced_identity_fields
            for mif in sorted(missing_id_fields):
                missing_inputs.append(f"identity_field:{mif}")

            # required_relations ⊆ produced_relations
            missing_rels = set(required_relations) - produced_relations
            for mr in sorted(missing_rels):
                missing_inputs.append(f"relation:{mr}")

        # Check control plan
        if requires_control:
            control_plan = _list(exp.get("control_plan"))
            if not control_plan:
                missing_inputs.append("control_plan_missing")

        # Coverage status — SPEC v1.2.1: must NOT return COMPLETE if any check fails
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
            "required_relations": required_relations,
            "required_identity_fields": required_identity_fields,
            "required_time_window": None,
            # observer_ids is a set — emit in sorted order so compile output is
            # deterministic across processes (PYTHONHASHSEED-independent).
            "observer_plan_refs": sorted(observer_ids),
            "coverage_status": coverage_status,
            "missing_inputs": missing_inputs,
            "fingerprint": fingerprint,
        })

    # Overall status — SPEC v1.2.1 §10.3: BLOCKED_ORACLE_INPUT_INCOMPLETE
    overall_status = "COMPLETE" if not all_missing else "INCOMPLETE"
    reason_code = "" if overall_status == "COMPLETE" else "BLOCKED_ORACLE_INPUT_INCOMPLETE"

    return {
        "schema_version": "qualibug.oracle-input-contract-batch.v1",
        "experiment_id": _text(exp.get("experiment_id")),
        "obligation_id": _text(exp.get("obligation_id")),
        "contracts": contracts,
        "observer_output_contract": observer_output,
        "overall_status": overall_status,
        "total_missing": len(all_missing),
        "missing_inputs": all_missing,
        "reason_code": reason_code,
        "detail": "" if overall_status == "COMPLETE" else f"oracle_input_missing:{';'.join(all_missing[:5])}",
    }
