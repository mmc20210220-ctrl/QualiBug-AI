"""Family-specific executable experiment protocol compiler.

This module owns step semantics. The generic experiment compiler owns contract
assembly and blockers; it must never replace a missing family protocol with a
single status-code probe.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def source_request_example(operation: dict[str, Any]) -> dict[str, Any]:
    """Return an explicitly documented request example, never synthesized data."""

    direct = _dict(operation).get("request_example")
    if isinstance(direct, dict) and direct:
        return deepcopy(direct)
    request_schema = _dict(_dict(operation).get("request_schema"))
    content = _dict(request_schema.get("content"))
    for media in content.values():
        if not isinstance(media, dict):
            continue
        example = media.get("example")
        if isinstance(example, dict) and example:
            return deepcopy(example)
        for row in _dict(media.get("examples")).values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return deepcopy(value)
    return {}


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    request_schema = _dict(_dict(operation).get("request_schema"))
    if _text(request_schema.get("type")) or _dict(request_schema.get("properties")):
        return request_schema
    for media in _dict(request_schema.get("content")).values():
        schema = _dict(_dict(media).get("schema"))
        if schema:
            return schema
    return {}


def _validation_protocol_material(
    operation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    control = source_request_example(operation)
    schema = _request_body_schema(operation)
    if not control or not schema:
        return {}, {}, {}
    required = [
        _text(value)
        for value in schema.get("required") or []
        if _text(value)
    ]
    for field in required:
        if field not in control:
            continue
        treatment = deepcopy(control)
        treatment.pop(field, None)
        return control, treatment, {
            "json_path": f"$.{field}",
            "constraint": "required",
            "source": "request_schema",
        }
    return {}, {}, {}


def compile_family_protocol(
    *,
    risk_family: str,
    operation: dict[str, Any],
    operation_ref: str,
    control_actor_ref: str,
    treatment_actor_ref: str,
    property_spec: dict[str, Any],
) -> dict[str, Any]:
    """Compile exact family steps or return one typed blocker."""

    family = _text(risk_family)
    method = _text(operation.get("method")).upper()
    needs_control = family in {
        "authorization",
        "isolation",
        "validation",
        "privacy",
        "visibility",
    }
    if needs_control and not control_actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "control_actor",
        }
    if not treatment_actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "treatment_actor",
        }

    if family == "idempotency":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "idempotency_requires_write_operation",
            }
        body = source_request_example(operation)
        if method in {"POST", "PUT", "PATCH"} and not body:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "source_request_example_missing",
            }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [
                {
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "idempotency_initial_write",
                    "protocol_step": "initial_write",
                    "body": deepcopy(body),
                    "property_template": _text(property_spec.get("template")),
                },
                {
                    "step_id": "treatment_2",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "idempotency_repeat_write",
                    "protocol_step": "repeat_write",
                    "body": deepcopy(body),
                    "property_template": _text(property_spec.get("template")),
                },
            ],
        }

    if family == "validation":
        if method not in {"POST", "PUT", "PATCH"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "validation_body_protocol_requires_write_operation",
            }
        control_body, treatment_body, mutation = _validation_protocol_material(operation)
        if not mutation:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "source_schema_mutation_missing",
            }
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref,
                "operation_ref": operation_ref,
                "intent": "valid_source_control",
                "protocol_step": "positive_control",
                "body": deepcopy(control_body),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "single_constraint_mutation",
                "protocol_step": "single_mutation",
                "body": deepcopy(treatment_body),
                "mutation": mutation,
            }],
            "assertion": {
                "kind": "http_status_class",
                "expected_class": 4,
                "compare_field": "status_code",
            },
        }

    write_body: dict[str, Any] = {}
    if (
        family in {"authorization", "isolation", "visibility"}
        and method in {"POST", "PUT", "PATCH"}
    ):
        write_body = source_request_example(operation)
        if not write_body:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "source_request_example_missing",
            }

    control_plan: list[dict[str, Any]] = []
    if needs_control:
        control_step = {
            "step_id": "control_1",
            "actor_ref": control_actor_ref,
            "operation_ref": operation_ref,
            "intent": "authorized_control",
            "protocol_step": "positive_control",
        }
        if write_body:
            control_step["body"] = deepcopy(write_body)
        control_plan.append(control_step)
    treatment_step = {
        "step_id": "treatment_1",
        "actor_ref": treatment_actor_ref,
        "operation_ref": operation_ref,
        "intent": "treatment",
        "protocol_step": "treatment",
        "property_template": _text(property_spec.get("template")),
    }
    if write_body:
        treatment_step["body"] = deepcopy(write_body)
    return {
        "status": "COMPILED",
        "control_plan": control_plan,
        "treatment_plan": [treatment_step],
    }
