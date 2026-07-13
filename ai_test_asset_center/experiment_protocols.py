"""Validation-aware protocol facade over the stable family compiler."""
from __future__ import annotations

from copy import deepcopy
import math
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


def _field_tokens(property_spec: dict[str, Any]) -> tuple[str | int, ...]:
    raw = property_spec.get("field_tokens")
    if isinstance(raw, list) and raw and all(
        isinstance(value, (str, int)) and not isinstance(value, bool)
        for value in raw
    ):
        return tuple(raw)
    field_path = _text(
        property_spec.get("field_path")
        or property_spec.get("field")
        or property_spec.get("field_name")
        or property_spec.get("field_ref")
    )
    return tuple(part for part in field_path.split(".") if part)


def _schema_and_value_at(
    schema: dict[str, Any],
    value: Any,
    tokens: tuple[str | int, ...],
) -> tuple[dict[str, Any], Any, bool]:
    current_schema = _dict(schema)
    current_value = value
    for token in tokens:
        if isinstance(token, int):
            current_schema = _dict(current_schema.get("items"))
            if not isinstance(current_value, list) or not (
                0 <= token < len(current_value)
            ):
                return {}, None, False
            current_value = current_value[token]
            continue
        current_schema = _dict(
            _dict(current_schema.get("properties")).get(token)
        )
        if not isinstance(current_value, dict) or token not in current_value:
            return {}, None, False
        current_value = current_value[token]
    return current_schema, current_value, bool(current_schema)


def _parent_and_leaf(
    value: Any,
    tokens: tuple[str | int, ...],
) -> tuple[Any, str | int | None, bool]:
    if not tokens:
        return None, None, False
    parent = value
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(parent, list) or not (
                0 <= token < len(parent)
            ):
                return None, None, False
            parent = parent[token]
        else:
            if not isinstance(parent, dict) or token not in parent:
                return None, None, False
            parent = parent[token]
    leaf = tokens[-1]
    if isinstance(leaf, int):
        return parent, leaf, isinstance(parent, list) and 0 <= leaf < len(parent)
    return parent, leaf, isinstance(parent, dict) and leaf in parent


def _same_declared_type(value: Any, declared_type: str) -> bool:
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


def _invalid_type_value(declared_type: str) -> tuple[bool, Any]:
    values = {
        "string": {},
        "integer": {},
        "number": {},
        "boolean": {},
        "array": {},
        "object": [],
        "null": True,
    }
    return (declared_type in values, deepcopy(values.get(declared_type)))


