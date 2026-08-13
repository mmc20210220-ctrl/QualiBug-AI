"""Knowledge parsing facade with OpenAPI security declaration provenance."""
from __future__ import annotations
from typing import Any

from . import _parsing_security_base as _base
from ..openapi_security_authority import stamp_openapi_operation_security

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_original_openapi_operations = _base._core._openapi_operations


def _openapi_operations(openapi: dict[str, Any], source_id: str = "") -> list[dict[str, Any]]:
    rows = [dict(row) for row in _original_openapi_operations(openapi, source_id)]
    return stamp_openapi_operation_security(rows, openapi)


# The runtime-contract installer wraps this facade function, while mechanics
# call sites resolve the same governed function from their defining globals.
_base._core._openapi_operations = _openapi_operations


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))


__all__ = sorted({
    *[name for name in dir(_base) if not name.startswith("__")],
    "_openapi_operations",
})
