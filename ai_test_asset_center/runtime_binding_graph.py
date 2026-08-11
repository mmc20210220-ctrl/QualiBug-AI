"""Public runtime-binding authority facade.

The accumulated resolver/observer/fixture/credential authorities live in
``_runtime_binding_graph_authority_mechanics``. This outer boundary closes two
remaining semantic gaps:

* body resource identities require source-declared FK/reference metadata before
  field-name/collection resolution is formal; and
* effect observers require semantic same-resource authority. Parent paths,
  alternate collections, domain siblings and other affinity candidates remain
  diagnostics only unless an explicit relation chain, exact transport path, or
  frozen create identity-output contract proves the observer.
"""
from __future__ import annotations

import re
from typing import Any

from . import _runtime_binding_graph_authority_mechanics as _authority

for _name in dir(_authority):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_authority, _name)

_original_build_binding_plan = _authority.build_binding_plan
_candidate_effect_observers = _authority.declared_effect_observers


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


def _normalized_field(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


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
    schema = _request_schema_root(operation)
    if not schema:
        return {}
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", _text(body_path))
    node = schema
    for token in tokens:
        while _text(node.get("type")).lower() == "array" and _dict(node.get("items")):
            node = _dict(node.get("items"))
        child = _dict(_dict(node.get("properties")).get(token))
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
        if _text(row.get("source_priority")) not in {
            "same_actor_list_read",
            "fixture_create_only",
        }:
            governed.append(row)
            continue
        body_paths = [
            _text(value)
            for value in _list(row.get("body_template_paths"))
            if _text(value)
        ]
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


def _relation_has_source_authority(relation: dict[str, Any]) -> bool:
    return bool(
        _list(relation.get("source_refs"))
        and _text(relation.get("status")) not in {"conflicting", "unsupported"}
    )


def _explicit_observer_relation_authority(
    source_operation: dict[str, Any],
    observer_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> bool:
    source_ref = _text(
        source_operation.get("id") or source_operation.get("operation_id")
    )
    observer_ref = _text(
        observer_operation.get("id") or observer_operation.get("operation_id")
    )
    if not source_ref or not observer_ref:
        return False

    relations = [
        _dict(row)
        for row in _list(_dict(behavior_ir).get("relations"))
        if isinstance(row, dict) and _relation_has_source_authority(_dict(row))
    ]
    entity_ids = {
        _text(row.get("id"))
        for row in _list(_dict(behavior_ir).get("entities"))
        if isinstance(row, dict) and _text(row.get("id"))
    }

    for relation in relations:
        if _text(relation.get("relation_type")) not in {"observes", "scopes"}:
            continue
        refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
        }
        if source_ref in refs and observer_ref in refs:
            return True

    source_entities: set[str] = set()
    for relation in relations:
        if _text(relation.get("relation_type")) not in {
            "produces",
            "consumes",
            "scopes",
        }:
            continue
        refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
            _text(relation.get("entity_ref")),
        }
        if source_ref not in refs:
            continue
        source_entities.update(ref for ref in refs if ref in entity_ids)
    if not source_entities:
        return False

    for relation in relations:
        if _text(relation.get("relation_type")) not in {"observes", "scopes"}:
            continue
        refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
            _text(relation.get("entity_ref")),
        }
        if observer_ref in refs and source_entities.intersection(refs):
            return True
    return False


def _create_identity_observer_authority(
    source_operation: dict[str, Any],
    observer_operation: dict[str, Any],
) -> bool:
    if _text(source_operation.get("method")).upper() != "POST":
        return False
    binding = _dict(source_operation.get("identity_output_binding"))
    if (
        _text(binding.get("schema_version")) != "qualibug.identity-output-binding.v1"
        or _text(binding.get("status")).upper() != "FROZEN"
        or not _text(binding.get("source_path"))
    ):
        return False

    source_path = _authority.normalize_path_placeholders(
        _text(source_operation.get("path") or source_operation.get("raw_path"))
    ).rstrip("/")
    observer_path = _authority.normalize_path_placeholders(
        _text(observer_operation.get("path") or observer_operation.get("raw_path"))
    )
    if (
        not source_path.startswith("/")
        or _authority.path_has_placeholders(source_path)
        or _text(observer_operation.get("method")).upper() not in {"GET", "HEAD"}
    ):
        return False
    placeholders = list(_authority.extract_placeholders(observer_path))
    if len(placeholders) != 1:
        return False
    if _authority.normalize_path_placeholders(
        _authority.collection_path(observer_path)
    ).rstrip("/") != source_path:
        return False

    aliases = {
        _normalized_field(value)
        for value in [
            binding.get("source_identity_field"),
            *_list(binding.get("alias_targets")),
            *_list(binding.get("consumer_targets")),
            *_list(source_operation.get("identity_binding_aliases")),
        ]
        if _normalized_field(value)
    }
    return _normalized_field(placeholders[0]) in aliases


def _observer_authority(
    source_operation: dict[str, Any],
    observer_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> str:
    source_path = _authority.normalize_path_placeholders(
        _text(source_operation.get("path") or source_operation.get("raw_path"))
    )
    observer_path = _authority.normalize_path_placeholders(
        _text(observer_operation.get("path") or observer_operation.get("raw_path"))
    )
    if (
        _text(observer_operation.get("method")).upper() in {"GET", "HEAD"}
        and source_path.startswith("/")
        and source_path == observer_path
    ):
        return "exact_transport_path"
    if _explicit_observer_relation_authority(
        source_operation,
        observer_operation,
        behavior_ir,
    ):
        return "source_relation_chain"
    if _create_identity_observer_authority(source_operation, observer_operation):
        return "frozen_identity_output"
    return ""


def declared_effect_observers(
    operation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    max_candidates: int = 2,
) -> list[dict[str, str]]:
    """Filter heuristic candidates to formal observer authorities."""

    source_ref = _text(operation.get("id") or operation.get("operation_id"))
    operations = {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }
    source = _dict(operations.get(source_ref))
    if not source:
        return []

    raw_candidates = _candidate_effect_observers(
        source,
        behavior_ir=behavior_ir,
        max_candidates=5,
    )
    governed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_candidates:
        candidate = _dict(raw)
        observer_ref = _text(candidate.get("operation_ref"))
        observer = _dict(operations.get(observer_ref))
        if not observer or not _observer_authority(source, observer, behavior_ir):
            continue
        key = (
            observer_ref,
            _text(observer.get("method")).upper(),
            _authority.normalize_path_placeholders(
                _text(observer.get("path") or observer.get("raw_path"))
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        governed.append(
            {
                "operation_ref": key[0],
                "method": key[1],
                "path": key[2],
            }
        )

    governed.sort(
        key=lambda row: (
            _text(row.get("operation_ref")),
            _text(row.get("method")),
            _text(row.get("path")),
        )
    )
    # Asking for one observer must never hide ambiguity by truncation.
    if int(max_candidates or 1) <= 1 and len(governed) > 1:
        return []
    limit = max(1, min(int(max_candidates or 1), 5))
    return governed[:limit]


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


_authority.build_binding_plan = build_binding_plan
_authority._core.build_binding_plan = build_binding_plan
_authority.declared_effect_observers = declared_effect_observers
_authority._core.declared_effect_observers = declared_effect_observers

__all__ = sorted(
    {
        *[
            name
            for name in dir(_authority)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "build_binding_plan",
        "declared_effect_observers",
        "_body_identity_relation_authority",
        "_govern_body_identity_relations",
        "_observer_authority",
    }
)
