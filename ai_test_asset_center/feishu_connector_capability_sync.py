"""Composition facade for capability-aware Feishu synchronization.

The implementation remains in ``feishu_connector_capability_sync_core``.  This facade binds its
remote-lifecycle step to the recoverable cross-authority commit protocol without duplicating
discovery, capability classification, export, ingestion, or coverage logic.
"""
from __future__ import annotations

from functools import wraps
from typing import Any

from . import feishu_connector_capability_sync_core as _core
from .connector_lifecycle_commit_authority import (
    reconcile_connector_remote_lifecycle_atomic,
)

# Preserve the existing public and test injection surface while keeping one implementation body.
for _name, _value in vars(_core).items():
    if not _name.startswith("__"):
        globals().setdefault(_name, _value)

_DEFAULT_REMOTE_LIFECYCLE_AUTHORITY = reconcile_connector_remote_lifecycle_atomic
reconcile_connector_remote_lifecycle = _DEFAULT_REMOTE_LIFECYCLE_AUTHORITY

_FACADE_OWNED_NAMES = {
    "_core",
    "_DEFAULT_REMOTE_LIFECYCLE_AUTHORITY",
    "_FACADE_OWNED_NAMES",
    "_propagate_overrides",
    "sync_feishu_connector",
    "wraps",
    "Any",
}


def _propagate_overrides() -> None:
    """Forward explicit dependency overrides to the single implementation module."""
    for name, value in list(globals().items()):
        if name in _FACADE_OWNED_NAMES or name.startswith("__"):
            continue
        if name in vars(_core):
            setattr(_core, name, value)
    # Production defaults to the atomic authority; tests may explicitly replace this facade field.
    _core.reconcile_connector_remote_lifecycle = globals().get(
        "reconcile_connector_remote_lifecycle",
        _DEFAULT_REMOTE_LIFECYCLE_AUTHORITY,
    )


@wraps(_core.sync_feishu_connector)
def sync_feishu_connector(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _propagate_overrides()
    return _core.sync_feishu_connector(*args, **kwargs)


__all__ = sorted(
    {
        *list(getattr(_core, "__all__", []) or []),
        "FEISHU_MATERIALIZATION_CAPABILITY_VERSION",
        "classify_feishu_resource",
        "sync_feishu_connector",
    }
)
