"""Public runtime-binding authority facade.

The accumulated resolver/observer/fixture/credential authorities live in
``_runtime_binding_graph_authority_mechanics``. This outer boundary closes one
remaining semantic gap: a body field whose value is a resource identity may not
be linked to a collection merely because its name resembles ``addressId`` and a
real ``/addresses`` GET exists.

Formal body-identity resolution requires source-declared relationship evidence
on that request field (foreign-key/reference metadata). Path identity and the
existing ownership-identity channel are unchanged. Non-identity business scalar
bindings remain eligible for their existing source-grounded mechanisms.
"""
from __future__ import annotations

import re
from typing import Any

from . import _runtime_binding_graph_authority_mechanics as _authority

for _name in dir(_authority):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_authority, _name)

_original_build_binding_plan = _authority.build_binding_plan


def __getattr__(name: str) -> Any:
    return getattr(_authority, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_authority)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _request_schema_root(operation: dict[str, Any]) -> dict[str, Any]:
    schema = _dict(
        operation.get("request_schema") or operation.get("requestBody")
    )
    content = _dict(schema.get("content"))
    if content:
        media = _dict(content.get("application/json"))
        nested = _dict(media.get("schema"))
        if nested:
            schema = nested
    return schema


def _schema_node_for_body_path(
    operation: dict[str, Any],
    body_path: str,
) -> dict[str, Any]:
    """Resolve a request-schema node by structural body path only."""

    schema = _request_schema_root(operation)
    if not schema:
        return {}
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", _text(body_path))
    node = schema
    for token in tokens:
        while _text(node.get("type")).lower() == "array" and _dict(node.get("items")):
            node = _dict(node.get("items"))
        properties = _dict(node.get("properties"))
        child = _dict(properties.get(token))
        if not child:
            return {}
        node = child
    return node


def _reference_marker(node: dict[str, Any]) -> str:
    if node.get("x-foreign-key") is True or node.get("foreign_key") is True:
        return "request_schema_foreign_key"
    for field in (
        "x-reference",
        "x-reference-target",
        "x-resource-ref",
        "x-entity-ref",
        "reference_ref",
        "entity_ref",
    ):
        value = node.get(field)
        if value is True or _text(value):
            return f"request_schema_{field.replace('-', '_')}"
    return ""


def _field_dictionary_reference_authority(
    operation: dict[str, Any],
    body_path: str,
) -> str:
    leaf = _text(body_path).split(".")[-1].split("[")[0]
    normalized_path = re.sub(r"\[\d+\]", "[]", _text(body_path))
    for raw in _list(operation.get("field_dictionary")):
        row = _dict(raw)
        field = _text(
            row.get("field_path") or row.get("field") or row.get("name")
        )
        if not field:
            continue
        normalized_field = re.sub(r"\[\d+\]", "[]", field)
        if normalized_field not in {normalized_path, leaf} and field != leaf:
            continue
        if row.get("foreign_key") is True:
            return "field_dictionary_foreign_key"
        for key in (
            "reference_ref",
            "entity_ref",
            "resource_ref",
            "foreign_key_ref",
        ):
            if _text(row.get(key)):
                return f"field_dictionary_{key}"
    return ""


def _body_identity_relation_authority(
    operation: dict[str, Any],
    body_paths: list[str],
) -> tuple[bool, str]:
    """Require every concrete body occurrence to carry explicit reference evidence."""

    paths = list(dict.fromkeys(_text(path) for path in body_paths if _text(path)))
    if not paths:
        return False, "body_identity_path_missing"
    authorities: list[str] = []
    for path in paths:
        authority = _reference_marker(_schema_node_for_body_path(operation, path))
        if not authority:
            authority = _field_dictionary_reference_authority(operation, path)
        if not authority:
            return False, f"body_identity_relation_undeclared:{path}"
        authorities.append(authority)
    return True, "+".join(sorted(set(authorities)))


def _govern_body_identity_relations(
    plan: list[dict[str, Any]],
    *,
    operation: dict[str, Any],
) -> list[dict[str, Any]]:
    path_placeholders = set(
        _authority.extract_placeholders(
            operation.get("path"),
            operation.get("operation_id"),
            *[str(value) for value in _list(operation.get("parameters"))],
        )
    )
    ownership_params = set(
        _authority._ownership_params_declared_on_operation(operation)
    )
    governed: list[dict[str, Any]] = []
    for raw in plan:
        row = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(row, dict):
            governed.append(row)
            continue
        target = _text(row.get("target"))
        if (
            not _authority._identity_shaped_target(target)
            or target in path_placeholders
            or target in ownership_params
            or _text(row.get("source_priority"))
            in {
                "ownership_identity_param",
                "actor_credential_secret",
                "sequential_output_binding",
                "runtime_actor_secret_ref",
            }
        ):
            governed.append(row)
            continue

        body_paths = [
            _text(value)
            for value in _list(row.get("body_template_paths"))
            if _text(value)
        ]
        # Only body-identity rows that use entity-resolution channels need this
        # relation gate. A separately sealed output/materialization authority is
        # not reinterpreted by field naming here.
        if _text(row.get("source_priority")) not in {
            "same_actor_list_read",
            "fixture_create_only",
        }:
            governed.append(row)
            continue

        allowed, authority = _body_identity_relation_authority(
            operation,
            body_paths,
        )
        if allowed:
            row["body_identity_relation_authority"] = authority
            governed.append(row)
            continue

        row.update(
            {
                "status": "blocked",
                "source_priority": "body_identity_relation_unresolved",
                "resolver_operations": [],
                "value_fingerprint": "",
                "blocked_reason": "BODY_IDENTITY_RELATION_NOT_SOURCE_DECLARED",
                "body_identity_relation_authority": authority,
            }
        )
        row.pop("fixture_setup", None)
        governed.append(row)
    return governed


def build_binding_plan(
    *,
    operation: dict[str, Any],
    obligation: dict[str, Any],
    actors: list[dict[str, Any]] | None = None,
    available_values: dict[str, dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plan = _original_build_binding_plan(
        operation=operation,
        obligation=obligation,
        actors=actors,
        available_values=available_values,
        behavior_ir=behavior_ir,
    )
    return _govern_body_identity_relations(plan, operation=operation)


# Keep internal dynamic call sites on the public authority while preserving the
# already-saved historical build function inside the accumulated mechanics.
_authority.build_binding_plan = build_binding_plan
_authority._core.build_binding_plan = build_binding_plan

__all__ = sorted(
    {
        *[
            name
            for name in dir(_authority)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "build_binding_plan",
        "_body_identity_relation_authority",
        "_govern_body_identity_relations",
    }
)
