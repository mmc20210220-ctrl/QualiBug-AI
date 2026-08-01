"""Composition facade for capability-aware Feishu synchronization.

The implementation remains in ``feishu_connector_capability_sync_core``.  This facade binds its
snapshot and remote-lifecycle steps to the recoverable commit authorities without duplicating
discovery, capability classification, export, ingestion, or coverage logic.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from typing import Any, Iterator

from . import feishu_connector_capability_sync_core as _core
from .connector_checkpoint_commit_authority import (
    reconcile_connector_remote_lifecycle_with_checkpoint,
    sync_connector_snapshot_batch_deferred,
)

# Preserve the existing public and test injection surface while keeping one implementation body.
for _name, _value in vars(_core).items():
    if not _name.startswith("__"):
        globals().setdefault(_name, _value)

_DEFAULT_SYNC_SNAPSHOT_AUTHORITY = sync_connector_snapshot_batch_deferred
_DEFAULT_REMOTE_LIFECYCLE_AUTHORITY = (
    reconcile_connector_remote_lifecycle_with_checkpoint
)
sync_connector_snapshot_batch = _DEFAULT_SYNC_SNAPSHOT_AUTHORITY
reconcile_connector_remote_lifecycle = _DEFAULT_REMOTE_LIFECYCLE_AUTHORITY

_FACADE_OWNED_NAMES = {
    "_core",
    "_DEFAULT_SYNC_SNAPSHOT_AUTHORITY",
    "_DEFAULT_REMOTE_LIFECYCLE_AUTHORITY",
    "_FACADE_OWNED_NAMES",
    "_temporary_core_overrides",
    "sync_feishu_connector",
    "contextmanager",
    "wraps",
    "Any",
    "Iterator",
}


@contextmanager
def _temporary_core_overrides() -> Iterator[None]:
    """Forward facade/test overrides for one call and then restore the core exactly."""
    saved: dict[str, Any] = {}
    try:
        for name, value in list(globals().items()):
            if name in _FACADE_OWNED_NAMES or name.startswith("__"):
                continue
            if name in vars(_core):
                saved.setdefault(name, getattr(_core, name))
                setattr(_core, name, value)
        for name, fallback in (
            ("sync_connector_snapshot_batch", _DEFAULT_SYNC_SNAPSHOT_AUTHORITY),
            (
                "reconcile_connector_remote_lifecycle",
                _DEFAULT_REMOTE_LIFECYCLE_AUTHORITY,
            ),
        ):
            saved.setdefault(name, getattr(_core, name))
            setattr(_core, name, globals().get(name, fallback))
        yield
    finally:
        for name, value in saved.items():
            setattr(_core, name, value)


@wraps(_core.sync_feishu_connector)
def sync_feishu_connector(*args: Any, **kwargs: Any) -> dict[str, Any]:
    with _temporary_core_overrides():
        return _core.sync_feishu_connector(*args, **kwargs)


__all__ = sorted(
    {
        *list(getattr(_core, "__all__", []) or []),
        "FEISHU_MATERIALIZATION_CAPABILITY_VERSION",
        "classify_feishu_resource",
        "sync_feishu_connector",
    }
)
