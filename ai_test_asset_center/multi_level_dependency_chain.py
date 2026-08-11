"""Outermost multi-level dependency cleanup-authority facade.

Reference/create/actor authority lives in
``_multi_level_dependency_chain_authority_mechanics``. This final boundary makes
recursive cleanup consume the same unique CleanupOperationAuthority used by runtime
fixture binding. The recursive mechanics may keep indexing its cleanup list because
this facade guarantees that list contains zero or exactly one authoritative route.
"""
from __future__ import annotations

from typing import Any

from . import _multi_level_dependency_chain_authority_mechanics as _authority
from .cleanup_operation_authority import resolve_cleanup_operation

for _name in dir(_authority):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_authority, _name)


def __getattr__(name: str) -> Any:
    return getattr(_authority, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_authority)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _cleanup_operations(
    create_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return zero or exactly one cleanup operation for one create identity."""

    receipt = resolve_cleanup_operation(
        _dict(create_operation),
        behavior_ir=behavior_ir,
    )
    if _text(receipt.get("status")) != "RESOLVED":
        return []
    cleanup = _dict(receipt.get("cleanup_operation"))
    return [cleanup] if cleanup else []


# The original recursive planner resolves this helper from its defining-module
# globals. Patching the underlying mechanics makes every depth obey the same rule.
_authority._core._cleanup_operations = _cleanup_operations
_authority._core._declared_cleanup_operations = getattr(
    __import__(
        "ai_test_asset_center.runtime_binding_graph",
        fromlist=["_declared_cleanup_operations"],
    ),
    "_declared_cleanup_operations",
)

plan_multi_level_dependency_chain = _authority.plan_multi_level_dependency_chain

__all__ = sorted(
    {
        *[
            name
            for name in dir(_authority)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "_cleanup_operations",
        "plan_multi_level_dependency_chain",
    }
)
