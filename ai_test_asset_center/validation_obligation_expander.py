"""Expand one source-grounded validation obligation into field-specific variants."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
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
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    if declared_type == "boolean":
        return isinstance(value, bool)
    if declared_type == "array":
        return isinstance(value, list)
    if declared_type == "object":
        return isinstance(value, dict)
    if declared_type == "null":
        return value is None
    return False


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
    """Return deterministic field-specific variants or one guarded original.

    Expansion is restricted to fields explicitly present in the documented
    request example and request schema. Every validation experiment also
    requires a business-effect observer so a rejected request that still writes
    data cannot be misclassified as a passing validation check.
    """

    obl = _dict(obligation)
    if _text(obl.get("risk_family")) != "validation":
        return [obl]
    guarded = _with_validation_effect_observer(obl)
    prop = _dict(guarded.get("property"))
    if any(
        _text(prop.get(key))
        for key in ("field", "field_name", "field_ref", "json_path")
    ):
        return [guarded]

    schema = _body_schema(_dict(operation))
    properties = _dict(schema.get("properties"))
    example = _request_example(_dict(operation))
    if not properties or not example:
        return [guarded]

    required = [
        _text(value)
        for value in _list(schema.get("required"))
        if _text(value)
    ]
    targets: list[tuple[str, str]] = []
    if required:
        targets = [
            (field, "required")
            for field in required
            if field in properties and field in example
        ]
    else:
        for field in sorted(properties):
            declared_type = _text(
                _dict(properties.get(field)).get("type")
            ).lower()
            if (
                field in example
                and declared_type
                and _matches_declared_type(
                    example[field],
                    declared_type,
                )
            ):
                targets.append((field, f"type:{declared_type}"))

    if len(targets) <= 1:
        return [guarded]

    original_id = (
        _text(guarded.get("obligation_id"))
        or "validation_obligation"
    )
    variants: list[dict[str, Any]] = []
    for field, constraint in targets:
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
            "expanded_from_obligation_id": original_id,
            "expected_rejection_status_class": 4,
            "expected_treatment_effect_count": 0,
        })
        variant["property"] = variant_property
        variant["compile_status"] = "PENDING"
        variants.append(variant)
    return variants
