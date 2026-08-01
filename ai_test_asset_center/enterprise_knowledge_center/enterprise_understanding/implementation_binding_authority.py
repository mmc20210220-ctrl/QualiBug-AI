"""Single relationship-authority facade for implementation binding.

The mature binder remains the sole implementation. This module replaces only its
relationship admission predicate with the shared source-backed relationship authority
used by linking, Probe compilation, and identity projection.
"""
from __future__ import annotations

from typing import Any

from .._linking import _relationship_is_authoritative
from . import implementation_binding as _impl

# Existing binder helpers resolve this global at call time, so one assignment closes
# direct package exports and governed calls without copying or wrapping the algorithm.
_impl._authoritative_relationship = _relationship_is_authoritative

IMPLEMENTATION_BINDING_SCHEMA = _impl.IMPLEMENTATION_BINDING_SCHEMA
IMPLEMENTATION_BINDING_GATE_SCHEMA = _impl.IMPLEMENTATION_BINDING_GATE_SCHEMA
build_behavior_implementation_bindings = _impl.build_behavior_implementation_bindings


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))


__all__ = [
    "IMPLEMENTATION_BINDING_SCHEMA",
    "IMPLEMENTATION_BINDING_GATE_SCHEMA",
    "build_behavior_implementation_bindings",
]