def _invalid_enum_value(
    current: Any,
    enum_values: list[Any],
) -> tuple[bool, Any]:
    if current not in enum_values:
        return False, None
    if isinstance(current, str):
        candidate = "__qualibug_invalid_enum__"
        while candidate in enum_values:
            candidate += "_x"
        return True, candidate
    if isinstance(current, bool):
        candidate = not current
        return (candidate not in enum_values, candidate)
    if isinstance(current, int) and not isinstance(current, bool):
        numeric = [
            value
            for value in enum_values
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        candidate = (max(numeric) + 1) if numeric else current + 1
        while candidate in enum_values:
            candidate += 1
        return True, candidate
    if isinstance(current, float):
        numeric = [
            float(value)
            for value in enum_values
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
        ]
        candidate = (max(numeric) + 1.0) if numeric else current + 1.0
        while candidate in enum_values:
            candidate += 1.0
        return True, candidate
    if isinstance(current, list):
        candidates = [[], ["__qualibug_invalid_enum__"]]
        for candidate in candidates:
            if candidate not in enum_values:
                return True, candidate
    if isinstance(current, dict):
        candidates = [{}, {"__qualibug_invalid_enum__": True}]
        for candidate in candidates:
            if candidate not in enum_values:
                return True, candidate
    return False, None


def _numeric_boundary_candidate(
    *,
    constraint: str,
    boundary: Any,
    declared_type: str,
) -> tuple[bool, Any]:
    if isinstance(boundary, bool) or not isinstance(boundary, (int, float)):
        return False, None
    if declared_type == "integer":
        if constraint == "minimum":
            return True, math.ceil(boundary) - 1
        if constraint == "exclusiveMinimum":
            return True, math.floor(boundary)
        if constraint == "maximum":
            return True, math.floor(boundary) + 1
        if constraint == "exclusiveMaximum":
            return True, math.ceil(boundary)
        return False, None
    if declared_type == "number":
        if constraint == "minimum":
            return True, boundary - 1
        if constraint == "exclusiveMinimum":
            return True, boundary
        if constraint == "maximum":
            return True, boundary + 1
        if constraint == "exclusiveMaximum":
            return True, boundary
    return False, None


def _invalid_pattern_value(pattern: str, current: str) -> tuple[bool, str]:
    try:
        if re.search(pattern, current) is None:
            return False, ""
    except re.error:
        return False, ""
    for candidate in (
        "__qualibug_pattern_violation__",
        "!",
        "0",
        " ",
        "",
    ):
        try:
            if re.search(pattern, candidate) is None:
                return True, candidate
        except re.error:
            return False, ""
    return False, ""


def _invalid_constraint_value(
    *,
    constraint: str,
    field_schema: dict[str, Any],
    current: Any,
    constraint_value: Any,
) -> tuple[bool, Any, str]:
    declared_type = _text(field_schema.get("type")).lower()
    if constraint.startswith("type:"):
        target_type = constraint.split(":", 1)[1]
        ok, value = _invalid_type_value(target_type)
        return ok, value, "replace_with_wrong_type"
    if constraint == "enum":
        ok, value = _invalid_enum_value(
            current,
            _list(field_schema.get("enum")) or _list(constraint_value),
        )
        return ok, value, "replace_outside_enum"
    if constraint in {
        "minimum",
        "exclusiveMinimum",
        "maximum",
        "exclusiveMaximum",
    }:
        ok, value = _numeric_boundary_candidate(
            constraint=constraint,
            boundary=constraint_value,
            declared_type=declared_type,
        )
        return ok, value, "replace_outside_numeric_boundary"
    if constraint == "minLength":
        if (
            not isinstance(current, str)
            or not isinstance(constraint_value, int)
            or isinstance(constraint_value, bool)
            or constraint_value <= 0
            or len(current) < constraint_value
        ):
            return False, None, ""
        return True, current[: constraint_value - 1], "truncate_below_min_length"
    if constraint == "maxLength":
        if (
            not isinstance(current, str)
            or not isinstance(constraint_value, int)
            or isinstance(constraint_value, bool)
            or constraint_value < 0
            or len(current) > constraint_value
        ):
            return False, None, ""
        seed = current or "x"
        value = (seed * (constraint_value + 1))[: constraint_value + 1]
        return True, value, "extend_above_max_length"
    if constraint == "pattern":
        if not isinstance(current, str):
            return False, None, ""
        ok, value = _invalid_pattern_value(_text(constraint_value), current)
        return ok, value, "replace_with_pattern_mismatch"
    if constraint == "minItems":
        if (
            not isinstance(current, list)
            or not isinstance(constraint_value, int)
            or isinstance(constraint_value, bool)
            or constraint_value <= 0
            or len(current) < constraint_value
        ):
            return False, None, ""
        return (
            True,
            deepcopy(current[: constraint_value - 1]),
            "truncate_below_min_items",
        )
    if constraint == "maxItems":
        if (
            not isinstance(current, list)
            or not isinstance(constraint_value, int)
            or isinstance(constraint_value, bool)
            or constraint_value < 0
            or len(current) > constraint_value
        ):
            return False, None, ""
        value = deepcopy(current)
        filler = deepcopy(current[0]) if current else None
        while len(value) <= constraint_value:
            value.append(deepcopy(filler))
        return True, value, "extend_above_max_items"
    return False, None, ""


def _source_constraint_material(
    operation: dict[str, Any],
    property_spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    control = _base.source_request_example(operation)
    schema = _base._request_body_schema(operation)
    tokens = _field_tokens(property_spec)
    constraint = _text(property_spec.get("validation_constraint"))
    if not control or not schema or not tokens or not constraint:
        return {}, {}, {}, "source_constraint_context_missing"
    field_schema, current, found = _schema_and_value_at(
        schema,
        control,
        tokens,
    )
    if not found:
        return {}, {}, {}, "source_constraint_field_unresolved"

    treatment = deepcopy(control)
    parent, leaf, exists = _parent_and_leaf(treatment, tokens)
    if not exists:
        return {}, {}, {}, "source_constraint_field_unresolved"
    constraint_value = property_spec.get("validation_constraint_value")
    if constraint == "required":
        if not isinstance(parent, dict) or not isinstance(leaf, str):
            return {}, {}, {}, "required_constraint_parent_invalid"
        parent.pop(leaf, None)
        operator = "remove_required_field"
    else:
        ok, invalid_value, operator = _invalid_constraint_value(
            constraint=constraint,
            field_schema=field_schema,
            current=current,
            constraint_value=constraint_value,
        )
        if not ok:
            return {}, {}, {}, f"source_constraint_mutation_unavailable:{constraint}"
        if isinstance(leaf, int):
            if not isinstance(parent, list):
                return {}, {}, {}, "source_constraint_parent_invalid"
            parent[leaf] = deepcopy(invalid_value)
        else:
            if not isinstance(parent, dict):
                return {}, {}, {}, "source_constraint_parent_invalid"
            parent[leaf] = deepcopy(invalid_value)

    mutation = {
        "json_path": _text(property_spec.get("json_path"))
        or "$.” + ".".join(str(token) for token in tokens),
        "field_tokens": list(tokens),
        "constraint": constraint,
        "constraint_value": deepcopy(constraint_value),
        "source": "request_schema",
        "operator": operator,
    }
    return control, treatment, mutation, ""


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

    constraint = _text(property_spec.get("validation_constraint"))
    if constraint:
        control, treatment, mutation, reason = _source_constraint_material(
            operation,
            property_spec,
        )
        if reason:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": reason,
            }
        control_plan = [
            dict(row)
            for row in result.get("control_plan") or []
            if isinstance(row, dict)
        ]
        treatment_plan = [
            dict(row)
            for row in result.get("treatment_plan") or []
            if isinstance(row, dict)
        ]
        if len(control_plan) != 1 or len(treatment_plan) != 1:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_UNSUPPORTED_ADAPTER",
                "detail": "validation_protocol_shape_invalid",
            }
        control_plan[0]["body"] = deepcopy(control)
        treatment_plan[0]["body"] = deepcopy(treatment)
        treatment_plan[0]["mutation"] = mutation
        result = {
            **result,
            "control_plan": control_plan,
            "treatment_plan": treatment_plan,
        }

    assertion = dict(result.get("assertion") or {})
    assertion.update({
        "kind": "validation_rejection",
        "expected_class": 4,
        "expected_effect_count": 0,
        "expected_control_effect_min": 1,
        "compare_field": "status_code",
        "effect_field": "treatment_effect_count",
        "control_effect_field": "control_effect_count",
    })
    return {
        **result,
        "assertion": assertion,
    }
