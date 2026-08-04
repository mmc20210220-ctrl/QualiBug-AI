"""Validation-aware protocol facade over the stable family compiler."""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

from . import experiment_protocols_base as _base
from .experiment_protocols_base import *  # noqa: F401,F403
from .behavior_ir_core import _infer_operation_effect, _is_ephemeral_session_path


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


def _json_path(tokens: tuple[str | int, ...]) -> str:
    path = "$"
    for token in tokens:
        if isinstance(token, int):
            path += f"[{token}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            path += f".{token}"
        else:
            escaped = token.replace("\\", "\\\\").replace("'", "\\'")
            path += f"['{escaped}']"
    return path


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
        min_length = field_schema.get("minLength")
        max_length = field_schema.get("maxLength")
        lower = min_length if isinstance(min_length, int) and min_length >= 0 else 0
        upper = max_length if isinstance(max_length, int) and max_length >= lower else max(lower, len(value))
        if ok and not lower <= len(value) <= upper:
            candidate = "!" * max(lower, 1)
            try:
                if len(candidate) <= upper and re.search(_text(constraint_value), candidate) is None:
                    value = candidate
                else:
                    return False, None, ""
            except re.error:
                return False, None, ""
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
    tokens = _field_tokens(property_spec)
    constraint = _text(property_spec.get("validation_constraint"))
    location = _text(property_spec.get("parameter_location")).lower()
    if (
        tokens
        and isinstance(tokens[0], str)
        and str(tokens[0]).startswith("@")
    ):
        token_location = str(tokens[0])[1:].lower()
        if not location:
            location = token_location
        if location == token_location:
            tokens = tokens[1:]
    if location in {"query", "path", "header"}:
        return _parameter_constraint_material(
            operation,
            property_spec,
            location=location,
            tokens=tokens,
            constraint=constraint,
        )

    control = _base.source_request_example(operation)
    schema = _base._request_body_schema(operation)
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
    constraint_source = _text(
        property_spec.get("validation_constraint_source") or "request_schema"
    )
    if constraint_source not in {"request_schema", "source_invariant"}:
        return {}, {}, {}, "source_constraint_lineage_invalid"
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
        "json_path": _text(property_spec.get("json_path")) or _json_path(tokens),
        "field_tokens": list(tokens),
        "parameter_location": "body",
        "constraint": constraint,
        "constraint_value": deepcopy(constraint_value),
        "source": constraint_source,
        "operator": operator,
    }
    return control, treatment, mutation, ""


