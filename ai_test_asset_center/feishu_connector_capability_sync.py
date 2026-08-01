"""Public composition facade for capability-aware Feishu synchronization.

The implementation body remains in ``feishu_connector_capability_sync_core``. Production calls
use permanent ContextVar dispatchers for discovery, snapshot commit and lifecycle reconciliation,
so concurrent connector instances never rewrite shared module globals. Explicit test dependency
overrides retain a narrow compatibility bridge without creating a second implementation.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from functools import wraps
from typing import Any, Iterator

from . import feishu_connector_capability_sync_core as _core
from .connector_checkpoint_commit_authority import (
    ConnectorCheckpointCommitError,
    reconcile_connector_remote_lifecycle_with_checkpoint,
    sync_connector_snapshot_batch_deferred,
)
from .connector_lifecycle_commit_authority import ConnectorLifecycleCommitError
from .connector_lifecycle_recovery_intent import (
    ConnectorLifecycleRecoveryIntentError,
)
from .feishu_lifecycle_recovery_runtime import (
    FeishuLifecycleRecoveryRuntimeError,
    discover_feishu_resources_with_recovery_intent,
    feishu_lifecycle_recovery_scope,
    reconcile_feishu_lifecycle_with_recovery_intent,
    sync_feishu_snapshot_with_recovery_intent,
)

_CORE_BASELINE = {
    name: value
    for name, value in vars(_core).items()
    if not name.startswith("__")
}
for _name, _value in _CORE_BASELINE.items():
    globals().setdefault(_name, _value)

_DEFAULT_DISCOVERY_AUTHORITY = _CORE_BASELINE["discover_feishu_wiki_resources"]
_DEFAULT_SYNC_SNAPSHOT_AUTHORITY = sync_connector_snapshot_batch_deferred
_DEFAULT_REMOTE_LIFECYCLE_AUTHORITY = (
    reconcile_connector_remote_lifecycle_with_checkpoint
)
discover_feishu_wiki_resources = _DEFAULT_DISCOVERY_AUTHORITY
sync_connector_snapshot_batch = _DEFAULT_SYNC_SNAPSHOT_AUTHORITY
reconcile_connector_remote_lifecycle = _DEFAULT_REMOTE_LIFECYCLE_AUTHORITY

# These three hooks are permanently context-dispatched. Per-call delegates live in ContextVar.
_core.discover_feishu_wiki_resources = (
    discover_feishu_resources_with_recovery_intent
)
_core.sync_connector_snapshot_batch = sync_feishu_snapshot_with_recovery_intent
_core.reconcile_connector_remote_lifecycle = (
    reconcile_feishu_lifecycle_with_recovery_intent
)

_OVERRIDE_LOCK = threading.RLock()
_MANAGED_HOOKS = {
    "discover_feishu_wiki_resources",
    "sync_connector_snapshot_batch",
    "reconcile_connector_remote_lifecycle",
}
_FACADE_OWNED_NAMES = {
    "_core",
    "_CORE_BASELINE",
    "_DEFAULT_DISCOVERY_AUTHORITY",
    "_DEFAULT_SYNC_SNAPSHOT_AUTHORITY",
    "_DEFAULT_REMOTE_LIFECYCLE_AUTHORITY",
    "_OVERRIDE_LOCK",
    "_MANAGED_HOOKS",
    "_FACADE_OWNED_NAMES",
    "_temporary_explicit_core_overrides",
    "_project_committed_checkpoint",
    "sync_feishu_connector",
    "threading",
    "contextmanager",
    "wraps",
    "Any",
    "Iterator",
    "ConnectorCheckpointCommitError",
    "ConnectorLifecycleCommitError",
    "ConnectorLifecycleRecoveryIntentError",
    "FeishuLifecycleRecoveryRuntimeError",
    "discover_feishu_resources_with_recovery_intent",
    "feishu_lifecycle_recovery_scope",
    "reconcile_feishu_lifecycle_with_recovery_intent",
    "sync_feishu_snapshot_with_recovery_intent",
}


def _explicit_core_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for name, baseline in _CORE_BASELINE.items():
        if name in _MANAGED_HOOKS or name in _FACADE_OWNED_NAMES:
            continue
        current = globals().get(name, baseline)
        if current is not baseline:
            overrides[name] = current
    return overrides


@contextmanager
def _temporary_explicit_core_overrides() -> Iterator[None]:
    """Compatibility bridge for explicit tests; production normally has no overrides."""
    overrides = _explicit_core_overrides()
    if not overrides:
        yield
        return
    with _OVERRIDE_LOCK:
        saved = {name: getattr(_core, name) for name in overrides}
        try:
            for name, value in overrides.items():
                setattr(_core, name, value)
            yield
        finally:
            for name, value in saved.items():
                setattr(_core, name, value)


def _project_committed_checkpoint(result: dict[str, Any]) -> dict[str, Any]:
    lifecycle = dict(result.get("remote_lifecycle") or {})
    if lifecycle.get("cursor_checkpoint_committed") is not True:
        return result
    projected = dict(result)
    projected.update(
        {
            "cursor_checkpoint_committed": True,
            "cursor_checkpoint_pending_lifecycle_commit": False,
            "pending_cursor_fingerprint": "",
            "previous_cursor_checkpoint_preserved": False,
            "checkpoint_committed_after_lifecycle_decision": True,
            "checkpoint_response_projection": (
                "COMMITTED_WITH_CURSOR_FINGERPRINT_REDACTED"
            ),
        }
    )
    return projected


@wraps(_core.sync_feishu_connector)
def sync_feishu_connector(*args: Any, **kwargs: Any) -> dict[str, Any]:
    project_id = args[0] if args else kwargs.get("project_id")
    connector = kwargs.get("connector_instance_id")
    grace_value = kwargs.get("retire_after_complete_snapshots")
    count_value = kwargs.get("max_retire_count")
    ratio_value = kwargs.get("max_retire_ratio")
    try:
        with feishu_lifecycle_recovery_scope(
            str(project_id or ""),
            str(connector or ""),
            root=kwargs.get("root"),
            actor=kwargs.get("actor"),
            deletion_policy=str(kwargs.get("deletion_policy") or "RETAIN"),
            retire_after_complete_snapshots=int(
                2 if grace_value is None else grace_value
            ),
            max_retire_count=int(100 if count_value is None else count_value),
            max_retire_ratio=float(0.25 if ratio_value is None else ratio_value),
            discovery_delegate=globals().get(
                "discover_feishu_wiki_resources",
                _DEFAULT_DISCOVERY_AUTHORITY,
            ),
            snapshot_delegate=globals().get(
                "sync_connector_snapshot_batch",
                _DEFAULT_SYNC_SNAPSHOT_AUTHORITY,
            ),
            lifecycle_delegate=globals().get(
                "reconcile_connector_remote_lifecycle",
                _DEFAULT_REMOTE_LIFECYCLE_AUTHORITY,
            ),
            classifier=globals().get(
                "classify_feishu_resource",
                _CORE_BASELINE["classify_feishu_resource"],
            ),
            lifecycle_resource_builder=_CORE_BASELINE["_lifecycle_resource"],
            snapshot_cursor_builder=globals().get(
                "_snapshot_cursor",
                _CORE_BASELINE["_snapshot_cursor"],
            ),
        ):
            with _temporary_explicit_core_overrides():
                result = _core.sync_feishu_connector(*args, **kwargs)
    except (
        ConnectorCheckpointCommitError,
        ConnectorLifecycleCommitError,
        ConnectorLifecycleRecoveryIntentError,
        FeishuLifecycleRecoveryRuntimeError,
    ) as exc:
        raise FeishuConnectorError(
            f"feishu_lifecycle_checkpoint_commit_failed:{exc}"
        ) from exc
    return _project_committed_checkpoint(result)


__all__ = sorted(
    {
        *list(getattr(_core, "__all__", []) or []),
        "FEISHU_MATERIALIZATION_CAPABILITY_VERSION",
        "classify_feishu_resource",
        "sync_feishu_connector",
    }
)
