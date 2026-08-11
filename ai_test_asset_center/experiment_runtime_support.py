"""Runtime-support facade with one structural resource-selection authority.

The established transport/preflight/credential mechanics live in
``_experiment_runtime_support_mechanics``.  Runtime binding must not select a
business resource because it has a larger balance, quantity, amount, or because
its current fields make the planned mutation more likely to change something.
Those are testing conveniences, not identity authority.

This facade therefore routes all ordinary entity extraction through the same
domain-neutral resolver used by planning.  Only an explicitly compiled
``@state=...@`` target may filter rows by business state; otherwise response
order and structural identity are preserved.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_runtime_support_mechanics as _core
from ._experiment_runtime_support_mechanics import *  # noqa: F401,F403
from .real_id_resolver import (
    _extract_entity_candidates as _structural_entity_candidates,
    bind_entity_fields as _structural_bind_entity_fields,
)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _runtime_entity_candidates(value: Any) -> list[dict[str, Any]]:
    """Return structurally identified rows in target response order."""

    return [
        dict(row)
        for row in _structural_entity_candidates(value)
        if isinstance(row, dict)
    ]


def _select_runtime_binding(
    body: Any,
    target_path: str,
    *,
    preferred_body: Any = None,
) -> dict[str, str]:
    """Resolve one binding without mutation-convenience candidate ranking.

    ``preferred_body`` remains accepted for API compatibility but is not a
    selection signal.  If the compiler sealed an explicit state-scoped target,
    state is a real precondition authority and may select a matching row.  All
    other bindings delegate directly to structural identity resolution.
    """

    governed_body = body
    governed_path = _text(target_path)
    if governed_path.startswith("@state="):
        from .runtime_binding_materializer_base import (
            _STATE_TARGET_PATH_RE,
            _state_selected_entity,
        )

        match = _STATE_TARGET_PATH_RE.match(governed_path)
        if not match:
            return {}
        required_state = _text(match.group(1)).lower()
        governed_path = _text(match.group(2))
        selected = _state_selected_entity(
            _runtime_entity_candidates(governed_body),
            required_state,
        )
        if not selected:
            return {}
        governed_body = selected

    return _structural_bind_entity_fields(governed_body, governed_path)


# Functions extracted into the mechanics module resolve helpers from that
# module's globals.  Mirror the governed authorities there so fixture/runtime
# callers cannot bypass this facade through an already-defined function.
_core._runtime_entity_candidates = _runtime_entity_candidates
_core._select_runtime_binding = _select_runtime_binding

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_runtime_entity_candidates",
        "_select_runtime_binding",
    }
)
