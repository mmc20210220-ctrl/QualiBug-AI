"""Freeze explicit state-field authority onto compiled precondition steps."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "qualibug.state-precondition-compile-freeze.v1"
BLOCKED_STATE_PRECONDITION_FIELD_MISSING = "BLOCKED_STATE_PRECONDITION_FIELD_MISSING"
BLOCKED_STATE_PRECONDITION_FIELD_AMBIGUOUS = "BLOCKED_STATE_PRECONDITION_FIELD_AMBIGUOUS"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _block(experiment: dict[str, Any], reason_code: str, detail: str) -> dict[str, Any]:
    result = deepcopy(experiment)
    result["compile_receipt"] = {
        **_dict(result.get("compile_receipt")),
        "status": "BLOCKED",
        "reason_code": reason_code,
        "detail": detail,
        "state_precondition_freeze_status": "BLOCKED",
    }
    result["state_precondition_freeze_receipt"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "reason_code": reason_code,
        "detail": detail,
    }
    return result


def _assertion_fields(experiment: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for raw in _list(experiment.get("assertions")):
        row = _dict(raw)
        prop = _dict(row.get("property"))
        binding = _dict(row.get("field_rule_binding"))
        for value in (
            row.get("state_field"),
            prop.get("state_field"),
            row.get("field"),
            prop.get("field"),
            binding.get("field"),
            binding.get("field_path"),
            binding.get("json_path"),
        ):
            field = _text(value)
            if field.startswith("$."):
                field = field[2:]
            if "." in field:
                field = field.rsplit(".", 1)[-1]
            if field and field not in fields:
                fields.append(field)
    return fields


def _step_readback_fields(step: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    contract = _dict(step.get("readback_contract"))
    for raw in _list(contract.get("required_fields")):
        value = _text(_dict(raw).get("field") if isinstance(raw, dict) else raw)
        if value.startswith("$."):
            value = value[2:]
        if "." in value:
            value = value.rsplit(".", 1)[-1]
        if value and value not in fields:
            fields.append(value)
    return fields


def freeze_state_precondition_fields(experiment: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(_dict(experiment))
    if _text(_dict(result.get("compile_receipt")).get("status")) != "COMPILED":
        return result
    plan = [dict(row) for row in _list(result.get("precondition_plan")) if isinstance(row, dict)]
    if not plan:
        result["state_precondition_freeze_receipt"] = {
            "schema_version": SCHEMA_VERSION,
            "status": "NOT_REQUIRED",
            "step_ids": [],
            "state_fields": [],
        }
        result["compile_receipt"] = {
            **_dict(result.get("compile_receipt")),
            "state_precondition_freeze_status": "NOT_REQUIRED",
        }
        return result

    assertion_fields = _assertion_fields(result)
    frozen: list[dict[str, Any]] = []
    bindings: list[dict[str, str]] = []
    # State-TRANSITION step intents. Only these need a frozen governed state
    # field. A precondition plan mixes fixture/subject/dependency establishment
    # steps (create the referenced user/address/order — ``*_establishment``)
    # with state-advancement steps (cancel the order to reach the declared
    # pre-state). The establishment steps carry no governed state transition and
    # the referenced entity often has no STATE field at all (a user). Demanding
    # a state field on every step blocked every conservation/idempotency
    # experiment whose subject chain included such a dependency create with
    # ``BLOCKED_STATE_PRECONDITION_FIELD_MISSING`` — a structural ceiling on
    # whole risk families, not a missing input. These intent strings are
    # product-internal step semantics, never industry/business terms.
    _STATE_TRANSITION_INTENTS = frozenset({
        "state_precondition_establishment",
        "money_subject_state_advancement",
    })
    for step in plan:
        is_state_transition = bool(
            _text(step.get("from_state"))
            or _text(step.get("to_state"))
            or _text(step.get("intent")) in _STATE_TRANSITION_INTENTS
        )
        if not is_state_transition:
            # Preserve establishment steps verbatim; they carry no governed
            # state transition and need no frozen state field.
            frozen.append(step)
            continue
        declared = _text(step.get("state_field") or step.get("field"))
        candidates: list[str] = []
        if declared:
            candidates.append(declared)
        for value in _step_readback_fields(step) + assertion_fields:
            if value and value not in candidates:
                candidates.append(value)
        if not candidates:
            return _block(
                result,
                BLOCKED_STATE_PRECONDITION_FIELD_MISSING,
                f"step={_text(step.get('step_id')) or 'missing'}:state_field_unresolved",
            )
        if len(candidates) > 1:
            return _block(
                result,
                BLOCKED_STATE_PRECONDITION_FIELD_AMBIGUOUS,
                f"step={_text(step.get('step_id')) or 'missing'}:fields={','.join(candidates)}",
            )
        step["state_field"] = candidates[0]
        contract = deepcopy(_dict(step.get("readback_contract")))
        contract["state_field"] = candidates[0]
        step["readback_contract"] = contract
        runtime_body_plan = deepcopy(_dict(step.get("runtime_body_plan")))
        runtime_readback = deepcopy(_dict(runtime_body_plan.get("readback_contract")))
        runtime_readback["state_field"] = candidates[0]
        runtime_body_plan["readback_contract"] = runtime_readback
        step["runtime_body_plan"] = runtime_body_plan
        frozen.append(step)
        bindings.append({
            "step_id": _text(step.get("step_id")),
            "state_field": candidates[0],
            "target_state": _text(step.get("to_state") or step.get("target_state")),
        })

    payload = {
        "experiment_id": _text(result.get("experiment_id")),
        "bindings": bindings,
    }
    result["precondition_plan"] = frozen
    result["state_precondition_freeze_receipt"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "FROZEN",
        "freeze_fingerprint": _fingerprint(payload),
        **payload,
    }
    result["compile_receipt"] = {
        **_dict(result.get("compile_receipt")),
        "state_precondition_freeze_status": "FROZEN",
        "state_precondition_freeze_fingerprint": _fingerprint(payload),
    }
    return result
