"""Context-isolated Feishu lifecycle recovery composition.

The Feishu core remains transport and capability focused.  These dispatchers bind one invocation
to a durable lifecycle recovery intent and one pre-generated sync epoch without mutating shared
module globals per request.  ContextVar isolation allows different connector instances to run
concurrently while tests may still inject explicit delegates through the public facade.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .connector_checkpoint_commit_authority import (
    reconcile_connector_remote_lifecycle_with_checkpoint,
    sync_connector_snapshot_batch_deferred,
)
from .connector_lifecycle_recovery_intent import (
    clear_connector_lifecycle_recovery_intent,
    load_connector_lifecycle_recovery_intent,
    stage_connector_lifecycle_recovery_intent,
    update_connector_lifecycle_recovery_intent_state,
)
from .connector_remote_lifecycle import _normalize_present_resources
from .connector_sync_authority import (
    _cursor_hash,
    _instance_by_id,
    _load_connector_registry,
)
from .enterprise_knowledge_center._common import ROOT, _safe_project_id
from .feishu_connector_adapter import discover_feishu_wiki_resources as _default_discovery

_RUNTIME_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "qualibug_feishu_lifecycle_recovery_runtime",
    default=None,
)


class FeishuLifecycleRecoveryRuntimeError(RuntimeError):
    """The per-call Feishu recovery composition is invalid."""


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _resource_digest(resources: list[dict[str, Any]]) -> str:
    normalized = list(_normalize_present_resources(resources).values())
    normalized.sort(key=lambda row: row["remote_resource_id"])
    return hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _pending_epoch(project: str, connector: str, root: Path) -> str:
    registry = _load_connector_registry(project, root)
    instance = _instance_by_id(registry, connector)
    return _text(
        instance.get("pending_lifecycle_sync_epoch_id")
        if isinstance(instance, dict)
        else "",
        160,
    )


def _cleanup_unbound_intent(context: dict[str, Any]) -> None:
    intent = dict(context.get("intent") or {})
    epoch = _text(intent.get("sync_epoch_id"), 160)
    if not epoch or context.get("intent_completed") is True:
        return
    project = context["project_id"]
    connector = context["connector_instance_id"]
    root = context["root"]
    try:
        if _pending_epoch(project, connector, root) == epoch:
            return
    except Exception:
        # Fail closed: an unreadable registry must not cause deletion of recovery evidence.
        return
    try:
        clear_connector_lifecycle_recovery_intent(
            project,
            connector,
            root=root,
            actor=context["actor"],
            expected_sync_epoch_id=epoch,
        )
    except Exception:
        return


@contextmanager
def feishu_lifecycle_recovery_scope(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None,
    actor: dict[str, Any] | None,
    deletion_policy: str,
    retire_after_complete_snapshots: int,
    max_retire_count: int,
    max_retire_ratio: float,
    discovery_delegate: Callable[..., list[dict[str, Any]]],
    snapshot_delegate: Callable[..., dict[str, Any]],
    lifecycle_delegate: Callable[..., dict[str, Any]],
    classifier: Callable[[Mapping[str, Any]], Any],
    lifecycle_resource_builder: Callable[[Mapping[str, Any], Any], dict[str, Any]],
    snapshot_cursor_builder: Callable[[list[dict[str, Any]]], str],
) -> Iterator[dict[str, Any]]:
    if _RUNTIME_CONTEXT.get() is not None:
        raise FeishuLifecycleRecoveryRuntimeError(
            "feishu_lifecycle_recovery_scope_nested"
        )
    resolved_root = (root or ROOT).resolve()
    context = {
        "project_id": _safe_project_id(project_id),
        "connector_instance_id": _text(connector_instance_id, 160),
        "root": resolved_root,
        "actor": dict(actor or {}),
        "deletion_policy": _text(deletion_policy, 40).upper() or "RETAIN",
        "retire_after_complete_snapshots": int(retire_after_complete_snapshots),
        "max_retire_count": int(max_retire_count),
        "max_retire_ratio": float(max_retire_ratio),
        "discovery_delegate": discovery_delegate,
        "snapshot_delegate": snapshot_delegate,
        "lifecycle_delegate": lifecycle_delegate,
        "classifier": classifier,
        "lifecycle_resource_builder": lifecycle_resource_builder,
        "snapshot_cursor_builder": snapshot_cursor_builder,
        "intent": {},
        "intent_completed": False,
    }
    token = _RUNTIME_CONTEXT.set(context)
    try:
        yield context
    finally:
        _cleanup_unbound_intent(context)
        _RUNTIME_CONTEXT.reset(token)


def discover_feishu_resources_with_recovery_intent(
    access_token: str,
    resource_scope: str,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    context = _RUNTIME_CONTEXT.get()
    if context is None:
        return _default_discovery(access_token, resource_scope, **kwargs)
    descriptors = context["discovery_delegate"](
        access_token,
        resource_scope,
        **kwargs,
    )
    if not isinstance(descriptors, list):
        raise FeishuLifecycleRecoveryRuntimeError(
            "feishu_lifecycle_recovery_discovery_result_invalid"
        )
    resources: list[dict[str, Any]] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise FeishuLifecycleRecoveryRuntimeError(
                "feishu_lifecycle_recovery_descriptor_invalid"
            )
        capability = context["classifier"](descriptor)
        resources.append(
            context["lifecycle_resource_builder"](descriptor, capability)
        )
    next_cursor = context["snapshot_cursor_builder"](descriptors)
    intent = stage_connector_lifecycle_recovery_intent(
        context["project_id"],
        context["connector_instance_id"],
        present_resources=resources,
        next_cursor_fingerprint=_cursor_hash(next_cursor),
        root=context["root"],
        actor=context["actor"],
        deletion_policy=context["deletion_policy"],
        retire_after_complete_snapshots=context[
            "retire_after_complete_snapshots"
        ],
        max_retire_count=context["max_retire_count"],
        max_retire_ratio=context["max_retire_ratio"],
    )
    context["intent"] = intent
    return descriptors


def sync_feishu_snapshot_with_recovery_intent(
    project_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    context = _RUNTIME_CONTEXT.get()
    if context is None:
        return sync_connector_snapshot_batch_deferred(project_id, **kwargs)
    intent = dict(context.get("intent") or {})
    epoch = _text(intent.get("sync_epoch_id"), 160)
    if not epoch:
        raise FeishuLifecycleRecoveryRuntimeError(
            "feishu_lifecycle_recovery_intent_not_staged"
        )
    update_connector_lifecycle_recovery_intent_state(
        context["project_id"],
        context["connector_instance_id"],
        state="SNAPSHOT_COMMITTING",
        root=context["root"],
        actor=context["actor"],
        expected_sync_epoch_id=epoch,
    )
    delegated = dict(kwargs)
    delegated["sync_epoch_id"] = epoch
    run = context["snapshot_delegate"](project_id, **delegated)
    if run.get("status") == "COMPLETE":
        update_connector_lifecycle_recovery_intent_state(
            context["project_id"],
            context["connector_instance_id"],
            state="SNAPSHOT_COMMITTED_PENDING_LIFECYCLE",
            root=context["root"],
            actor=context["actor"],
            expected_sync_epoch_id=epoch,
        )
    return run


def reconcile_feishu_lifecycle_with_recovery_intent(
    project_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    context = _RUNTIME_CONTEXT.get()
    if context is None:
        return reconcile_connector_remote_lifecycle_with_checkpoint(
            project_id,
            **kwargs,
        )
    intent = dict(context.get("intent") or {})
    epoch = _text(intent.get("sync_epoch_id"), 160)
    if not epoch or _text(kwargs.get("sync_epoch_id"), 160) != epoch:
        raise FeishuLifecycleRecoveryRuntimeError(
            "feishu_lifecycle_recovery_epoch_mismatch"
        )
    resources = kwargs.get("present_resources")
    if not isinstance(resources, list) or (
        _resource_digest(resources) != intent.get("resource_digest")
    ):
        raise FeishuLifecycleRecoveryRuntimeError(
            "feishu_lifecycle_recovery_resource_digest_mismatch"
        )
    update_connector_lifecycle_recovery_intent_state(
        context["project_id"],
        context["connector_instance_id"],
        state="LIFECYCLE_RECOVERY_RUNNING",
        root=context["root"],
        actor=context["actor"],
        expected_sync_epoch_id=epoch,
    )
    try:
        lifecycle = context["lifecycle_delegate"](project_id, **kwargs)
    except Exception as exc:
        try:
            update_connector_lifecycle_recovery_intent_state(
                context["project_id"],
                context["connector_instance_id"],
                state="RECOVERY_BLOCKED",
                root=context["root"],
                actor=context["actor"],
                expected_sync_epoch_id=epoch,
                reason_code=type(exc).__name__,
            )
        except Exception:
            pass
        raise
    if lifecycle.get("cursor_checkpoint_committed") is True:
        clear_connector_lifecycle_recovery_intent(
            context["project_id"],
            context["connector_instance_id"],
            root=context["root"],
            actor=context["actor"],
            expected_sync_epoch_id=epoch,
        )
        context["intent_completed"] = True
    else:
        update_connector_lifecycle_recovery_intent_state(
            context["project_id"],
            context["connector_instance_id"],
            state="LIFECYCLE_COMMITTED_PENDING_CHECKPOINT",
            root=context["root"],
            actor=context["actor"],
            expected_sync_epoch_id=epoch,
        )
    return lifecycle


def current_feishu_recovery_intent() -> dict[str, Any]:
    context = _RUNTIME_CONTEXT.get()
    if context is None:
        return {}
    return dict(context.get("intent") or {})


__all__ = [
    "FeishuLifecycleRecoveryRuntimeError",
    "current_feishu_recovery_intent",
    "discover_feishu_resources_with_recovery_intent",
    "feishu_lifecycle_recovery_scope",
    "reconcile_feishu_lifecycle_with_recovery_intent",
    "sync_feishu_snapshot_with_recovery_intent",
]
