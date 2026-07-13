"""Validation-aware protocol facade over the stable family compiler."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from . import experiment_protocols_base as _base
from .experiment_protocols_base import *  # noqa: F401,F403


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_type(value: Any, declared_type: str) -> bool:
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
    return True


def _body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    request_schema = _dict(operation.get("request_schema"))
    if _text(request_schema.get("type")) or _dict(
        request_schema.get("properties")
    ):
        return request_schema
    for media in _dict(request_schema.get("content")).values():
        schema = _dict(_dict(media).get("schema"))
        if schema:
            return schema
    return {}


def _exclusive_boundary(
    field_schema: dict[str, Any],
    key: str,
    fallback_key: str,
) -> int | float | None:
    raw = field_schema.get(key)
    if _numeric(raw):
        return raw
    if raw is True and _numeric(field_schema.get(fallback_key)):
        return field_schema[fallback_key]
    return None


def _satisfies_other_constraints(
    value: Any,
    field_schema: dict[str, Any],
    *,
    skip: str,
) -> bool:
    declared_type = _text(field_schema.get("type")).lower()
    if skip != "type" and declared_type and not _matches_type(
        value,
        declared_type,
    ):
        return False

    enum_values = _list(field_schema.get("enum"))
    if skip != "enum" and enum_values and value not in enum_values:
        return False

    if isinstance(value, str):
        min_length = field_schema.get("minLength")
        if (
            skip != "minLength"
            and isinstance(min_length, int)
            and not isinstance(min_length, bool)
            and len(value) < min_length
        ):
            return False
        max_length = field_schema.get("maxLength")
        if (
            skip != "maxLength"
            and isinstance(max_length, int)
            and not isinstance(max_length, bool)
            and len(value) > max_length
        ):
            return False
        pattern = _text(field_schema.get("pattern"))
        if skip != "pattern" and pattern:
            try:
                if re.search(pattern, value) is None:
                    return False
            except re.error:
                return False

    if _numeric(value):
        minimum = field_schema.get("minimum")
        if (
            skip != "minimum"
            and _numeric(minimum)
            and value < minimum
        ):
            return False
        exclusive_minimum = _exclusive_boundary(
            field_schema,
            "exclusiveMinimum",
            "minimum",
        )
        if (
            skip != "exclusiveMinimum"
            and exclusive_minimum is not None
            and value <= exclusive_minimum
        ):
            return False
        maximum = field_schema.get("maximum")
        if (
            skip != "maximum"
            and _numeric(maximum)
            and value > maximum
        ):
            return False
        exclusive_maximum = _exclusive_boundary(
            field_schema,
            "exclusiveMaximum",
            "maximum",
        )
        if (
            skip != "exclusiveMaximum"
            and exclusive_maximum is not None
            and value >= exclusive_maximum
        ):
            return False

    if isinstance(value, list):
        min_items = field_schema.get("minItems")
        if (
            skip != "minItems"
            and isinstance(min_items, int)
            and not isinstance(min_items, bool)
            and len(value) < min_items
        ):
            return False
        max_items = field_schema.get("maxItems")
        if (
            skip != "maxItems"
            and isinstance(max_items, int)
            and not isinstance(max_items, bool)
            and len(value) > max_items
        ):
            return False
    return True


def _wrong_type_value(declared_type: str) -> Any:
    return {
        "string": {},
        "integer": "QUALIBUG_INVALID_INTEGER",
        "number": "QUALIBUG_INVALID_NUMBER",
        "boolean": "QUALIBUG_INVALID_BOOLEAN",
        "array": {},
        "object": [],
        "null": True,
    }.get(declared_type, {})


def _outside_enum(
    enum_values: list[Any],
    field_schema: dict[str, Any],
) -> Any:
    if not enum_values:
        return None
    sample = enum_values[0]
    candidates: list[Any] = []
    if isinstance(sample, str):
        candidates.extend(
            f"QUALIBUG_INVALID_ENUM_{index}"
            for index in range(1, 20)
        )
    elif isinstance(sample, bool):
        candidates.extend([False, True])
    elif _numeric(sample):
        numeric_values = [
            value for value in enum_values if _numeric(value)
        ]
        if numeric_values:
            candidates.extend([
                max(numeric_values) + 1,
                min(numeric_values) - 1,
            ])
    elif isinstance(sample, list):
        candidates.extend([[], ["QUALIBUG_INVALID_ENUM"]])
    elif isinstance(sample, dict):
        candidates.extend([{}, {"qualibug_invalid_enum": True}])
    for candidate in candidates:
        if (
            candidate not in enum_values
            and _satisfies_other_constraints(
                candidate,
                field_schema,
                skip="enum",
            )
        ):
            return candidate
    return None


def _pattern_invalid_value(
    control_value: str,
    field_schema: dict[str, Any],
) -> str | None:
    min_length = field_schema.get("minLength")
    max_length = field_schema.get("maxLength")
    lower = (
        min_length
        if isinstance(min_length, int)
        and not isinstance(min_length, bool)
        else 1
    )
    upper = (
        max_length
        if isinstance(max_length, int)
        and not isinstance(max_length, bool)
        else max(lower, len(control_value) + 8)
    )
    if upper < lower:
        return None
    seeds = ["!", "0", "_", " ", "QUALIBUG!", "invalid-"]
    for length in range(lower, min(upper, lower + 32) + 1):
        for seed in seeds:
            candidate = (seed * (length + len(seed)))[:length]
            if _satisfies_other_constraints(
                candidate,
                field_schema,
                skip="pattern",
            ):
                pattern = _text(field_schema.get("pattern"))
                try:
                    if re.search(pattern, candidate) is None:
                        return candidate
                except re.error:
                    return None
    return None


def _constraint_mutation(
    *,
    control_body: dict[str, Any],
    operation: dict[str, Any],
    property_spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    field = _text(
        property_spec.get("field")
        or property_spec.get("field_name")
        or property_spec.get("field_ref")
        or property_spec.get("json_path")
    ).removeprefix("$.")
    constraint = _text(property_spec.get("validation_constraint"))
    schema = _body_schema(operation)
    field_schema = _dict(_dict(schema.get("properties")).get(field))
    if not field or not constraint or field not in control_body or not field_schema:
        return {}, {}

    treatment = deepcopy(control_body)
    mutation: dict[str, Any] = {
        "json_path": f"$.{field}",
        "constraint": constraint,
        "source": "request_schema",
        "constraint_value": deepcopy(
            property_spec.get("validation_constraint_value")
        ),
    }
    current = control_body[field]

    if constraint == "required":
        treatment.pop(field, None)
        return treatment, mutation

    if constraint.startswith("type:"):
        declared_type = constraint.split(":", 1)[1]
        candidate = _wrong_type_value(declared_type)
        if _matches_type(candidate, declared_type):
            return {}, {}
        treatment[field] = candidate
        return treatment, mutation

    candidate: Any = None
    if constraint == "enum":
        candidate = _outside_enum(
            _list(field_schema.get("enum")),
            field_schema,
        )
    elif constraint == "minimum" and _numeric(field_schema.get("minimum")):
        boundary = field_schema["minimum"]
        candidate = boundary - (1 if isinstance(boundary, int) else 1.0)
    elif constraint == "exclusiveMinimum":
        candidate = _exclusive_boundary(
            field_schema,
            "exclusiveMinimum",
            "minimum",
        )
    elif constraint == "maximum" and _numeric(field_schema.get("maximum")):
        boundary = field_schema["maximum"]
        candidate = boundary + (1 if isinstance(boundary, int) else 1.0)
    elif constraint == "exclusiveMaximum":
        candidate = _exclusive_boundary(
            field_schema,
            "exclusiveMaximum",
            "maximum",
        )
    elif constraint == "minLength":
        minimum = field_schema.get("minLength")
        if (
            isinstance(current, str)
            and isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and minimum > 0
        ):
            candidate = current[: minimum - 1]
            if len(candidate) < minimum - 1:
                candidate = "x" * (minimum - 1)
    elif constraint == "maxLength":
        maximum = field_schema.get("maxLength")
        if (
            isinstance(current, str)
            and isinstance(maximum, int)
            and not isinstance(maximum, bool)
            and maximum >= 0
        ):
            seed = current or "x"
            candidate = (
                seed * ((maximum + 1) // len(seed) + 1)
            )[: maximum + 1]
    elif constraint == "pattern" and isinstance(current, str):
        candidate = _pattern_invalid_value(current, field_schema)
    elif constraint == "minItems":
        minimum = field_schema.get("minItems")
        if (
            isinstance(current, list)
            and isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and minimum > 0
        ):
            candidate = deepcopy(current[: minimum - 1])
    elif constraint == "maxItems":
        maximum = field_schema.get("maxItems")
        if (
            isinstance(current, list)
            and isinstance(maximum, int)
            and not isinstance(maximum, bool)
            and maximum >= 0
        ):
            fill = deepcopy(current[-1]) if current else "QUALIBUG_ITEM"
            candidate = deepcopy(current)
            while len(candidate) <= maximum:
                candidate.append(deepcopy(fill))

    if candidate is None:
        return {}, {}
    if _satisfies_other_constraints(
        candidate,
        field_schema,
        skip=constraint,
    ):
        treatment[field] = candidate
        return treatment, mutation
    return {}, {}


def compile_family_protocol(
    *,
    risk_family: str,
    operation: dict[str, Any],
    operation_ref: str,
    control_actor_ref: str,
    treatment_actor_ref: str,
    property_spec: dict[str, Any],
) -> dict[str, Any]:
    result = _base.compile_family_protocol(
        risk_family=risk_family,
        operation=operation,
        operation_ref=operation_ref,
        control_actor_ref=control_actor_ref,
        treatment_actor_ref=treatment_actor_ref,
        property_spec=property_spec,
    )
    if (
        _text(risk_family) != "validation"
        or _text(result.get("status")) != "COMPILED"
    ):
        return result

    control_body = _base.source_request_example(operation)
    treatment_body, mutation = _constraint_mutation(
        control_body=control_body,
        operation=operation,
        property_spec=property_spec,
    )
    if not treatment_body or not mutation:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "documented_validation_constraint_mutation_missing",
        }

    control_plan = [
        dict(row)
        for row in _list(result.get("control_plan"))
        if isinstance(row, dict)
    ]
    treatment_plan = [
        dict(row)
        for row in _list(result.get("treatment_plan"))
        if isinstance(row, dict)
    ]
    if len(control_plan) != 1 or len(treatment_plan) != 1:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_UNSUPPORTED_ADAPTER",
            "detail": "validation_protocol_shape_invalid",
        }
    control_plan[0]["body"] = deepcopy(control_body)
    treatment_plan[0]["body"] = deepcopy(treatment_body)
    treatment_plan[0]["mutation"] = mutation

    assertion = dict(result.get("assertion") or {})
    assertion.update({
        "kind": "validation_rejection",
        "expected_class": 4,
        "expected_effect_count": 0,
        "compare_field": "status_code",
        "effect_field": "treatment_effect_count",
    })
    return {
        **result,
        "control_plan": control_plan,
        "treatment_plan": treatment_plan,
        "assertion": assertion,
    }
