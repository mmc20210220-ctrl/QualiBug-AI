"""Fail-closed request-body recovery from source-declared API schemas.

The experiment compiler historically treated an explicit request example as the
only source-backed body for PUT/PATCH probes.  OpenAPI contracts frequently omit
examples while still declaring a complete deterministic body through ``const``,
``default`` or a single-value ``enum``.  Blocking those operations loses an
otherwise executable obligation.

This module deliberately does *not* synthesize arbitrary values.  A request body
is materialized only when every required field is independently source-attested.
Any ambiguous required field keeps the existing BLOCKED behavior.
"""
from __future__ import annotations

from copy import deepcopy
import sys
from typing import Any, Callable


SCHEMA_VERSION = "qualibug.source-request-schema-materialization.v1"
_MISSING = object()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_schema_for_operation(operation: dict[str, Any]) -> dict[str, Any]:
    """Return one unambiguous JSON request schema, otherwise an empty mapping."""
    request = _dict(
        _dict(operation).get("request_schema")
        or _dict(operation).get("requestBody")
    )
    if not request:
        return {}

    content = _dict(request.get("content"))
    if not content:
        nested = _dict(request.get("schema"))
        return nested or request

    media = _dict(content.get("application/json"))
    if not media:
        json_media = [
            value
            for key, value in content.items()
            if isinstance(value, dict)
            and (
                _text(key).lower().endswith("+json")
                or _text(key).lower().endswith("/json")
            )
        ]
        if len(json_media) != 1:
            return {}
        media = json_media[0]
    return _dict(media.get("schema"))


def _value_matches_declared_type(value: Any, schema: dict[str, Any]) -> bool:
    """Reject source values that contradict the same source schema's basic type."""
    declared = _text(schema.get("type")).lower()
    if not declared:
        return True
    if declared == "null":
        return value is None
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "string":
        return isinstance(value, str)
    if declared == "array":
        return isinstance(value, list)
    if declared == "object":
        return isinstance(value, dict)
    return True


def _source_scalar_or_container(schema: dict[str, Any]) -> tuple[bool, Any, str]:
    """Resolve a value that the schema itself states exactly.

    Explicit example is accepted because the existing compiler already treats
    source examples as executable authority.  ``default`` is not upgraded when
    it contradicts a declared enum/type.
    """
    candidate = _MISSING
    authority = ""
    if "const" in schema:
        candidate = schema.get("const")
        authority = "const"
    elif "default" in schema:
        candidate = schema.get("default")
        authority = "default"
    else:
        enum = schema.get("enum")
        if isinstance(enum, list) and len(enum) == 1:
            candidate = enum[0]
            authority = "single_enum"
        elif "example" in schema:
            candidate = schema.get("example")
            authority = "example"

    if candidate is _MISSING:
        return False, None, ""
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and candidate not in enum:
        return False, None, ""
    if not _value_matches_declared_type(candidate, schema):
        return False, None, ""
    return True, deepcopy(candidate), authority


def _materialize_schema_node(
    schema: dict[str, Any],
    *,
    path: str,
) -> tuple[bool, Any, list[dict[str, str]]]:
    exact, value, authority = _source_scalar_or_container(schema)
    if exact:
        return True, value, [{"path": path, "authority": authority}]

    properties = _dict(schema.get("properties"))
    required = [
        _text(name)
        for name in _list(schema.get("required"))
        if _text(name)
    ]
    if not properties or not required:
        # Choosing optional fields (or inventing a value for a scalar) changes
        # request semantics without source authority.  Keep the probe blocked.
        return False, None, []

    body: dict[str, Any] = {}
    receipts: list[dict[str, str]] = []
    for field in required:
        child = properties.get(field)
        if not isinstance(child, dict):
            return False, None, []
        ok, child_value, child_receipts = _materialize_schema_node(
            child,
            path=f"{path}.{field}" if path else field,
        )
        if not ok:
            return False, None, []
        body[field] = child_value
        receipts.extend(child_receipts)
    return True, body, receipts


def materialize_authoritative_request_body(
    operation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize a deterministic object body and its auditable receipt."""
    schema = _json_schema_for_operation(operation)
    if not schema:
        return {}, {
            "schema_version": SCHEMA_VERSION,
            "status": "UNAVAILABLE",
            "reason": "request_json_schema_unavailable_or_ambiguous",
            "fields": [],
        }

    ok, value, fields = _materialize_schema_node(schema, path="$request")
    if not ok or not isinstance(value, dict) or not value:
        return {}, {
            "schema_version": SCHEMA_VERSION,
            "status": "UNAVAILABLE",
            "reason": "required_request_fields_not_deterministic",
            "fields": [],
        }

    # Preserve the established placeholder-identity handling for any documented
    # source value that is itself a placeholder literal.
    from .runtime_binding_graph import _tokenize_placeholder_identity_values

    body = _tokenize_placeholder_identity_values(deepcopy(value))
    return body, {
        "schema_version": SCHEMA_VERSION,
        "status": "MATERIALIZED",
        "reason": "source_declared_deterministic_schema",
        "fields": fields,
    }


def _wrap_source_request_example(
    original: Callable[..., dict[str, Any]],
) -> Callable[..., dict[str, Any]]:
    if getattr(original, "_qualibug_schema_materialization_wrapper", False):
        return original

    def wrapped(
        operation: dict[str, Any],
        *,
        sibling_operations: list[Any] | None = None,
    ) -> dict[str, Any]:
        existing = original(
            operation,
            sibling_operations=sibling_operations,
        )
        if isinstance(existing, dict) and existing:
            return existing
        body, _receipt = materialize_authoritative_request_body(operation)
        return body

    setattr(wrapped, "_qualibug_schema_materialization_wrapper", True)
    setattr(wrapped, "_qualibug_original_source_request_example", original)
    return wrapped


def install_source_request_schema_materialization() -> None:
    """Install one wrapper on all already-loaded compiler bindings.

    Several compiler modules import ``_source_request_example`` by value.  The
    product already uses import-time bridge installers for additive compiler
    authority, so the installer updates both the support authority and those
    loaded local bindings.  Future imports receive the wrapped support symbol.
    """
    from . import experiment_compiler_support as support

    wrapped = _wrap_source_request_example(support._source_request_example)
    support._source_request_example = wrapped

    package = __name__.rsplit(".", 1)[0]
    for suffix in (
        "experiment_compiler_obligation_core",
        "_experiment_compiler_base_mechanics",
        "experiment_compiler",
    ):
        module = sys.modules.get(f"{package}.{suffix}")
        if module is not None and hasattr(module, "_source_request_example"):
            setattr(module, "_source_request_example", wrapped)
