"""State-aware, domain-neutral runtime identity resolution.

The stable low-level response/entity mechanics remain in
``real_id_resolver_base``. This facade removes the closed domain dictionaries
from the active resolver path and replaces them with structural derivation:

* parameter aliases are exact/snake/camel spelling plus generic primary-key
  compatibility, never order/user/coupon/patient/etc. vocabulary;
* body dependency collection candidates come from the field token's own entity
  stem and generic pluralization;
* alternate list paths come from the real path hierarchy and identity-token
  stems, never products/materials/users/accounts catalogs.

All candidates are still intersected with Behavior IR's source-declared
operations by the binding graph. Unknown semantics therefore remain unresolved
instead of being forced through a familiar industry alias.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import real_id_resolver_base as _base
from .real_id_resolver_base import *  # noqa: F401,F403


_STATE_PATH_RE = re.compile(r"^@state=([a-z0-9]+)@(.*)$")
_original_bind_entity_fields = _base.bind_entity_fields
_GENERIC_PRIMARY_KEYS = ("id", "uuid", "guid", "pk", "key")
_IDENTITY_SUFFIXES = ("uuid", "guid", "number", "code", "key", "id", "no")


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _snake_identity_name(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", raw).replace("-", "_")
    return re.sub(r"_+", "_", snake).strip("_").lower()


def _camel_identity_name(value: Any) -> str:
    snake = _snake_identity_name(value)
    if not snake:
        return ""
    parts = [part for part in snake.split("_") if part]
    if not parts:
        return ""
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _identity_entity_stem(value: Any) -> str:
    key = _field_key(value)
    if not key:
        return ""
    for suffix in _IDENTITY_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix) + 1:
            stem = key[: -len(suffix)].strip("_")
            if stem:
                return stem
    return ""


def param_field_candidates(param_name: str) -> list[str]:
    """Domain-neutral response fields that may satisfy one path parameter."""

    name = _text(param_name)
    if not name:
        return ["id"]
    key = _field_key(name)
    snake = _snake_identity_name(name)
    camel = _camel_identity_name(name)
    ordered = [name, snake, camel]
    if key in {_field_key(item) for item in _GENERIC_PRIMARY_KEYS}:
        ordered.extend(_GENERIC_PRIMARY_KEYS)
    elif _identity_entity_stem(name):
        # A single entity-qualified identity may be exposed by an API as its
        # generic primary key. Multi-identity paths are governed separately and
        # do not use this cross-spelling fallback until ambiguity is eliminated.
        ordered.extend(_GENERIC_PRIMARY_KEYS)
    # Natural keys such as sku/code have no entity stem and stay exact. Mapping
    # sku->code or code->coupon is business semantics, not a naming convention.
    result: list[str] = []
    seen: set[str] = set()
    for value in ordered:
        token = _text(value)
        # Keep userId and user_id as distinct structural spellings; callers such
        # as the legacy single-identity body resolver perform exact dict lookup.
        # Only exact case-insensitive duplicates are removed here.
        duplicate_key = token.lower()
        if token and duplicate_key not in seen:
            seen.add(duplicate_key)
            result.append(token)
    return result


def body_field_collection_paths(
    field: str,
    *,
    api_prefix: str = "/api",
) -> list[str]:
    """Derive collection candidates from the field's own entity stem only."""

    stem = _identity_entity_stem(field)
    if not stem:
        return []
    prefix = _text(api_prefix).rstrip("/") or "/api"
    plural = _base._pluralize_resource(stem)
    candidates = [
        f"{prefix}/{plural}" if plural else "",
        f"{prefix}/{stem}",
    ]
    return list(
        dict.fromkeys(
            candidate
            for candidate in candidates
            if candidate.startswith("/")
        )
    )


def _api_identity_prefix(path: str) -> str:
    parts = [
        part
        for part in _base.normalize_path_placeholders(path).strip("/").split("/")
        if part and not _base._NORMALIZED_PARAM_RE.fullmatch(part)
    ]
    if not parts:
        return ""
    if parts[0].lower() == "api":
        if len(parts) > 1 and re.fullmatch(r"v\d+(?:\.\d+)?", parts[1].lower()):
            return "/" + "/".join(parts[:2])
        return "/api"
    if re.fullmatch(r"v\d+(?:\.\d+)?", parts[0].lower()):
        return "/" + parts[0]
    return "/" + parts[0]


def alternate_collection_paths(path: str) -> list[str]:
    """Return only structural parent/token collection alternatives."""

    normalized = _base.normalize_path_placeholders(path).split("?", 1)[0]
    params = _base.infer_path_params(normalized)
    if not params:
        return []
    primary = _base.collection_path(normalized)
    prefix = _api_identity_prefix(normalized)
    alternatives: list[str] = []

    if prefix:
        for param in params:
            for candidate in body_field_collection_paths(param, api_prefix=prefix):
                if candidate != primary:
                    alternatives.append(candidate)

    parts = [part for part in primary.strip("/").split("/") if part]
    for size in range(len(parts) - 1, 0, -1):
        candidate = "/" + "/".join(parts[:size])
        if candidate and candidate != primary:
            alternatives.append(candidate)

    return list(
        dict.fromkeys(
            candidate
            for candidate in alternatives
            if candidate.startswith("/")
            and not _base.path_has_placeholders(candidate)
        )
    )


