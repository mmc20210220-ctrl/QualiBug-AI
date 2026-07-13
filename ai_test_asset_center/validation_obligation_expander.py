"""Expand one source-grounded validation obligation into field-specific variants."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = operation.get("request_example")
    if isinstance(direct, dict) and direct:
        return deepcopy(direct)
    request_schema = _dict(operation.get("request_schema"))
    for media in _dict(request_schema.get("content")).values():
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


def _matches_declared_type(value: Any, declared_type: str) -> bool:
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


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def _constraint_targets(
    *,
    field: str,
    field_schema: dict[str, Any],
    example_value: Any,
    required: bool,
) -> list[tuple[str, Any]]:
    targets: list[tuple[str, Any]] = []
    if required:
        targets.append(("required", True))

    declared_type = _text(field_schema.get("type")).lower()
    if declared_type and _matches_declared_type(example_value, declared_type):
        targets.append((f"type:{declared_type}", declared_type))

    enum_values = _list(field_schema.get("enum"))
    if enum_values and example_value in enum_values:
        targets.append(("enum", deepcopy(enum_values)))

    if isinstance(example_value, str):
        min_length = field_schema.get("minLength")
        if (
            isinstance(min_length, int)
            and not isinstance(min_length, bool)
            and min_length > 0
            and len(example_value) >= min_length
        ):
            targets.append(("minLength", min_length))
        max_length = field_schema.get("maxLength")
        if (
            isinstance(max_length, int)
            and not isinstance(max_length, bool)
            and max_length >= 0
            and len(example_value) <= max_length
        ):
            targets.append(("maxLength", max_length))
        pattern = _text(field_schema.get("pattern"))
        if pattern:
            try:
                matches = re.search(pattern, example_value) is not None
            except re.error:
                matches = False
            if matches:
                targets.append(("pattern", pattern))

    if _numeric(example_value):
        minimum = field_schema.get("minimum")
        if _numeric(minimum) and example_value >= minimum:
            targets.append(("minimum", minimum))
        exclusive_minimum = _exclusive_boundary(
            field_schema,
            "exclusiveMinimum",
            "minimum",
        )
        if (
            exclusive_minimum is not None
            and example_value > exclusive_minimum
        ):
            targets.append(("exclusiveMinimum", exclusive_minimum))
        maximum = field_schema.get("maximum")
        if _numeric(maximum) and example_value <= maximum:
            targets.append(("maximum", maximum))
        exclusive_maximum = _exclusive_boundary(
            field_schema,
            "exclusiveMaximum",
            "maximum",
        )
        if (
            exclusive_maximum is not None
            and example_value < exclusive_maximum
        ):
            targets.append(("exclusiveMaximum", exclusive_maximum))

    if isinstance(example_value, list):
        min_items = field_schema.get("minItems")
        if (
            isinstance(min_items, int)
            and not isinstance(min_items, bool)
            and min_items > 0
            and len(example_value) >= min_items
        ):
            targets.append(("minItems", min_items))
        max_items = field_schema.get("maxItems")
        if (
            isinstance(max_items, int)
            and not isinstance(max_items, bool)
            and max_items >= 0
            and len(example_value) <= max_items
        ):
            targets.append(("maxItems", max_items))
    return targets


def _variant_id(obligation_id: str, field: str, constraint: str) -> str:
    material = json.dumps(
        [obligation_id, field, constraint],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{obligation_id}__v_{digest}"


def _with_validation_effect_observer(
    obligation: dict[str, Any],
) -> dict[str, Any]:
    row = deepcopy(_dict(obligation))
    observers = [
        _text(value)
        for value in _list(row.get("required_observers"))
        if _text(value)
    ]
    for observer_id in ("http_response", "business_effect"):
        if observer_id not in observers:
            observers.append(observer_id)
    row["required_observers"] = observers
    return row


def expand_validation_obligation(
    obligation: dict[str, Any],
    *,
    operation: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return deterministic source-schema validation variants.

    Only constraints explicitly declared in the request schema are expanded.
    Every variant requires a business-effect observer so rejected requests that
    still mutate persistent state cannot be misclassified as passing checks.
    """

    obl = _dict(obligation)
    if _text(obl.get("risk_family")) != "validation":
        return [obl]
    guarded = _with_validation_effect_observer(obl)
    prop = _dict(guarded.get("property"))
    explicit_field = next(
        (
            _text(prop.get(key)).removeprefix("$.")
            for key in ("field", "field_name", "field_ref", "json_path")
            if _text(prop.get(key))
        ),
        "",
    )
    if explicit_field and _text(prop.get("validation_constraint")):
        return [guarded]

    schema = _body_schema(_dict(operation))
    properties = _dict(schema.get("properties"))
    example = _request_example(_dict(operation))
    if not properties or not example:
        return [guarded]

    required_fields = {
        _text(value)
        for value in _list(schema.get("required"))
        if _text(value)
    }
    selected_fields = (
        [explicit_field]
        if explicit_field
        else [str(field) for field in properties]
    )
    targets: list[tuple[str, str, Any]] = []
    for field in selected_fields:
        field_schema = _dict(properties.get(field))
        if field not in example or not field_schema:
            continue
        for constraint, constraint_value in _constraint_targets(
            field=field,
            field_schema=field_schema,
            example_value=example[field],
            required=field in required_fields,
        ):
            targets.append((field, constraint, constraint_value))

    if not targets:
        return [guarded]

    original_id = (
        _text(guarded.get("obligation_id"))
        or "validation_obligation"
    )
    variants: list[dict[str, Any]] = []
    for field, constraint, constraint_value in targets:
        variant = deepcopy(guarded)
        variant["obligation_id"] = _variant_id(
            original_id,
            field,
            constraint,
        )
        variant_property = dict(prop)
        variant_property.update({
            "field": field,
            "json_path": f"$.{field}",
            "validation_constraint": constraint,
            "validation_constraint_value": deepcopy(constraint_value),
            "expanded_from_obligation_id": original_id,
            "expected_rejection_status_class": 4,
            "expected_treatment_effect_count": 0,
        })
        variant["property"] = variant_property
        variant["compile_status"] = "PENDING"
        variants.append(variant)
    return variants
