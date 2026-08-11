"""Strict cleanup identity helper used at destructive execution boundaries."""
from __future__ import annotations

from typing import Any

from .cleanup_identity_authority import strict_observed_resource_identity


def identity_from_governed_write(
    cleanup: dict[str, Any],
    governed: dict[str, Any],
    *,
    step_body: Any = None,
) -> str:
    """Resolve only run-observed identity for the cleanup contract's row."""

    identity_column = str(cleanup.get("identity_column") or "id").strip() or "id"
    tracked = str(governed.get("observed_created_identity") or "").strip()
    if tracked:
        return tracked
    for raw in (
        (governed.get("response_bound_after") or {}).get("body")
        if isinstance(governed.get("response_bound_after"), dict)
        else None,
        (governed.get("write") or {}).get("body")
        if isinstance(governed.get("write"), dict)
        else None,
        (governed.get("after") or {}).get("body")
        if isinstance(governed.get("after"), dict)
        else None,
        (governed.get("before") or {}).get("body")
        if isinstance(governed.get("before"), dict)
        else None,
        step_body,
    ):
        identity = strict_observed_resource_identity(
            raw,
            identity_column=identity_column,
        )
        if identity:
            return identity
    return ""


__all__ = ["identity_from_governed_write"]