def _state_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).casefold())


def _entity_state_values(entity: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for key, value in entity.items():
        normalized = _field_key(key)
        if isinstance(value, (dict, list, bool)) or value in (None, ""):
            continue
        if (
            normalized in {"state", "status", "stage", "lifecycle", "lifecyclestatus"}
            or normalized.endswith("status")
            or normalized.endswith("state")
            or normalized.endswith("stage")
        ):
            values.append(value)
    return values


def _identity_sort_key(entity: dict[str, Any]) -> tuple[str, str]:
    identities = [
        _text(value)
        for key, value in entity.items()
        if not isinstance(value, (dict, list))
        and (
            _field_key(key) in {_field_key(item) for item in _GENERIC_PRIMARY_KEYS}
            or _field_key(key).endswith("id")
        )
        and _text(value)
    ]
    return (
        identities[0] if identities else "",
        json.dumps(
            entity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


def _entity_in_required_state(body: Any, required_token: str) -> dict[str, Any]:
    matches = [
        dict(entity)
        for entity in _base._extract_entity_candidates(body)
        if any(
            _state_token(value) == required_token
            for value in _entity_state_values(entity)
        )
    ]
    if not matches:
        return {}
    return sorted(matches, key=_identity_sort_key)[0]


def _scalar(value: Any) -> str:
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        return ""
    return _text(value)


def _strict_multi_identity_bindings(
    body: Any,
    path: str,
) -> dict[str, str]:
    """Bind a multi-identity path without cross-resource id mirroring."""

    params = _base.infer_path_params(path)
    if len(params) <= 1:
        return _original_bind_entity_fields(body, path)
    entities = _base._extract_entity_candidates(body)
    if not entities and isinstance(body, dict):
        entities = [body]
    if not entities:
        return {}

    bindings: dict[str, str] = {}
    unresolved: list[str] = []
    generic_params = {_field_key(item) for item in _GENERIC_PRIMARY_KEYS}

    for param in params:
        wanted = _field_key(param)
        values: list[tuple[str, str]] = []
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            for field, raw in entity.items():
                if _field_key(field) != wanted:
                    continue
                value = _scalar(raw)
                if value:
                    values.append((_text(field), value))
        unique_values = list(dict.fromkeys(value for _, value in values))
        if len(unique_values) == 1:
            bindings[param] = unique_values[0]
            for field, value in values:
                if value == unique_values[0]:
                    bindings.setdefault(field, value)
        elif wanted in generic_params:
            generic_values: list[tuple[str, str]] = []
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                for field, raw in entity.items():
                    if _field_key(field) not in generic_params:
                        continue
                    value = _scalar(raw)
                    if value:
                        generic_values.append((_text(field), value))
            unique_generic = list(
                dict.fromkeys(value for _, value in generic_values)
            )
            if len(unique_generic) == 1:
                bindings[param] = unique_generic[0]
                for field, value in generic_values:
                    if value == unique_generic[0]:
                        bindings.setdefault(field, value)
            else:
                unresolved.append(param)
        else:
            unresolved.append(param)

    unresolved_explicit = [
        param
        for param in unresolved
        if _field_key(param) not in generic_params
    ]
    if len(unresolved_explicit) == 1:
        generic_values: list[tuple[str, str]] = []
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            for field, raw in entity.items():
                if _field_key(field) not in generic_params:
                    continue
                value = _scalar(raw)
                if value:
                    generic_values.append((_text(field), value))
        unique_generic = list(
            dict.fromkeys(value for _, value in generic_values)
        )
        if len(unique_generic) == 1:
            target = unresolved_explicit[0]
            bindings[target] = unique_generic[0]
            for field, value in generic_values:
                if value == unique_generic[0]:
                    bindings.setdefault(field, value)
    return bindings


def bind_entity_fields(body: Any, path: str = "") -> dict[str, str]:
    raw_path = _text(path)
    marker = _STATE_PATH_RE.match(raw_path)
    if not marker:
        return _strict_multi_identity_bindings(body, raw_path)

    required_state = _text(marker.group(1))
    resolved_path = _text(marker.group(2))
    selected = _entity_in_required_state(body, required_state)
    if not selected:
        return {}
    return _strict_multi_identity_bindings(selected, resolved_path)


_base.param_field_candidates = param_field_candidates
_base.body_field_collection_paths = body_field_collection_paths
_base.alternate_collection_paths = alternate_collection_paths
_base.bind_entity_fields = bind_entity_fields

__all__ = sorted(
    {
        *[
            name
            for name in dir(_base)
            if not name.startswith("__")
        ],
        "param_field_candidates",
        "body_field_collection_paths",
        "alternate_collection_paths",
        "bind_entity_fields",
        "_strict_multi_identity_bindings",
    }
)
