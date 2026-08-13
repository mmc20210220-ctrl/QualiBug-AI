"""Public schema-seed authority with shared OpenAPI access provenance."""
from __future__ import annotations
from typing import Any

from . import schema_validation_seed_authority_mainline_base as _base
from .openapi_security_authority import operation_has_source_declared_anonymous_access

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_original_anonymous_execution = _base._operation_declares_anonymous_execution


def _operation_declares_anonymous_execution(operation: dict[str, Any]) -> bool:
    return bool(
        operation_has_source_declared_anonymous_access(operation)
        or _original_anonymous_execution(operation)
    )


_base._operation_declares_anonymous_execution = _operation_declares_anonymous_execution
append_operation_schema_validation_seeds = _base.append_operation_schema_validation_seeds


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))


__all__ = ["append_operation_schema_validation_seeds"]
