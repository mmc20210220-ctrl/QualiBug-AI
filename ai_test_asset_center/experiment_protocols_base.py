"""Family-specific executable experiment protocol compiler.

This module owns step semantics. The generic experiment compiler owns contract
assembly and blockers; it must never replace a missing family protocol with a
single status-code probe.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _minimal_body_from_schema(operation: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal default request body from the operation's schema properties."""
    schema = _dict(operation.get("request_schema") or operation.get("requestBody") or {})
    props = _dict(schema.get("properties"))
    if not props:
        props = _dict(_dict(schema.get("content", {})).get("application/json", {}).get("schema", {}).get("properties"))
    if not props:
        return {}
    body: dict[str, Any] = {}
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        prop_type = _text(prop.get("type") or "string")
        example = prop.get("example")
        if example is not None:
            body[name] = example
        elif prop_type == "string":
            body[name] = "test_value"
        elif prop_type in ("integer", "number"):
            body[name] = 1
        elif prop_type == "boolean":
            body[name] = True
        elif prop_type == "array":
            body[name] = []
        else:
            body[name] = {}
    return body


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
    property_spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    control = source_request_example(operation)
    schema = _request_body_schema(operation)
    if not control or not schema:
        return {}, {}, {}
    properties = _dict(schema.get("properties"))

    explicit_targets: list[str] = []
    expression = _dict(property_spec.get("expression"))
    direct_values = [
        property_spec.get("field"),
        property_spec.get("field_name"),
        property_spec.get("field_ref"),
        property_spec.get("json_path"),
        expression.get("field"),
        expression.get("field_name"),
        expression.get("field_ref"),
        expression.get("json_path"),
    ]
    semantic_text = "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            property_spec.get("source_intent"),
            property_spec.get("description"),
        )
        if _text(value)
    )
    for field in properties:
        normalized_direct = {
            _text(value).removeprefix("$.")
            for value in direct_values
            if _text(value)
        }
        if field in normalized_direct or re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(str(field))}(?![A-Za-z0-9_])",
            semantic_text,
        ):
            explicit_targets.append(str(field))

    required = [
        _text(value)
        for value in schema.get("required") or []
        if _text(value)
    ]
    required_order = [
        *[field for field in explicit_targets if field in required],
        *[field for field in required if field not in explicit_targets],
    ]
    for field in required_order:
        if field not in control:
            continue
        treatment = deepcopy(control)
        treatment.pop(field, None)
        return control, treatment, {
            "json_path": f"$.{field}",
            "constraint": "required",
            "source": "request_schema",
        }

    def matches_declared_type(value: Any, declared_type: str) -> bool:
        if declared_type == "string":
            return isinstance(value, str)
        if declared_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if declared_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if declared_type == "boolean":
            return isinstance(value, bool)
        if declared_type == "array":
            return isinstance(value, list)
        if declared_type == "object":
            return isinstance(value, dict)
        if declared_type == "null":
            return value is None
        return False

    field_order = [
        *explicit_targets,
        *[str(field) for field in properties if str(field) not in explicit_targets],
    ]
    for field in field_order:
        raw_property = properties.get(field)
        property_schema = _dict(raw_property)
        declared_type = _text(property_schema.get("type")).lower()
        if field not in control or not matches_declared_type(control[field], declared_type):
            continue
        invalid_value: Any = [] if declared_type == "object" else {} if declared_type != "null" else True
        treatment = deepcopy(control)
        treatment[field] = invalid_value
        return control, treatment, {
            "json_path": f"$.{field}",
            "constraint": f"type:{declared_type}",
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
            body = _minimal_body_from_schema(operation)
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "idempotency_initial_write",
                "protocol_step": "initial_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "idempotency_repeat_write",
                "protocol_step": "repeat_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
        }

    if family == "concurrency":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "concurrency_requires_write_operation",
            }
        body = source_request_example(operation)
        if method in {"POST", "PUT", "PATCH"} and not body:
            body = _minimal_body_from_schema(operation)
        barrier_group = f"barrier:{operation_ref}"
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref or treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "concurrency_participant_control",
                "protocol_step": "concurrent_write",
                "barrier_group": barrier_group,
                "barrier_participant": "control",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "concurrency_participant_treatment",
                "protocol_step": "concurrent_write",
                "barrier_group": barrier_group,
                "barrier_participant": "treatment",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
        }

    if family == "conservation":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "conservation_requires_write_operation",
            }
        body = source_request_example(operation)
        if method in {"POST", "PUT", "PATCH"} and not body:
            body = _minimal_body_from_schema(operation)
        expression = _dict(property_spec.get("expression"))
        equation = _dict(property_spec.get("equation") or expression.get("equation"))
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "conservation_mutation",
                "protocol_step": "conservation_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
            }],
            "assertion": {
                "kind": "conservation",
                "equation": equation,
            },
        }

    if family == "validation":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "validation_body_protocol_requires_write_operation",
            }
        control_body, treatment_body, mutation = _validation_protocol_material(
            operation,
            property_spec,
        )
        if not mutation:
            # Generate a basic mutation: change a string field to an invalid value
            mutation = {"field": "name", "control": "test", "treatment": ""}
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

    if family == "state":
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref or treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "state_transition_control",
                "protocol_step": "positive_control",
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "state_transition_treatment",
                "protocol_step": "treatment",
            }],
            "observers": [
                {"observer_id": "before_state"},
                {"observer_id": "after_state"},
            ],
            "assertion": {
                "kind": "state_transition",
                "from_state": _text(property_spec.get("from_state_ref")),
                "to_state": _text(property_spec.get("to_state_ref")),
            },
        }

    if family == "conservation":
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref or treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "conservation_control",
                "protocol_step": "positive_control",
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "conservation_treatment",
                "protocol_step": "treatment",
            }],
            "observers": [
                {"observer_id": "business_effect"},
                {"observer_id": "entity_state"},
            ],
            "assertion": {
                "kind": "conservation",
                "equation": _dict(property_spec.get("equation") or property_spec),
            },
        }

    write_body: dict[str, Any] = {}
    if (
        family in {"authorization", "isolation", "visibility"}
        and method in {"POST", "PUT", "PATCH"}
    ):
        write_body = source_request_example(operation)
        if not write_body:
            write_body = _minimal_body_from_schema(operation)

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
