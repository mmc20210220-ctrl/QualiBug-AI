"""Public actor-exploration facade with source-truthful anonymous access."""
from __future__ import annotations
from typing import Any

from . import actor_exploration_runtime_mainline_base as _base
from .openapi_security_authority import operation_has_source_declared_anonymous_access

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)


def build_executable_candidates(
    actors: dict[str, dict[str, Any]],
    *,
    operation: dict[str, Any] | None = None,
    obligation: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    permission_observations: list[Any] | None = None,
    permitted_actor_ids: set[str] | None = None,
):
    governed_operation = operation
    if isinstance(operation, dict):
        governed_operation = dict(operation)
        governed_operation["security"] = (
            [] if operation_has_source_declared_anonymous_access(operation) else None
        )
    return _base.build_executable_candidates(
        actors,
        operation=governed_operation,
        obligation=obligation,
        runtime_context=runtime_context,
        permission_observations=permission_observations,
        permitted_actor_ids=permitted_actor_ids,
    )


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
