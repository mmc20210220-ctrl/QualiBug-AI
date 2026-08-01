"""Recovery-aware facade for managed connector auto synchronization.

The original scheduling, fencing, profile checkpoint and retry implementation remains in
``connector_auto_sync_core``. The core remains the sole owner of
``connector_connection_profiles.json`` and ``managed_connector_sync_fence``. This facade
permanently composes lifecycle checkpoint recovery into that existing managed path and projects
persisted recovery state to operators. Production calls do not rewrite shared globals; explicit
dependency overrides retain a narrow, serialized compatibility bridge for focused tests and
embedders.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from . import connector_auto_sync_core as _core
from .connector_lifecycle_recovery_intent import lifecycle_recovery_intent_path
from .connector_lifecycle_recovery_supervisor import (
    ConnectorLifecycleRecoverySupervisorError,
    inspect_pending_connector_lifecycle_checkpoint,
    recover_pending_connector_lifecycle_checkpoint,
)

_CORE_BASELINE = {
    name: value
    for name, value in vars(_core).items()
    if not name.startswith("__")
}
_CORE_RECOVER_MANAGED_CHECKPOINT = _core.recover_managed_feishu_checkpoint
_CORE_RECOVERY_PENDING = _core._recovery_pending
_CORE_AUTO_SYNC_STATUS = _core.connector_auto_sync_status
_CORE_RUN_MANAGED = _core.run_managed_feishu_sync
_CORE_RUN_SWEEP = _core.run_connector_auto_sync_sweep

for _name, _value in _CORE_BASELINE.items():
    globals().setdefault(_name, _value)

_RECOVERY_ONLY_ACTIONS = {
    "RECOVERED_COMMITTED_CHECKPOINT",
    "REPLAYED_LIFECYCLE_AND_COMMITTED_CHECKPOINT",
}
_OVERRIDE_LOCK = threading.RLock()
_PERMANENT_COMPOSITION_NAMES = {
    "recover_managed_feishu_checkpoint",
    "_recovery_pending",
    "connector_auto_sync_status",
    "run_managed_feishu_sync",
    "run_connector_auto_sync_sweep",
}
_FACADE_OWNED_NAMES = {
    "_core",
    "_CORE_BASELINE",
    "_CORE_RECOVER_MANAGED_CHECKPOINT",
    "_CORE_RECOVERY_PENDING",
    "_CORE_AUTO_SYNC_STATUS",
    "_CORE_RUN_MANAGED",
    "_CORE_RUN_SWEEP",
    "_RECOVERY_ONLY_ACTIONS",
    "_OVERRIDE_LOCK",
    "_PERMANENT_COMPOSITION_NAMES",
    "_FACADE_OWNED_NAMES",
    "_explicit_core_overrides",
    "_temporary_explicit_core_overrides",
    "recover_managed_feishu_checkpoint",
    "_recovery_pending",
    "connector_auto_sync_status",
    "run_managed_feishu_sync",
    "run_connector_auto_sync_sweep",
    "threading",
    "contextmanager",
    "Path",
    "Any",
    "Callable",
    "Iterator",
    "ConnectorLifecycleRecoverySupervisorError",
    "inspect_pending_connector_lifecycle_checkpoint",
    "recover_pending_connector_lifecycle_checkpoint",
    "lifecycle_recovery_intent_path",
}


def _explicit_core_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for name, baseline in _CORE_BASELINE.items():
        if name in _PERMANENT_COMPOSITION_NAMES or name in _FACADE_OWNED_NAMES:
            continue
        current = globals().get(name, baseline)
        if current is not baseline:
            overrides[name] = current
    return overrides


@contextmanager
def _temporary_explicit_core_overrides() -> Iterator[None]:
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


def recover_managed_feishu_checkpoint(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    timeout: float = 15.0,
    transport: Any = None,
    sleeper: Callable[[float], None] = _core.time.sleep,
) -> dict[str, Any]:
    """Recover profile, lifecycle and cursor authorities before a new sync may start."""
    resolved_root = (root or _core.ROOT).resolve()
    clean_actor = dict(actor or _core._AUTO_SYNC_ACTOR)
    legacy = _CORE_RECOVER_MANAGED_CHECKPOINT(
        project_id,
        connector_instance_id,
        root=resolved_root,
        actor=clean_actor,
        timeout=timeout,
        transport=transport,
        sleeper=sleeper,
    )
    lifecycle = recover_pending_connector_lifecycle_checkpoint(
        project_id,
        connector_instance_id,
        root=resolved_root,
        actor=clean_actor,
    )
    action = str(lifecycle.get("recovery_action") or "")
    if action == "WAITING_FOR_SNAPSHOT_BIND":
        raise ConnectorLifecycleRecoverySupervisorError(
            "connector_lifecycle_recovery_waiting_for_snapshot_bind"
        )
    post_lifecycle_profile = {}
    if action in _RECOVERY_ONLY_ACTIONS:
        post_lifecycle_profile = _CORE_RECOVER_MANAGED_CHECKPOINT(
            project_id,
            connector_instance_id,
            root=resolved_root,
            actor=clean_actor,
            timeout=timeout,
            transport=transport,
            sleeper=sleeper,
        )
    return {
        **legacy,
        "connector_lifecycle_checkpoint_recovery": lifecycle,
        "post_lifecycle_profile_recovery": post_lifecycle_profile,
        "lifecycle_checkpoint_recovery_action": action,
        "lifecycle_checkpoint_recovery_is_automatic": True,
        "raw_error_persisted": False,
        "customer_material_mutation_executed": False,
    }


def _recovery_pending(
    root: Path,
    project: str,
    connector: str,
    instance: dict[str, Any],
    profile: dict[str, Any],
    *,
    now: float,
) -> bool:
    if _CORE_RECOVERY_PENDING(
        root,
        project,
        connector,
        instance,
        profile,
        now=now,
    ):
        return True
    if str(instance.get("pending_lifecycle_sync_epoch_id") or "").strip():
        return True
    if instance.get("lifecycle_recovery_attention_required") is True:
        return True
    try:
        return lifecycle_recovery_intent_path(
            project,
            connector,
            root=root,
        ).is_file()
    except Exception:
        return True


def connector_auto_sync_status(
    root: Path,
    project_id: str,
    connector_instance_id: str,
) -> dict[str, Any]:
    base = _CORE_AUTO_SYNC_STATUS(root, project_id, connector_instance_id)
    resolved_root = root.resolve()
    project = _core._safe_project_id(project_id)
    connector = _core._text(connector_instance_id, 160)
    try:
        instance = _core._instance(project, connector, resolved_root)
        inspection = inspect_pending_connector_lifecycle_checkpoint(
            project,
            connector,
            root=resolved_root,
        )
    except Exception:
        return {
            **base,
            "lifecycle_recovery_state": "NOT_AVAILABLE",
            "lifecycle_recovery_attention_required": False,
            "lifecycle_checkpoint_recovery_is_automatic": True,
            "lifecycle_recovery_raw_error_returned": False,
        }

    persisted_state = _core._text(
        instance.get("lifecycle_recovery_state"), 80
    )
    inspection_status = _core._text(inspection.get("status"), 80)
    attention = bool(
        instance.get("lifecycle_recovery_attention_required")
        or inspection.get("attention_required")
    )
    pending = inspection_status not in {"", "NOT_REQUIRED"}
    if attention:
        projected_state = "attention_required"
        message = "连接器生命周期恢复受阻，需要检查运行收据或恢复意图"
    elif pending:
        projected_state = "recovering"
        message = "系统正在自动恢复资料生命周期与同步断点"
    else:
        projected_state = base.get("state")
        message = base.get("message")
    return {
        **base,
        "state": projected_state,
        "message": message,
        "lifecycle_recovery_state": persisted_state or inspection_status,
        "lifecycle_recovery_pending_sync_epoch_id": _core._text(
            inspection.get("pending_sync_epoch_id"), 160
        ),
        "lifecycle_recovery_pending_age_seconds": int(
            inspection.get("pending_age_seconds") or 0
        ),
        "lifecycle_recovery_stale": bool(inspection.get("stale")),
        "lifecycle_recovery_attention_required": attention,
        "lifecycle_recovery_failure_count": int(
            instance.get("lifecycle_recovery_failure_count") or 0
        ),
        "lifecycle_recovery_last_error_category": _core._text(
            instance.get("lifecycle_recovery_last_error_category"), 120
        ),
        "maintenance_required_by_user": bool(
            base.get("maintenance_required_by_user") or attention
        ),
        "lifecycle_checkpoint_recovery_is_automatic": True,
        "lifecycle_recovery_raw_error_returned": False,
    }


def run_managed_feishu_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
    with _temporary_explicit_core_overrides():
        previous_recovery = _core.recover_managed_feishu_checkpoint
        _core.recover_managed_feishu_checkpoint = globals().get(
            "recover_managed_feishu_checkpoint",
            recover_managed_feishu_checkpoint,
        )
        try:
            return _CORE_RUN_MANAGED(*args, **kwargs)
        finally:
            _core.recover_managed_feishu_checkpoint = previous_recovery


def run_connector_auto_sync_sweep(
    root: Path,
    *,
    now: float | None = None,
    sync_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_runner = sync_runner or run_managed_feishu_sync
    with _temporary_explicit_core_overrides():
        return _CORE_RUN_SWEEP(
            root,
            now=now,
            sync_runner=selected_runner,
        )


# Permanently compose the original managed path and supervisor loop with the new boundaries.
_core.recover_managed_feishu_checkpoint = recover_managed_feishu_checkpoint
_core._recovery_pending = _recovery_pending
_core.connector_auto_sync_status = connector_auto_sync_status
_core.run_managed_feishu_sync = run_managed_feishu_sync
_core.run_connector_auto_sync_sweep = run_connector_auto_sync_sweep

ensure_connector_auto_sync_supervisor = _core.ensure_connector_auto_sync_supervisor
stop_connector_auto_sync_supervisor = _core.stop_connector_auto_sync_supervisor
stop_all_connector_auto_sync_supervisors = (
    _core.stop_all_connector_auto_sync_supervisors
)
test_managed_feishu_connection = _core.test_managed_feishu_connection
validate_connector_checkpoint = _core.validate_connector_checkpoint


__all__ = [
    "connector_auto_sync_status",
    "ensure_connector_auto_sync_supervisor",
    "recover_managed_feishu_checkpoint",
    "run_connector_auto_sync_sweep",
    "run_managed_feishu_sync",
    "stop_all_connector_auto_sync_supervisors",
    "stop_connector_auto_sync_supervisor",
    "test_managed_feishu_connection",
    "validate_connector_checkpoint",
]
