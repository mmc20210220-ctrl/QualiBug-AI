"""Validate and materialize runtime bindings from source-declared operations."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from .real_id_resolver import (
    bind_entity_fields,
    infer_path_params,
    normalize_path_placeholders,
    param_field_candidates,
    path_has_placeholders,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


_PATH_PARAMETER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BODY_PLACEHOLDER_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def _response_scalar_fields(value: Any) -> dict[str, list[Any]]:
    fields: dict[str, list[Any]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, (str, int)) and not isinstance(child, bool):
                    fields.setdefault(_field_key(key), []).append(child)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return fields


def runtime_cleanup_bindings(
    path_template: str,
    steps: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], list[str]]:
    placeholders = _PATH_PARAMETER_RE.findall(_text(path_template))
    if not placeholders:
        return _text(path_template), {}, []
    fields: dict[str, list[Any]] = {}
    for step in steps:
        if not isinstance(step, dict) or _text(step.get("phase")) not in {"control", "treatment"}:
            continue
        if not (200 <= int(step.get("status_code") or 0) < 300):
            continue
        for key, values in _response_scalar_fields(step.get("body")).items():
            fields.setdefault(key, []).extend(values)
    bindings: dict[str, Any] = {}
    missing: list[str] = []
    for name in placeholders:
        normalized = _field_key(name)
        candidates = list(dict.fromkeys(fields.get(normalized) or []))
        if not candidates and normalized == "id":
            id_values = [
                value
                for key, values in fields.items()
                if key.endswith("id")
                for value in values
            ]
            candidates = list(dict.fromkeys(id_values))
        if len(candidates) != 1:
            missing.append(name)
            continue
        bindings[name] = candidates[0]
    materialized = _text(path_template)
    for name, value in bindings.items():
        materialized = materialized.replace("{" + name + "}", quote(str(value), safe=""))
    return materialized, bindings, missing


def validated_runtime_resolvers(
    binding: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    resolvers: list[dict[str, str]] = []
    if _text(binding.get("status")) != "runtime_resolvable":
        return resolvers
    for raw in _list(binding.get("resolver_operations")):
        if not isinstance(raw, dict):
            continue
        operation_ref = _text(raw.get("operation_ref"))
        declared = operations.get(operation_ref) or {}
        method = _text(raw.get("method")).upper()
        path = normalize_path_placeholders(_text(raw.get("path")))
        declared_method = _text(declared.get("method")).upper()
        declared_path = normalize_path_placeholders(
            _text(declared.get("path") or declared.get("raw_path"))
        )
        if (
            not operation_ref
            or method not in {"GET", "HEAD"}
            or method != declared_method
            or path != declared_path
            or not path.startswith("/")
            or path_has_placeholders(path)
        ):
            continue
        resolvers.append({
            "operation_ref": operation_ref,
            "method": method,
            "path": path,
        })
    return resolvers


def runtime_binding_contract_ready(
    path: str,
    *,
    binding_plan: list[Any],
    fixture_dag: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> bool:
    targets = infer_path_params(normalize_path_placeholders(path))
    if not targets:
        return True
    bindings = {
        _text(item.get("target")): item
        for item in binding_plan
        if isinstance(item, dict) and _text(item.get("target"))
    }
    dag_targets = {
        _text(node.get("target"))
        for node in _list(fixture_dag.get("nodes"))
        if isinstance(node, dict)
        and _text(node.get("kind")) == "runtime_read_binding"
        and node.get("constructible") is not False
    }
    return all(
        target in dag_targets
        and bool(validated_runtime_resolvers(bindings.get(target) or {}, operations))
        for target in targets
    )


def materialize_path(path: str, bindings: dict[str, Any]) -> str:
    materialized = normalize_path_placeholders(path)
    for name in infer_path_params(materialized):
        value = bindings.get(name)
        if value in (None, "", [], {}):
            continue
        materialized = materialized.replace("{" + name + "}", quote(str(value), safe=""))
    return materialized


def materialize_body_template(value: Any, token_values: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {
            key: materialize_body_template(child, token_values)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [materialize_body_template(child, token_values) for child in value]
    if isinstance(value, str):
        match = _BODY_PLACEHOLDER_RE.match(value)
        if match:
            token = _text(match.group(1))
            if token in token_values:
                return token_values[token]
    return value


def runtime_value_from_response(body: Any, target: str, target_path: str) -> Any:
    bindings = bind_entity_fields(body, target_path or f"/{{{target}}}")
    value = bindings.get(target)
    if value not in (None, "", [], {}):
        return value
    normalized = _field_key(target)
    fields = _response_scalar_fields(body)
    candidates = list(dict.fromkeys(fields.get(normalized) or []))
    if not candidates and normalized.endswith("id"):
        candidates = list(dict.fromkeys(fields.get("id") or []))
    return candidates[0] if len(candidates) == 1 else None


def runtime_setup_value_from_response(body: Any, target: str) -> Any:
    """Capture the created resource identity without descending into child items."""
    candidates = param_field_candidates(target)
    sources = [body] if isinstance(body, dict) else []
    if isinstance(body, dict):
        for wrapper in ("data", "result", "resource", "item"):
            nested = body.get(wrapper)
            if isinstance(nested, dict):
                sources.append(nested)
    for source in sources:
        for field in candidates:
            value = source.get(field)
            if value not in (None, "", [], {}):
                return value
    return None


def validated_fixture_setup(
    binding: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    setup = _dict(binding.get("fixture_setup"))
    operation_ref = _text(setup.get("operation_ref"))
    operation = operations.get(operation_ref) or {}
    method = _text(setup.get("method")).upper()
    path = normalize_path_placeholders(_text(setup.get("path")))
    if (
        not operation_ref
        or method != "POST"
        or _text(operation.get("method")).upper() != method
        or normalize_path_placeholders(_text(operation.get("path") or operation.get("raw_path"))) != path
        or not path.startswith("/")
        or path_has_placeholders(path)
        or not isinstance(setup.get("body_template"), dict)
        or not setup.get("body_template")
    ):
        return {}
    body_bindings: list[dict[str, Any]] = []
    for raw in _list(setup.get("body_bindings")):
        if not isinstance(raw, dict):
            return {}
        resolvers = validated_runtime_resolvers(
            {
                "status": "runtime_resolvable",
                "resolver_operations": raw.get("resolver_operations"),
            },
            operations,
        )
        if not _text(raw.get("target")) or not _text(raw.get("template_token")) or not resolvers:
            return {}
        body_bindings.append({
            "target": _text(raw.get("target")),
            "template_token": _text(raw.get("template_token")),
            "resolver_operations": resolvers,
        })
    cleanup_operations: list[dict[str, str]] = []
    for raw in _list(setup.get("cleanup_operations")):
        if not isinstance(raw, dict):
            continue
        cleanup_ref = _text(raw.get("operation_ref"))
        cleanup = operations.get(cleanup_ref) or {}
        cleanup_method = _text(raw.get("method")).upper()
        cleanup_path = normalize_path_placeholders(_text(raw.get("path")))
        if (
            cleanup_ref
            and cleanup_method in {"DELETE", "POST", "PATCH", "PUT"}
            and _text(cleanup.get("method")).upper() == cleanup_method
            and normalize_path_placeholders(_text(cleanup.get("path") or cleanup.get("raw_path"))) == cleanup_path
            and cleanup_path.startswith("/")
            and path_has_placeholders(cleanup_path)
        ):
            cleanup_operations.append({
                "operation_ref": cleanup_ref,
                "method": cleanup_method,
                "path": cleanup_path,
            })
    if not cleanup_operations:
        return {}
    actor_refs = [
        _text(actor_ref)
        for actor_ref in _list(setup.get("actor_refs"))
        if _text(actor_ref) in actors
    ]
    if not actor_refs:
        return {}
    return {
        "operation_ref": operation_ref,
        "method": method,
        "path": path,
        "actor_refs": list(dict.fromkeys(actor_refs)),
        "body_template": dict(setup["body_template"]),
        "body_bindings": body_bindings,
        "cleanup_operations": cleanup_operations,
    }