def _parameter_constraint_material(
    operation: dict[str, Any],
    property_spec: dict[str, Any],
    *,
    location: str,
    tokens: tuple[str | int, ...],
    constraint: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    if len(tokens) != 1 or not isinstance(tokens[0], str) or not constraint:
        return {}, {}, {}, "parameter_constraint_tokens_invalid"
    name = str(tokens[0])
    parameter_row: dict[str, Any] = {}
    for raw in _list(operation.get("parameters")):
        if not isinstance(raw, dict):
            continue
        if _text(raw.get("name")) != name:
            continue
        param_in = _text(raw.get("in") or raw.get("location")).lower() or location
        if param_in != location:
            continue
        parameter_row = dict(raw)
        break
    schema = _dict(parameter_row.get("schema"))
    current = parameter_row.get("example", schema.get("example"))
    if current is None and location == "path":
        current = "1"
    if current is None:
        declared = _text(schema.get("type")).lower()
        if declared == "integer":
            current = 1
        elif declared == "number":
            current = 1.0
        elif declared == "boolean":
            current = True
        else:
            current = "example"
    if not schema:
        if isinstance(current, bool):
            schema = {"type": "boolean"}
        elif isinstance(current, int) and not isinstance(current, bool):
            schema = {"type": "integer"}
        elif isinstance(current, float):
            schema = {"type": "number"}
        else:
            schema = {"type": "string"}
    constraint_value = property_spec.get("validation_constraint_value")
    constraint_source = _text(
        property_spec.get("validation_constraint_source") or "request_schema"
    )
    if constraint_source not in {"request_schema", "source_invariant"}:
        return {}, {}, {}, "source_constraint_lineage_invalid"
    control = {name: deepcopy(current)}
    treatment = deepcopy(control)
    if constraint == "required":
        treatment.pop(name, None)
        operator = "remove_required_parameter"
    else:
        ok, invalid_value, operator = _invalid_constraint_value(
            constraint=constraint,
            field_schema=schema,
            current=current,
            constraint_value=constraint_value,
        )
        if not ok:
            return {}, {}, {}, f"source_constraint_mutation_unavailable:{constraint}"
        treatment[name] = deepcopy(invalid_value)
    mutation = {
        "json_path": _text(property_spec.get("json_path")) or _json_path(
            (f"@{location}", name)
        ),
        "field_tokens": [f"@{location}", name],
        "parameter_location": location,
        "constraint": constraint,
        "constraint_value": deepcopy(constraint_value),
        "source": constraint_source,
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
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _base.compile_family_protocol(
        risk_family=risk_family,
        operation=operation,
        operation_ref=operation_ref,
        control_actor_ref=control_actor_ref,
        treatment_actor_ref=treatment_actor_ref,
        property_spec=property_spec,
        behavior_ir=behavior_ir,
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
        location = _text(mutation.get("parameter_location")).lower() or "body"
        if location in {"query", "path", "header"}:
            control_plan[0][location] = deepcopy(control)
            treatment_plan[0][location] = deepcopy(treatment)
            body_example = _base.source_request_example(operation)
            if body_example:
                control_plan[0].setdefault("body", deepcopy(body_example))
                treatment_plan[0].setdefault("body", deepcopy(body_example))
        else:
            control_plan[0]["body"] = deepcopy(control)
            treatment_plan[0]["body"] = deepcopy(treatment)
        treatment_plan[0]["mutation"] = mutation

        # A validation experiment proves nothing unless the treatment request actually
        # differs from the control. Measured on a live target, both plans came out with
        # path "/api/products/:sku" byte-identical while the mutation descriptor claimed
        # remove_required_parameter -- so the executor sent the same request twice, the
        # oracle expected 4xx, saw the control's own 200, and reported a violation. Every
        # read returning 200 became a "validation not enforced" defect.
        #
        # Blocking here is the same discipline the compiler already applies to a
        # synthesized observer: a probe that cannot distinguish its two arms must not
        # run, because its only possible output is a fabricated finding.
        #
        # The comparable set must match what the step executor can faithfully render
        # as a *wire* difference, not merely a plan-dict difference. The executor only
        # realizes "query" (merged into the request line) and "body" (the payload). It
        # never consumes a "header" plan dict, and it treats "path" as a string route
        # template rather than a mutated param dict -- so a path- or header-only
        # mutation produces two byte-identical wire requests and a vacuous runtime
        # contrast. Counting those locations here let such plans run and emit a
        # misleading down-weighted "finding". Restrict the contrast check to the
        # locations the executor renders; a mutation confined to path/header is blocked
        # here, honestly, at compile time. Realizing path/header mutations on the wire
        # (executor path-param substitution + governed-write header threading) is a
        # deliberate, separately-scoped enhancement.
        _comparable = ("query", "body")
        if all(
            control_plan[0].get(field) == treatment_plan[0].get(field)
            for field in _comparable
        ):
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": (
                    "validation_treatment_identical_to_control:"
                    + _text(mutation.get("operator") or "unapplied_mutation")
                ),
            }

        result = {
            **result,
            "control_plan": control_plan,
            "treatment_plan": treatment_plan,
        }

    if not constraint and not _text(property_spec.get("field")):
        return result

    assertion = dict(result.get("assertion") or {})
    operation_effect = _infer_operation_effect(
        operation,
        _text(operation.get("method")).upper(),
    )
    is_ephemeral_session = _is_ephemeral_session_path(
        _text(operation.get("path") or operation.get("raw_path"))
    )
    if operation_effect == "write":
        assertion.update({
            "kind": "validation_rejection",
            "expected_class": 4,
            "expected_effect_count": 0,
            "compare_field": "status_code",
            "effect_field": "treatment_effect_count",
            "control_effect_field": "control_effect_count",
        })
        if is_ephemeral_session:
            # A session/token exchange is a source-declared validation target, but
            # it has no durable entity effect that a control can prove. Keep the
            # strict rejection and zero-side-effect contract while making that
            # evidence boundary explicit; the finalizer projects this same
            # source-derived scope into runtime observations.
            assertion["business_effect_requirement"] = "NOT_APPLICABLE"
        else:
            assertion["expected_control_effect_min"] = 1
    else:
        # A source-declared read-only POST can still enforce input validation,
        # but it has no business mutation whose zero-effect receipt can be
        # observed. Keep the validation verdict on the transport response and
        # do not require an impossible business-effect observer.
        assertion.update({
            "kind": "http_status_class",
            "expected_class": 4,
            "compare_field": "status_code",
        })
    return {
        **result,
        "assertion": assertion,
    }
