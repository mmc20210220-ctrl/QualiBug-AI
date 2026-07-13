"""Effect-aware runtime binding facade.

The stable binding implementation remains unchanged. This layer strengthens
cleanup target resolution so an HTTP-rejected write that demonstrably changed
business state is cleaned with the same source-declared compensation route as a
2xx write.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, unquote

from . import runtime_binding_materializer_base as _base
from .runtime_binding_materializer_base import *  # noqa: F401,F403


_PATH_PARAMETER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _http_status(value: Any) -> int:
    row = _dict(value)
    try:
        return int(row.get("status") or row.get("status_code") or 0)
    except (TypeError, ValueError):
        return 0


def _server_managed_field(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", _text(value).lower())
    return normalized in {
        "createdat",
        "updatedat",
        "createdtime",
        "updatedtime",
        "modifiedat",
        "modifiedtime",
        "timestamp",
    }


def _without_server_managed_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_server_managed_fields(child)
            for key, child in sorted(value.items())
            if not _server_managed_field(key)
        }
    if isinstance(value, list):
        return sorted(
            (_without_server_managed_fields(child) for child in value),
            key=lambda child: json.dumps(
                child,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
    return value


def _snapshot_state_changed(step: dict[str, Any]) -> bool:
    governance = _dict(step.get("governance_receipt"))
    before = _dict(governance.get("before"))
    after = _dict(governance.get("after"))
    if not (
        200 <= _http_status(before) < 300
        and 200 <= _http_status(after) < 300
    ):
        return False
    before_body = before.get("body")
    after_body = after.get("body")
    if not isinstance(before_body, (dict, list)) or not isinstance(
        after_body,
        (dict, list),
    ):
        return False
    return _without_server_managed_fields(
        before_body
    ) != _without_server_managed_fields(after_body)


def _cleanup_candidate(step: Any) -> bool:
    if not isinstance(step, dict):
        return False
    if _text(step.get("phase")) not in {"control", "treatment"}:
        return False
    if _text(step.get("method")).upper() not in _WRITE_METHODS:
        return False
    status = _http_status(step)
    return 200 <= status < 300 or _snapshot_state_changed(step)


def _path_bindings_from_concrete(
    path_template: str,
    concrete_path: str,
) -> dict[str, str]:
    template = _base.normalize_path_placeholders(
        _text(path_template)
    ).split("?", 1)[0]
    concrete = _text(concrete_path).split("?", 1)[0]
    names = _PATH_PARAMETER_RE.findall(template)
    if not names or not concrete.startswith("/"):
        return {}
    pattern_parts: list[str] = []
    cursor = 0
    for match in _PATH_PARAMETER_RE.finditer(template):
        pattern_parts.append(re.escape(template[cursor:match.start()]))
        pattern_parts.append("([^/]+)")
        cursor = match.end()
    pattern_parts.append(re.escape(template[cursor:]))
    matched = re.fullmatch("".join(pattern_parts), concrete)
    if not matched:
        return {}
    return {
        name: unquote(value)
        for name, value in zip(names, matched.groups())
        if _text(value)
    }


def runtime_cleanup_paths(
    path_template: str,
    steps: list[dict[str, Any]],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Resolve one compensation target per proven effectful write.

    A candidate is either HTTP-accepted or has auditable before/after snapshots
    proving business-state change. This prevents a 4xx response with a real
    write side effect from escaping cleanup.
    """

    template = _text(path_template)
    placeholders = _PATH_PARAMETER_RE.findall(template)
    effectful_steps = [
        step for step in steps if _cleanup_candidate(step)
    ]
    if not placeholders:
        return ([(template, {})] if effectful_steps else []), []
    if not effectful_steps:
        return [], ["effectful_write_receipt"]

    paths: list[tuple[str, dict[str, Any]]] = []
    missing: list[str] = []
    seen_paths: set[str] = set()
    for index, step in enumerate(effectful_steps):
        fields = _base._response_scalar_fields(step.get("body"))
        governance = _dict(step.get("governance_receipt"))
        before_fields = _base._response_scalar_fields(
            _dict(governance.get("before")).get("body")
        )
        after_fields = _base._response_scalar_fields(
            _dict(governance.get("after")).get("body")
        )
        concrete_path_bindings = _path_bindings_from_concrete(
            template,
            _text(step.get("path")),
        )
        bindings: dict[str, Any] = {}
        step_missing: list[str] = []
        for name in placeholders:
            normalized = _base._field_key(name)
            direct = concrete_path_bindings.get(name)
            candidates = [direct] if direct not in (None, "") else []
            if not candidates:
                candidates = list(
                    dict.fromkeys(fields.get(normalized) or [])
                )
            if not candidates and normalized == "id":
                candidates = list(
                    dict.fromkeys(
                        value
                        for key, values in fields.items()
                        if key.endswith("id")
                        for value in values
                    )
                )
            if not candidates:
                observed_after = list(
                    dict.fromkeys(after_fields.get(normalized) or [])
                )
                observed_before = set(
                    before_fields.get(normalized) or []
                )
                candidates = [
                    value
                    for value in observed_after
                    if value not in observed_before
                ]
            if not candidates and normalized == "id":
                observed_after = list(
                    dict.fromkeys(
                        value
                        for key, values in after_fields.items()
                        if key.endswith("id")
                        for value in values
                    )
                )
                observed_before = {
                    value
                    for key, values in before_fields.items()
                    if key.endswith("id")
                    for value in values
                }
                candidates = [
                    value
                    for value in observed_after
                    if value not in observed_before
                ]
            if not candidates:
                stable_after = list(
                    dict.fromkeys(after_fields.get(normalized) or [])
                )
                if len(stable_after) == 1:
                    candidates = stable_after
            if not candidates and normalized == "id":
                stable_ids = list(
                    dict.fromkeys(
                        value
                        for key, values in after_fields.items()
                        if key.endswith("id")
                        for value in values
                    )
                )
                if len(stable_ids) == 1:
                    candidates = stable_ids
            candidates = list(
                dict.fromkeys(
                    value
                    for value in candidates
                    if value not in (None, "")
                )
            )
            if len(candidates) != 1:
                step_missing.append(name)
                continue
            bindings[name] = candidates[0]
        if step_missing:
            step_ref = _text(step.get("step_id")) or str(index)
            missing.extend(
                f"{step_ref}:{name}" for name in step_missing
            )
            continue
        materialized = template
        for name, value in bindings.items():
            materialized = materialized.replace(
                "{" + name + "}",
                quote(str(value), safe=""),
            )
        if materialized in seen_paths:
            continue
        seen_paths.add(materialized)
        paths.append((materialized, bindings))
    return paths, missing
