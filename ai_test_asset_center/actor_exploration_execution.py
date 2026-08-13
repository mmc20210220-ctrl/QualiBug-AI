"""Public exploration-execution facade with proven anonymous-write authority."""
from __future__ import annotations
from typing import Any

from . import actor_exploration_execution_mainline_base as _base
from .openapi_security_authority import operation_has_source_declared_anonymous_access

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)


def exploration_execution_policy(
    *,
    operation: dict[str, Any],
    experiment: dict[str, Any],
    requested_max_attempts: int,
) -> tuple[bool, int, str]:
    governed_operation = dict(operation or {})
    governed_operation["security"] = (
        [] if operation_has_source_declared_anonymous_access(operation) else None
    )
    return _base.exploration_execution_policy(
        operation=governed_operation,
        experiment=experiment,
        requested_max_attempts=requested_max_attempts,
    )


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
