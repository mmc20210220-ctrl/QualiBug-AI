"""Source-only control bodies for request-schema validation probes.

Validation may use a body assembled from JSON Schema/OpenAPI only when every
required value is concretely declared by the source (example/default/const or
one unambiguous enum literal). Type information alone never authorizes a
synthetic value. This authority is validation-scoped: normal request-body
materialization keeps its existing source/observed-data rules.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_INSTALLED = False
_MISSING = object()


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _t(value: Any) -> str:
    return str(value or "").strip()


def _body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    request_schema = _d(_d(operation).get("request_schema"))
    if _d(request_schema.get("properties")):
        return request_schema
    for media in _d(request_schema.get("content")).values():
        schema = _d(_d(media).get("schema"))
        if _d(schema.get("properties")):
            return schema
    if _t(request_schema.get("type")):
        return request_schema
    for media in _d(request_schema.get("content")).values():
        schema = _d(_d(media).get("schema"))
        if schema:
            return schema
    return {}


def _direct_value(schema: dict[str, Any]) -> tuple[Any, str]:
    for key, authority in (
        ("example", "schema_example"),
        ("default", "schema_default"),
        ("const", "schema_const"),
    ):
        if key in schema and schema.get(key) is not None:
            return deepcopy(schema.get(key)), authority
    enum_values = [
        deepcopy(value) for value in _l(schema.get("enum")) if value is not None
    ]
    if len(enum_values) == 1:
        return enum_values[0], "schema_singleton_enum"
    return _MISSING, ""


def _node_value(
    schema: dict[str, Any],
    *,
    depth: int = 0,
) -> tuple[Any, dict[str, Any]]:
    if depth > 12 or not schema:
        return _MISSING, {}
    direct, authority = _direct_value(schema)
    if direct is not _MISSING:
        return direct, {"authority": authority}

    properties = _d(schema.get("properties"))
    if not properties:
        return _MISSING, {}
    required = {_t(value) for value in _l(schema.get("required")) if _t(value)}
    body: dict[str, Any] = {}
    fields: dict[str, Any] = {}
    for field_name, raw_field_schema in properties.items():
        name = str(field_name)
        field_schema = _d(raw_field_schema)
        value, receipt = _node_value(field_schema, depth=depth + 1)
        if value is _MISSING:
            if name in required:
                return _MISSING, {}
            continue
        body[name] = deepcopy(value)
        fields[name] = receipt
    if required and not required.issubset(body):
        return _MISSING, {}
    if not body:
        return _MISSING, {}
    return body, {"authority": "schema_properties", "fields": fields}


def declared_validation_body_control(
    operation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a valid source-declared control body or ``({}, {})``."""

    value, receipt = _node_value(_body_schema(operation))
    if not isinstance(value, dict) or not value:
        return {}, {}
    return deepcopy(value), {
        "authority": "request_schema_concrete_values",
        "schema_receipt": receipt,
        "type_fallback_allowed": False,
        "observed_runtime_value_used": False,
    }


def _constraint_material_with_declared_body(
    operation: dict[str, Any],
    property_spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    from . import experiment_protocols_privacy_base as privacy

    original = getattr(privacy, "_qualibug_original_source_constraint_material", None)
    if not callable(original):
        raise RuntimeError("validation_source_constraint_original_missing")
    control, treatment, mutation, reason = original(operation, property_spec)
    if not reason:
        return control, treatment, mutation, reason
    location = _t(_d(property_spec).get("parameter_location")).lower()
    tokens = _d(property_spec).get("field_tokens")
    if location in {"query", "path", "header"} or (
        isinstance(tokens, list)
        and tokens
        and isinstance(tokens[0], str)
        and tokens[0].startswith("@")
    ):
        return control, treatment, mutation, reason

    schema_control, receipt = declared_validation_body_control(operation)
    if not schema_control:
        return control, treatment, mutation, reason
    projected = dict(_d(operation))
    projected["request_example"] = deepcopy(schema_control)
    control, treatment, mutation, retry_reason = original(projected, property_spec)
    if retry_reason:
        return {}, {}, {}, retry_reason
    mutation = dict(mutation)
    mutation.update({
        "control_value_authority": receipt["authority"],
        "source_declared_control_value": True,
    })
    return control, treatment, mutation, ""


def install_validation_body_control_authority() -> None:
    """Install validation-only schema control fallback for expansion + protocol."""

    global _INSTALLED
    if _INSTALLED:
        return
    from . import _validation_obligation_expander_core as expander
    from . import experiment_protocols_privacy_base as privacy

    if not hasattr(expander, "_qualibug_original_request_example"):
        expander._qualibug_original_request_example = expander._request_example
    original_request_example = expander._qualibug_original_request_example

    def _request_example_with_declared_schema(
        operation: dict[str, Any],
    ) -> dict[str, Any]:
        existing = original_request_example(operation)
        if isinstance(existing, dict) and existing:
            return existing
        control, _receipt = declared_validation_body_control(operation)
        return control

    expander._request_example = _request_example_with_declared_schema

    if not hasattr(privacy, "_qualibug_original_source_constraint_material"):
        privacy._qualibug_original_source_constraint_material = privacy._source_constraint_material
    privacy._source_constraint_material = _constraint_material_with_declared_body
    _INSTALLED = True


__all__ = [
    "declared_validation_body_control",
    "install_validation_body_control_authority",
]
