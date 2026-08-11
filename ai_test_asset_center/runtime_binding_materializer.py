"""Effect-aware runtime binding facade.

The stable binding implementation remains unchanged. This layer strengthens
cleanup target resolution so an HTTP-rejected write that demonstrably changed
business state is cleaned with the same source-declared compensation route as a
2xx write.

Cleanup identity is safety-critical: a ``{id}`` placeholder may not be satisfied
by whichever response field happens to end in ``id``.  Runtime cleanup now uses
only the concrete request path or the exact declared placeholder identity
(top-level / standard response envelope semantics shared with the cleanup
adapter contract).  Ambiguous or differently-named identities remain unbound.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, unquote

from . import runtime_binding_materializer_base as _base
from .runtime_binding_materializer_base import *  # noqa: F401,F403
from .cleanup_adapter_ladder import identity_value_from_body


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


def _declared_placeholder_identity(body: Any, placeholder: str) -> str:
    """Resolve one cleanup placeholder without cross-field guessing.

    ``identity_value_from_body`` accepts the exact declared key.  Only when the
    placeholder itself is a generic primary-key name (id/uuid/guid/key) does
    that shared helper allow the other generic aliases, and only at the body
    root or a standard response envelope.  Nested related-object IDs and domain
    ``*Id`` suffixes are never substitutes.
    """

    return identity_value_from_body(body, placeholder)


def runtime_cleanup_paths(
    path_template: str,
    steps: list[dict[str, Any]],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Resolve one compensation target per proven effectful write.

    A candidate is either HTTP-accepted or has auditable before/after snapshots
    proving business-state change.  Each path placeholder must then resolve from
    the concrete request path or its own declared identity field; inability to
    prove that identity is a visible cleanup binding gap, never permission to
    choose another ID-shaped field.
    """

    template = _text(path_template)
    placeholders = _PATH_PARAMETER_RE.findall(template)
    effectful_steps = [step for step in steps if _cleanup_candidate(step)]
    if not placeholders:
        return ([(template, {})] if effectful_steps else []), []
    if not effectful_steps:
        return [], ["effectful_write_receipt"]

    paths: list[tuple[str, dict[str, Any]]] = []
    missing: list[str] = []
    seen_paths: set[str] = set()
    for index, step in enumerate(effectful_steps):
        governance = _dict(step.get("governance_receipt"))
        before_body = _dict(governance.get("before")).get("body")
        after_body = _dict(governance.get("after")).get("body")
        concrete_path_bindings = _path_bindings_from_concrete(
            template,
            _text(step.get("path")),
        )
        bindings: dict[str, Any] = {}
        step_missing: list[str] = []
        for name in placeholders:
            direct = concrete_path_bindings.get(name)
            candidates: list[Any] = (
                [direct] if direct not in (None, "") else []
            )

            if not candidates:
                response_value = _declared_placeholder_identity(
                    step.get("body"), name
                )
                if response_value:
                    candidates = [response_value]

            if not candidates:
                after_value = _declared_placeholder_identity(after_body, name)
                before_value = _declared_placeholder_identity(before_body, name)
                # An after-only or changed exact identity is useful evidence.
                # A stable exact identity is also acceptable because the field
                # itself is the source-declared cleanup placeholder; unlike the
                # removed suffix fallback, no cross-field substitution occurs.
                if after_value and (
                    not before_value
                    or after_value != before_value
                    or after_value == before_value
                ):
                    candidates = [after_value]

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
