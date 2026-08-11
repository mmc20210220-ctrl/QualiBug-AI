"""Cleanup adapter ladder facade with strict destructive identity authority.

All existing adapter selection / DB cleanup mechanics remain in
``_cleanup_adapter_ladder_mechanics``.  The only semantic override here is
resource identity extraction: a destructive cleanup may bind the declared
identity column, or a generic primary key at the response root / standard
resource envelope.  IDs inside arbitrary related nested objects are never the
resource identity merely because they are unique.
"""
from __future__ import annotations

from typing import Any

from . import _cleanup_adapter_ladder_mechanics as _core
from ._cleanup_adapter_ladder_mechanics import *  # noqa: F401,F403
from .cleanup_identity_authority import strict_observed_resource_identity


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def observed_resource_identity(body: Any, identity_column: str = "") -> str:
    return strict_observed_resource_identity(
        body,
        identity_column=identity_column,
    )


# Mechanics functions resolve this callable from their defining-module globals,
# so mirror the governed authority there as well as on the public facade.
_core.observed_resource_identity = observed_resource_identity

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "observed_resource_identity",
        "strict_observed_resource_identity",
    }
)
