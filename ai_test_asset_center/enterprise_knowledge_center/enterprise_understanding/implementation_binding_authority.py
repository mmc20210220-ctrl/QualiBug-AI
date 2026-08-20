"""Single source-backed authority facade for implementation binding.

The mature binder remains the sole implementation. This module centralizes the
relationship admission predicate and one conservative authority refinement: when a
rule authoritatively covers multiple interfaces but the confirmed Business Behavior
has one exact operation identity that is inside that authoritative set, the behavior
binding is narrowed to that one interface. Conflicting or non-exact identities are
never auto-resolved.
"""
from __future__ import annotations

from typing import Any

from .._linking import _relationship_is_authoritative
from . import implementation_binding as _impl

# Existing binder helpers resolve this global at call time, so one assignment closes
# direct package exports and governed calls without copying the mature algorithm.
_impl._authoritative_relationship = _relationship_is_authoritative

# Preserve the mature action binder once. Package import happens before governed
# submodules are loaded, so the governed pipeline sees the same refined authority.
_mature_bind_action = getattr(_impl, "_source_backed_mature_bind_action", _impl._bind_action)
_impl._source_backed_mature_bind_action = _mature_bind_action


def _bind_action_with_congruent_exact_narrowing(
    asset: dict[str, Any],
    behavior: dict[str, Any],
    interfaces: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Narrow broad rule authority only with one congruent exact behavior operation.

    Rule-level source authority may legitimately mention several interfaces. A formal
    Business Behavior, however, carries one operation_ref; if that exact operation
    resolves to one interface already inside the authoritative rule set, retaining all
    rule interfaces creates artificial MULTIPLE_AUTHORITATIVE ambiguity. This wrapper
    removes only that artificial broadening. Existing conflicts, unresolved exact
    identities, and genuinely multi-exact cases remain fail-closed.
    """
    bindings, unknowns, conflicts = _mature_bind_action(asset, behavior, interfaces)
    if conflicts:
        return bindings, unknowns, conflicts

    authoritative_ids = _impl._authoritative_interface_ids(asset, behavior)
    if len(authoritative_ids) <= 1:
        return bindings, unknowns, conflicts

    exact = _impl._exact_operation_interfaces(behavior, interfaces)
    if len(exact) != 1:
        return bindings, unknowns, conflicts
    exact_id = _impl.text(exact[0].get("interface_id"))
    if not exact_id or exact_id not in authoritative_ids:
        return bindings, unknowns, conflicts

    authoritative_bound = [
        row
        for row in bindings
        if bool(row.get("authoritative")) and _impl.text(row.get("status")) == "BOUND"
    ]
    if len(authoritative_bound) <= 1:
        return bindings, unknowns, conflicts

    narrowed = [
        row
        for row in bindings
        if not (
            bool(row.get("authoritative"))
            and _impl.text(row.get("status")) == "BOUND"
            and _impl.text(row.get("interface_id")) != exact_id
        )
    ]
    return narrowed, unknowns, conflicts


_impl._bind_action = _bind_action_with_congruent_exact_narrowing

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
