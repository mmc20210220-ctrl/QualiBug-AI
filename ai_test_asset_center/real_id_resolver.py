"""State-aware facade for real runtime identity resolution.

The stable resolver remains in ``real_id_resolver_base``. This facade teaches
the public ``bind_entity_fields`` entrypoint to honor the state-selection marker
emitted by the state experiment compiler. The main experiment executor calls
this public function directly, so the source-state precondition is now enforced
on the actual execution path rather than only in an auxiliary materializer.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import real_id_resolver_base as _base
from .real_id_resolver_base import *  # noqa: F401,F403


_STATE_PATH_RE = re.compile(r"^@state=([a-z0-9]+)@(.*)$")


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


def bind_entity_fields(body: Any, path: str = "") -> dict[str, str]:
    raw_path = _text(path)
    marker = _STATE_PATH_RE.match(raw_path)
    if not marker:
        return _base.bind_entity_fields(body, raw_path)

    required_state = _text(marker.group(1))
    resolved_path = _text(marker.group(2))
    selected = _entity_in_required_state(body, required_state)
    if not selected:
        return {}
    return _base.bind_entity_fields(selected, resolved_path)
