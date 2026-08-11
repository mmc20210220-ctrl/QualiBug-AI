"""State-aware facade for real runtime identity resolution.

The stable resolver remains in ``real_id_resolver_base``. This facade keeps the
state-selection behavior and adds one destructive ambiguity boundary: for a
path with multiple identity placeholders, one generic response ``id`` may not
be mirrored into several different identities. Exact snake/camel-equivalent
fields are resolved first; a generic id may fill at most one remaining identity
only after every other placeholder is already proven exactly.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import real_id_resolver_base as _base
from .real_id_resolver_base import *  # noqa: F401,F403


_STATE_PATH_RE = re.compile(r"^@state=([a-z0-9]+)@(.*)$")
_original_bind_entity_fields = _base.bind_entity_fields


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _state_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).casefold())


def _entity_state_values(entity: dict[str, Any]) -> list[Any]:
    exact = {
        "state",
        "status",
        "stage",
        "lifecycle",
        "lifecyclestatus",
        "orderstatus",
        "paymentstatus",
        "refundstatus",
        "shipmentstatus",
        "fulfillmentstatus",
    }
    values: list[Any] = []
    for key, value in entity.items():
        normalized = _field_key(key)
        if isinstance(value, (dict, list, bool)) or value in (None, ""):
            continue
        if (
            normalized in exact
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
            _field_key(key) in {"id", "uuid", "key"}
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
    generic_params = {"id", "uuid", "guid", "pk", "key"}

    # Phase 1: exact semantic field identity only. Normalization deliberately
    # treats order_id and orderId as the same declared name, but never collapses
    # addressId onto id or userId.
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

    # Phase 2: one generic resource id may close exactly ONE remaining explicit
    # identity. This is elimination, not mirroring: all sibling dimensions have
    # already been proven by exact fields. If two identities remain unknown,
    # generic id cannot tell which resource it names and neither is bound.
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


# Public call sites import this facade. Also update the base module's dynamic
# attribute for callers that resolve it after facade initialization; the saved
# original remains available internally to avoid recursion on single-id paths.
_base.bind_entity_fields = bind_entity_fields

__all__ = sorted(
    {
        *[
            name
            for name in dir(_base)
            if not name.startswith("__")
        ],
        "bind_entity_fields",
        "_strict_multi_identity_bindings",
    }
)
