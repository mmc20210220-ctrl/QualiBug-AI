"""Generic managed connector synchronization and automatic refresh supervisor.

One application authority owns trusted synchronization, crash recovery, fencing, scheduling,
retry backoff, and operator-safe status. Adapter-specific work is selected from the existing
Connector Registry; checkpoint, lifecycle, source-occurrence, and fencing authorities remain
unchanged.
"""
from __future__ import annotations

import atexit
import calendar
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .connector_checkpoint_recovery import (
    _legacy_stale_seconds,
    begin_connector_checkpoint_commit,
    clear_connector_checkpoint_journal,
    recover_connector_checkpoint_commit,
    stage_connector_checkpoint_result,
)
from .connector_connection_profiles import (
    ConnectorProfileError,
    commit_connector_sync_checkpoint,
    list_connector_connection_profiles,
    load_connector_sync_checkpoint,
    resolve_connector_connection_profile,
)
from .connector_oauth_authority import refresh_connector_oauth
from .connector_registry import (
    ConnectorRegistryError,
    build_default_connector_registry,
)
from .connector_sync_authority import ConnectorSyncError, list_connector_instances
from .connector_sync_fencing import _takeover_seconds, managed_connector_sync_fence
from .connector_sync_ownership import inspect_connector_sync_ownership
from .enterprise_knowledge_center._common import ROOT
from .real_project_onboarding import _safe_project_id

_AUTO_SYNC_ACTOR = {"name": "qualibug_auto_sync", "role": "knowledge_admin"}
_STATE_LOCK = threading.RLock()
_SUPERVISORS: dict[str, dict[str, Any]] = {}
_ATTEMPTS: dict[tuple[str, str, str], dict[str, Any]] = {}
_ATEXIT_REGISTERED = False


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: str) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _text(os.environ.get(name), 32)
    try:
        parsed = int(raw) if raw else default
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _enabled() -> bool:
    return _text(
        os.environ.get("QUALIBUG_CONNECTOR_AUTO_SYNC_ENABLED", "1"), 8
    ).lower() not in {"0", "false", "no", "off"}


def _policy() -> dict[str, int]:
    return {
        "refresh_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_INTERVAL_SECONDS",
            6 * 60 * 60,
            15 * 60,
            7 * 24 * 60 * 60,
        ),
        "sweep_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_SWEEP_SECONDS",
            60,
            10,
            60 * 60,
        ),
        "initial_delay_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_INITIAL_DELAY_SECONDS",
            10,
            0,
            10 * 60,
        ),
        "retry_base_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_RETRY_BASE_SECONDS",
            60,
            10,
            60 * 60,
        ),
        "retry_max_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_RETRY_MAX_SECONDS",
            60 * 60,
            60,
            24 * 60 * 60,
        ),
        "rate_limit_per_minute": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_RATE_LIMIT_PER_MINUTE",
            60,
            1,
            600,
        ),
        "max_resources": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_MAX_RESOURCES",
            5000,
            1,
            100000,
        ),
        "max_export_polls": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_MAX_EXPORT_POLLS",
            20,
            1,
            200,
        ),
        "timeout_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_TIMEOUT_SECONDS",
            15,
            1,
            300,
        ),
    }


_INSTANCE_POLICY_FIELDS: dict[str, tuple[str, int, int]] = {
    "refresh_seconds": ("sync_interval_seconds", 15 * 60, 7 * 24 * 60 * 60),
    "retry_base_seconds": ("sync_retry_base_seconds", 10, 60 * 60),
    "retry_max_seconds": ("sync_retry_max_seconds", 60, 24 * 60 * 60),
    "rate_limit_per_minute": ("sync_rate_limit_per_minute", 1, 600),
    "max_resources": ("sync_max_resources", 1, 100000),
    "max_export_polls": ("sync_max_export_polls", 1, 200),
    "timeout_seconds": ("sync_timeout_seconds", 1, 300),
}


def _instance_policy(instance: Mapping[str, Any], *, base: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Resolve only non-secret scheduling/resource policy from one instance metadata row."""
    policy = dict(base or _policy())
    metadata = instance.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise ConnectorSyncError("connector_instance_metadata_must_be_object")
    for field, (metadata_key, minimum, maximum) in _INSTANCE_POLICY_FIELDS.items():
        fallback = int(policy.get(field, minimum))
        raw = metadata.get(metadata_key)
        if raw in (None, ""):
            value = fallback
        else:
            if isinstance(raw, bool):
                raise ConnectorSyncError(
                    f"connector_auto_sync_policy_invalid:{metadata_key}"
                )
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise ConnectorSyncError(
                    f"connector_auto_sync_policy_invalid:{metadata_key}"
                ) from exc
            if not minimum <= value <= maximum:
                raise ConnectorSyncError(
                    f"connector_auto_sync_policy_out_of_range:{metadata_key}"
                )
        policy[field] = value
    if policy["retry_max_seconds"] < policy["retry_base_seconds"]:
        raise ConnectorSyncError("connector_auto_sync_retry_policy_invalid")
    return {key: int(value) for key, value in policy.items()}


def _parse_utc(value: Any) -> float:
    text = _text(value, 80)
    if not text:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


def _utc(timestamp: float | None = None) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() if timestamp is None else timestamp),
    )


def _instance(project: str, connector: str, root: Path) -> dict[str, Any]:
    rows = list_connector_instances(
        project,
        root=root,
        include_disabled=True,
    ).get("connector_instances") or []
    row = next(
        (
            dict(item)
            for item in rows
            if isinstance(item, dict)
            and _text(item.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if row is None:
        raise ConnectorSyncError("connector_instance_not_registered")
    return row


def _profile_resolver(project: str, root: Path):
    def resolve(profile_ref: str) -> dict[str, str]:
        return resolve_connector_connection_profile(
            project,
            profile_ref,
            root=root,
        )

    return resolve


def _managed_adapter(
    project: str,
    connector: str,
    root: Path,
    *,
    instance: Mapping[str, Any] | None = None,
) -> Any:
    row = instance or _instance(project, connector, root)
    connector_type = _text(row.get("connector_type"), 160)
    if not connector_type:
        raise ConnectorSyncError("connector_instance_type_missing")
    try:
        return build_default_connector_registry().get(connector_type)
    except ConnectorRegistryError as exc:
        raise ConnectorSyncError(str(exc)) from exc


def _managed_context(
    project: str,
    connector: str,
    root: Path,
    actor: Mapping[str, Any],
    instance: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    timeout: float,
    transport: Any,
    sleeper: Callable[[float], None],
    previous_cursor: str = "",
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_nodes: int | None = None,
    max_export_polls: int | None = None,
    export_poll_interval: float = 0.5,
    allow_raw_text_fallback: bool = False,
    sync_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "project_id": project,
        "connector_instance_id": connector,
        "connector_type": _text(instance.get("connector_type"), 160),
        "connection_profile_ref": _text(
            instance.get("connection_profile_ref"), 500
        ),
        "resource_scope": _text(instance.get("resource_scope"), 20000),
        "root": root,
        "actor": dict(actor),
        "resolve_connection_profile": _profile_resolver(project, root),
        "previous_cursor": previous_cursor,
        "deletion_policy": deletion_policy,
        "max_retire_count": max_retire_count,
        "max_retire_ratio": max_retire_ratio,
        "max_resources": int(max_nodes if max_nodes is not None else policy["max_resources"]),
        "max_export_polls": int(
            max_export_polls
            if max_export_polls is not None
            else policy["max_export_polls"]
        ),
        "export_poll_interval": export_poll_interval,
        "allow_raw_text_fallback": allow_raw_text_fallback,
        "timeout": timeout,
        "transport": transport,
        "sleeper": sleeper,
        "sync_policy": dict(policy),
        "sync_runner": sync_runner,
    }


def _adapter_remote_checkpoint(
    adapter: Any,
    context: Mapping[str, Any],
) -> str:
    resolver = getattr(adapter, "managed_remote_checkpoint", None)
    if not callable(resolver):
        connector_type = _text(context.get("connector_type"), 160)
        raise ConnectorSyncError(
            f"connector_remote_checkpoint_not_supported:{connector_type}"
        )
    value = resolver(context)
    if not isinstance(value, str):
        raise ConnectorSyncError("connector_remote_checkpoint_invalid")
    return value


def _adapter_managed_sync(
    adapter: Any,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    runner = getattr(adapter, "managed_sync", None)
    if not callable(runner):
        connector_type = _text(context.get("connector_type"), 160)
        raise ConnectorSyncError(
            f"connector_managed_sync_not_supported:{connector_type}"
        )
    result = runner(context)
    if not isinstance(result, Mapping):
        raise ConnectorSyncError("connector_managed_sync_result_invalid")
    return dict(result)


def validate_connector_checkpoint(
    project_id: str,
    connector_instance_id: str,
    checkpoint: str,
    *,
    root: Path | None = None,
) -> None:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    instance = _instance(project, connector, resolved_root)
    expected = _text(instance.get("last_committed_cursor_fingerprint"), 128)
    if not expected:
        if checkpoint:
            raise ConnectorProfileError(
                "connector_checkpoint_exists_without_registry_commit"
            )
        return
    if not checkpoint:
        raise ConnectorProfileError(
            "connector_checkpoint_missing_for_registry_commit"
        )
    if _fingerprint(checkpoint) != expected:
        raise ConnectorProfileError("connector_checkpoint_registry_mismatch")


def recover_managed_connector_checkpoint(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    timeout: float = 15.0,
    transport: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    clean_actor = dict(actor or _AUTO_SYNC_ACTOR)
    instance = _instance(project, connector, resolved_root)
    policy = _instance_policy(instance)
    adapter = _managed_adapter(
        project,
        connector,
        resolved_root,
        instance=instance,
    )
    context = _managed_context(
        project,
        connector,
        resolved_root,
        clean_actor,
        instance,
        policy,
        timeout=float(timeout),
        transport=transport,
        sleeper=sleeper,
    )
    return recover_connector_checkpoint_commit(
        project,
        connector,
        root=resolved_root,
        actor=clean_actor,
        remote_checkpoint_resolver=lambda: _adapter_remote_checkpoint(
            adapter,
            context,
        ),
    )


def test_managed_connector_connection(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    timeout: float = 15.0,
    transport: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
    oauth_token_requester: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    instance = _instance(project, connector, resolved_root)
    adapter = _managed_adapter(
        project,
        connector,
        resolved_root,
        instance=instance,
    )
    oauth_refresh: dict[str, Any] = {}
    manifest_getter = getattr(adapter, "manifest", None)
    if not callable(manifest_getter):
        raise ConnectorSyncError("connector_manifest_not_supported")
    manifest = manifest_getter()
    if getattr(manifest, "oauth_schema", None):
        oauth_refresh = refresh_connector_oauth(
            project,
            connector,
            root=resolved_root,
            actor=_AUTO_SYNC_ACTOR,
            token_requester=oauth_token_requester,
            timeout=float(timeout),
        )
    context = _managed_context(
        project,
        connector,
        resolved_root,
        _AUTO_SYNC_ACTOR,
        instance,
        _instance_policy(instance),
        timeout=float(timeout),
        transport=transport,
        sleeper=sleeper,
    )
    tester = getattr(adapter, "test_connection", None)
    if not callable(tester):
        raise ConnectorSyncError("connector_test_connection_not_supported")
    result = tester(context)
    if not isinstance(result, Mapping):
        raise ConnectorSyncError("connector_test_connection_result_invalid")
    projected = dict(result)
    if oauth_refresh:
        projected["oauth_refresh"] = oauth_refresh
    return projected


def _clear_intent_if_registry_did_not_advance(
    project: str,
    connector: str,
    previous_checkpoint: str,
    attempt_id: str,
    root: Path,
    actor: dict[str, Any],
) -> None:
    try:
        instance = _instance(project, connector, root)
        registry_fingerprint = _text(
            instance.get("last_committed_cursor_fingerprint"), 128
        )
        if registry_fingerprint == _fingerprint(previous_checkpoint):
            clear_connector_checkpoint_journal(
                project,
                connector,
                root=root,
                actor=actor,
                expected_attempt_id=attempt_id,
            )
    except Exception:
        return


def run_managed_connector_sync(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_nodes: int | None = None,
    max_export_polls: int | None = None,
    export_poll_interval: float = 0.5,
    allow_raw_text_fallback: bool = False,
    timeout: float | None = None,
    transport: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
    oauth_token_requester: Callable[..., Mapping[str, Any]] | None = None,
    sync_policy: Mapping[str, Any] | None = None,
    sync_runner: Callable[..., Mapping[str, Any]] | None = None,
    recovery_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute one registry-selected connector through the managed authority."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    clean_actor = dict(actor or _AUTO_SYNC_ACTOR)
    base_policy = dict(sync_policy or _policy())
    timeout_value = float(
        timeout
        if timeout is not None
        else base_policy.get("timeout_seconds", 15)
    )

    with managed_connector_sync_fence(
        project,
        connector,
        root=resolved_root,
        actor=clean_actor,
    ) as fence:
        oauth_refresh: dict[str, Any] = {}
        if sync_runner is None:
            instance_for_refresh = _instance(project, connector, resolved_root)
            adapter_for_refresh = _managed_adapter(
                project,
                connector,
                resolved_root,
                instance=instance_for_refresh,
            )
            manifest_getter = getattr(adapter_for_refresh, "manifest", None)
            if not callable(manifest_getter):
                raise ConnectorSyncError("connector_manifest_not_supported")
            manifest_for_refresh = manifest_getter()
            if getattr(manifest_for_refresh, "oauth_schema", None):
                oauth_refresh = refresh_connector_oauth(
                    project,
                    connector,
                    root=resolved_root,
                    actor=clean_actor,
                    token_requester=oauth_token_requester,
                    timeout=timeout_value,
                )
        recovery = (recovery_runner or recover_managed_connector_checkpoint)(
            project,
            connector,
            root=resolved_root,
            actor=clean_actor,
            timeout=timeout_value,
            transport=transport,
            sleeper=sleeper,
        )
        previous_cursor = load_connector_sync_checkpoint(
            project,
            connector,
            root=resolved_root,
        )
        validate_connector_checkpoint(
            project,
            connector,
            previous_cursor,
            root=resolved_root,
        )
        intent = begin_connector_checkpoint_commit(
            project,
            connector,
            previous_cursor,
            root=resolved_root,
            actor=clean_actor,
        )
        attempt_id = _text(intent.get("attempt_id"), 160)

        try:
            if sync_runner is not None:
                raw_run = sync_runner(
                    project,
                    connector_instance_id=connector,
                    resolve_connection_profile=_profile_resolver(
                        project,
                        resolved_root,
                    ),
                    root=resolved_root,
                    actor=clean_actor,
                    previous_cursor=previous_cursor,
                    deletion_policy=deletion_policy,
                    max_retire_count=max_retire_count,
                    max_retire_ratio=max_retire_ratio,
                    max_nodes=int(
                        max_nodes
                        if max_nodes is not None
                        else base_policy.get("max_resources", 5000)
                    ),
                    max_export_polls=int(
                        max_export_polls
                        if max_export_polls is not None
                        else base_policy.get("max_export_polls", 20)
                    ),
                    export_poll_interval=export_poll_interval,
                    allow_raw_text_fallback=allow_raw_text_fallback,
                    timeout=timeout_value,
                    transport=transport,
                    sleeper=sleeper,
                )
                if not isinstance(raw_run, Mapping):
                    raise ConnectorSyncError("connector_managed_sync_result_invalid")
                run = dict(raw_run)
            else:
                instance = _instance(project, connector, resolved_root)
                policy = _instance_policy(instance, base=base_policy)
                adapter = _managed_adapter(
                    project,
                    connector,
                    resolved_root,
                    instance=instance,
                )
                context = _managed_context(
                    project,
                    connector,
                    resolved_root,
                    clean_actor,
                    instance,
                    policy,
                    timeout=timeout_value,
                    transport=transport,
                    sleeper=sleeper,
                    previous_cursor=previous_cursor,
                    deletion_policy=deletion_policy,
                    max_retire_count=max_retire_count,
                    max_retire_ratio=max_retire_ratio,
                    max_nodes=max_nodes,
                    max_export_polls=max_export_polls,
                    export_poll_interval=export_poll_interval,
                    allow_raw_text_fallback=allow_raw_text_fallback,
                )
                run = _adapter_managed_sync(adapter, context)
                discovered_count = run.get("discovered_resource_count")
                if discovered_count is not None:
                    try:
                        if int(discovered_count) > int(context["max_resources"]):
                            raise ConnectorSyncError(
                                "connector_resource_limit_exceeded"
                            )
                    except (TypeError, ValueError) as exc:
                        raise ConnectorSyncError(
                            "connector_discovered_resource_count_invalid"
                        ) from exc
            if oauth_refresh:
                run["oauth_refresh"] = oauth_refresh
            if run.get("status") != "COMPLETE":
                clear_connector_checkpoint_journal(
                    project,
                    connector,
                    root=resolved_root,
                    actor=clean_actor,
                    expected_attempt_id=attempt_id,
                )
                return {
                    **run,
                    "sync_write_fencing": "MONOTONIC_REGISTRY_TOKEN",
                    "stale_writer_blocked": True,
                }

            checkpoint = _text(run.get("next_cursor"), 500)
            epoch = _text(run.get("sync_epoch_id"), 160)
            if not checkpoint or not epoch:
                raise ConnectorProfileError(
                    "connector_sync_checkpoint_missing_after_complete_run"
                )
            committed_fingerprint = _text(
                run.get("committed_cursor_fingerprint"), 128
            )
            if committed_fingerprint and committed_fingerprint != _fingerprint(checkpoint):
                raise ConnectorProfileError(
                    "connector_sync_checkpoint_commit_mismatch"
                )

            stage_connector_checkpoint_result(
                project,
                connector,
                attempt_id,
                checkpoint,
                sync_epoch_id=epoch,
                root=resolved_root,
                actor=clean_actor,
            )
            commit_connector_sync_checkpoint(
                project,
                connector,
                checkpoint,
                sync_epoch_id=epoch,
                root=resolved_root,
                actor=clean_actor,
            )
            clear_connector_checkpoint_journal(
                project,
                connector,
                root=resolved_root,
                actor=clean_actor,
                expected_attempt_id=attempt_id,
            )
            return {
                **run,
                "checkpoint_commit_protocol": "RECOVERABLE_TWO_STAGE",
                "checkpoint_recovery_required": False,
                "sync_write_fencing": "MONOTONIC_REGISTRY_TOKEN",
                "stale_writer_blocked": True,
                "stale_owner_taken_over": bool(fence.get("takeover")),
                "lifecycle_recovery_action": _text(
                    dict(recovery.get("sync_lifecycle_recovery") or {}).get("action"),
                    80,
                ),
            }
        except Exception:
            _clear_intent_if_registry_did_not_advance(
                project,
                connector,
                previous_cursor,
                attempt_id,
                resolved_root,
                clean_actor,
            )
            raise


def _project_ids(root: Path) -> list[str]:
    workspace = root / "platform_workspace"
    if not workspace.exists():
        return []
    projects: list[str] = []
    for profile_path in workspace.glob(
        "*/enterprise_knowledge_center/connector_connection_profiles.json"
    ):
        raw = profile_path.parent.parent.name
        try:
            project = _safe_project_id(raw)
        except ValueError:
            continue
        projects.append(project)
    return sorted(set(projects))


def _profile_index(project: str, root: Path) -> dict[str, dict[str, Any]]:
    payload = list_connector_connection_profiles(project, root=root)
    return {
        _text(row.get("connector_instance_id"), 160): dict(row)
        for row in payload.get("profiles") or []
        if isinstance(row, dict) and row.get("credentials_configured") is True
    }


def _key(root: Path, project: str, connector: str) -> tuple[str, str, str]:
    return str(root.resolve()), project, connector


def _recovery_pending(
    root: Path,
    project: str,
    connector: str,
    instance: dict[str, Any],
    profile: dict[str, Any],
    *,
    now: float,
) -> bool:
    workspace = (
        root
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
    )
    journal_path = workspace / "connector_checkpoint_journal" / f"{connector}.json"
    ownership_path = workspace / "connector_sync_ownership" / f"{connector}.json"
    active_epoch = _text(instance.get("active_sync_epoch_id"), 160)

    if ownership_path.is_file():
        try:
            ownership = inspect_connector_sync_ownership(
                project,
                connector,
                root=root,
                stale_after_seconds=_takeover_seconds(),
            )
        except Exception:
            return True
        if (
            ownership.get("owner_alive") is True
            and ownership.get("progress_stale") is not True
        ):
            return False
        return True

    if active_epoch:
        started = _parse_utc(instance.get("last_sync_started_at_utc"))
        return bool(
            started
            and now - started >= _legacy_stale_seconds()
        )

    registry_fingerprint = _text(
        instance.get("last_committed_cursor_fingerprint"), 128
    )
    profile_fingerprint = _text(profile.get("checkpoint_fingerprint"), 128)
    return bool(
        journal_path.is_file()
        or registry_fingerprint != profile_fingerprint
    )


def _due(
    instance: dict[str, Any],
    attempt: dict[str, Any],
    *,
    now: float,
    refresh_seconds: int,
    force: bool = False,
) -> bool:
    if instance.get("status") != "ACTIVE":
        return False
    if instance.get("active_sync_epoch_id") and not force:
        return False
    next_attempt = float(attempt.get("next_attempt_unix") or 0)
    if next_attempt and now < next_attempt:
        return False
    if force:
        return True
    last_success = _parse_utc(instance.get("last_successful_sync_at_utc"))
    last_failure = _parse_utc(instance.get("last_failed_sync_at_utc"))
    if last_failure > last_success:
        return True
    return not last_success or now - last_success >= refresh_seconds


def _failure_category(exc: Exception) -> str:
    message = str(exc or "").lower()
    if (
        "transport" in message
        or "api_failed" in message
        or "http_failed" in message
        or "remote_unavailable" in message
    ):
        return "REMOTE_UNAVAILABLE"
    if "permission" in message or "forbidden" in message:
        return "PERMISSION_REQUIRED"
    if "credential" in message or "profile" in message or "token" in message:
        return "AUTHORIZATION_REQUIRED"
    if (
        "already_running" in message
        or "lock_held" in message
        or "owner_active" in message
    ):
        return "BUSY"
    if "checkpoint" in message or "cursor" in message or "fence" in message:
        return "AUTO_RECOVERY"
    return "RETRYING"


def _oauth_refresh_projection(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    return {
        "supported": raw.get("supported") is True,
        "attempted": raw.get("attempted") is True,
        "refreshed": raw.get("refreshed") is True,
        "refresh_status": _text(raw.get("refresh_status"), 40)
        or "NOT_MEASURED",
        "credential_status": _text(raw.get("credential_status"), 64),
        "credential_expires_at_utc": _text(
            raw.get("credential_expires_at_utc"), 80
        ),
        "permission_status": _text(raw.get("permission_status"), 80),
        "credential_values_returned": False,
        "source_identity_preserved": raw.get("source_identity_preserved") is True,
        "checkpoint_preserved": raw.get("checkpoint_preserved") is True,
        "remote_deletion_inferred": False,
    }


def _record_success(
    key: tuple[str, str, str],
    run: dict[str, Any],
    now: float,
) -> None:
    with _STATE_LOCK:
        previous = dict(_ATTEMPTS.get(key) or {})
        _ATTEMPTS[key] = {
            "state": "healthy",
            "failure_count": 0,
            "last_attempt_at_utc": _utc(now),
            "last_success_at_utc": _utc(now),
            "last_sync_epoch_id": _text(run.get("sync_epoch_id"), 160),
            "next_attempt_unix": 0.0,
            "next_attempt_at_utc": "",
            "last_error_category": "",
            "raw_error_persisted": False,
            "last_oauth_refresh": _oauth_refresh_projection(
                run.get("oauth_refresh")
            ),
            "attempt_timestamps": list(previous.get("attempt_timestamps") or []),
        }


def _record_failure(
    key: tuple[str, str, str],
    exc: Exception,
    now: float,
    *,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> None:
    with _STATE_LOCK:
        previous = dict(_ATTEMPTS.get(key) or {})
        failure_count = int(previous.get("failure_count") or 0) + 1
        delay = min(
            retry_base_seconds * (2 ** max(0, failure_count - 1)),
            retry_max_seconds,
        )
        next_attempt = now + delay
        _ATTEMPTS[key] = {
            "state": "retrying",
            "failure_count": failure_count,
            "last_attempt_at_utc": _utc(now),
            "next_attempt_unix": next_attempt,
            "next_attempt_at_utc": _utc(next_attempt),
            "last_error_category": _failure_category(exc),
            "last_error_type": type(exc).__name__,
            "raw_error_persisted": False,
            "last_oauth_refresh": _oauth_refresh_projection(
                previous.get("last_oauth_refresh")
            ),
            "attempt_timestamps": list(previous.get("attempt_timestamps") or []),
        }


def _rate_limit_blocked(
    key: tuple[str, str, str],
    now: float,
    *,
    limit_per_minute: int,
) -> tuple[bool, float]:
    limit = max(1, int(limit_per_minute))
    with _STATE_LOCK:
        previous = dict(_ATTEMPTS.get(key) or {})
        history: list[float] = []
        for raw in previous.get("attempt_timestamps") or []:
            try:
                timestamp = float(raw)
            except (TypeError, ValueError):
                continue
            if timestamp > now or now - timestamp < 60.0:
                history.append(timestamp)
        if len(history) >= limit:
            next_attempt = min(history) + 60.0
            _ATTEMPTS[key] = {
                **previous,
                "attempt_timestamps": history,
                "rate_limited": True,
                "next_attempt_unix": next_attempt,
                "next_attempt_at_utc": _utc(next_attempt),
            }
            return True, next_attempt
        history.append(now)
        _ATTEMPTS[key] = {
            **previous,
            "attempt_timestamps": history,
            "rate_limited": False,
        }
    return False, 0.0


def _record_rate_limited(
    key: tuple[str, str, str],
    now: float,
    next_attempt: float,
) -> None:
    with _STATE_LOCK:
        previous = dict(_ATTEMPTS.get(key) or {})
        _ATTEMPTS[key] = {
            **previous,
            "state": "scheduled",
            "last_attempt_at_utc": _utc(now),
            "next_attempt_unix": next_attempt,
            "next_attempt_at_utc": _utc(next_attempt),
            "last_error_category": "RATE_LIMITED",
            "raw_error_persisted": False,
        }


def run_connector_auto_sync_sweep(
    root: Path,
    *,
    now: float | None = None,
    sync_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Refresh configured connector instances through the single managed sync path."""
    resolved_root = root.resolve()
    timestamp = time.time() if now is None else float(now)
    policy = _policy()
    managed_default = sync_runner is None
    selected_runner = sync_runner or run_managed_connector_sync
    attempted = succeeded = failed = skipped = 0

    for project in _project_ids(resolved_root):
        try:
            profiles = _profile_index(project, resolved_root)
            instances = list_connector_instances(
                project,
                root=resolved_root,
                include_disabled=True,
            ).get("connector_instances") or []
        except Exception:
            failed += 1
            continue

        for raw in instances:
            if not isinstance(raw, dict):
                continue
            connector = _text(raw.get("connector_instance_id"), 160)
            profile = profiles.get(connector)
            if not connector or not isinstance(profile, dict):
                skipped += 1
                continue
            key = _key(resolved_root, project, connector)
            with _STATE_LOCK:
                attempt = dict(_ATTEMPTS.get(key) or {})
            try:
                instance_policy = _instance_policy(raw, base=policy)
            except Exception as exc:
                failed += 1
                _record_failure(
                    key,
                    exc,
                    timestamp,
                    retry_base_seconds=int(
                        policy.get("retry_base_seconds", 60)
                    ),
                    retry_max_seconds=int(policy.get("retry_max_seconds", 3600)),
                )
                continue
            force_recovery = (
                managed_default
                and _recovery_pending(
                    resolved_root,
                    project,
                    connector,
                    raw,
                    profile,
                    now=timestamp,
                )
            )
            if not _due(
                raw,
                attempt,
                now=timestamp,
                refresh_seconds=instance_policy["refresh_seconds"],
                force=force_recovery,
            ):
                skipped += 1
                continue

            if managed_default:
                try:
                    _managed_adapter(
                        project,
                        connector,
                        resolved_root,
                        instance=raw,
                    )
                except Exception as exc:
                    failed += 1
                    _record_failure(
                        key,
                        exc,
                        timestamp,
                        retry_base_seconds=instance_policy["retry_base_seconds"],
                        retry_max_seconds=instance_policy["retry_max_seconds"],
                    )
                    continue

            rate_blocked, next_attempt = _rate_limit_blocked(
                key,
                timestamp,
                limit_per_minute=instance_policy["rate_limit_per_minute"],
            )
            if rate_blocked:
                skipped += 1
                _record_rate_limited(key, timestamp, next_attempt)
                continue

            attempted += 1
            with _STATE_LOCK:
                current_attempt = dict(_ATTEMPTS.get(key) or attempt)
                _ATTEMPTS[key] = {
                    **current_attempt,
                    "state": "running",
                    "last_attempt_at_utc": _utc(timestamp),
                }
            try:
                runner_kwargs = {
                    "root": resolved_root,
                    "actor": _AUTO_SYNC_ACTOR,
                }
                if managed_default:
                    runner_kwargs["sync_policy"] = instance_policy
                run = selected_runner(
                    project,
                    connector,
                    **runner_kwargs,
                )
                if run.get("status") != "COMPLETE":
                    raise ConnectorSyncError("connector_auto_sync_incomplete")
            except Exception as exc:
                failed += 1
                _record_failure(
                    key,
                    exc,
                    timestamp,
                    retry_base_seconds=instance_policy["retry_base_seconds"],
                    retry_max_seconds=instance_policy["retry_max_seconds"],
                )
            else:
                succeeded += 1
                _record_success(key, run, timestamp)

    return {
        "enabled": _enabled(),
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "completed_at_utc": _utc(timestamp),
        "new_registry_created": False,
    }


def connector_auto_sync_status(
    root: Path,
    project_id: str,
    connector_instance_id: str,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    policy = _policy()
    try:
        policy = _instance_policy(
            _instance(project, connector, resolved_root),
            base=policy,
        )
    except Exception:
        policy = _instance_policy({}, base=policy)
    with _STATE_LOCK:
        state = dict(_ATTEMPTS.get(_key(resolved_root, project, connector)) or {})
    status = _text(state.get("state"), 32) or (
        "scheduled" if _enabled() else "disabled"
    )
    messages = {
        "scheduled": "已开启自动更新",
        "running": "正在自动更新",
        "healthy": "自动更新正常",
        "retrying": "更新暂时中断，系统会自动恢复并重试",
        "disabled": "自动更新已关闭",
    }
    attention = _text(state.get("last_error_category"), 80)
    return {
        "enabled": _enabled(),
        "state": status,
        "message": messages.get(status, "已开启自动更新"),
        "last_attempt_at_utc": _text(state.get("last_attempt_at_utc"), 80),
        "last_success_at_utc": _text(state.get("last_success_at_utc"), 80),
        "next_attempt_at_utc": _text(state.get("next_attempt_at_utc"), 80),
        "failure_count": int(state.get("failure_count") or 0),
        "attention": attention,
        "refresh_interval_seconds": policy["refresh_seconds"],
        "rate_limit_per_minute": policy["rate_limit_per_minute"],
        "max_resources": policy["max_resources"],
        "max_export_polls": policy["max_export_polls"],
        "last_oauth_refresh": _oauth_refresh_projection(
            state.get("last_oauth_refresh")
        ),
        "maintenance_required_by_user": status == "retrying"
        and attention in {"AUTHORIZATION_REQUIRED", "PERMISSION_REQUIRED"},
        "checkpoint_recovery_is_automatic": True,
        "stale_writer_fencing_is_automatic": True,
        "raw_error_returned": False,
    }


def _supervisor_loop(root: Path, stop_event: threading.Event) -> None:
    policy = _policy()
    if stop_event.wait(policy["initial_delay_seconds"]):
        return
    while not stop_event.is_set():
        run_connector_auto_sync_sweep(root)
        stop_event.wait(policy["sweep_seconds"])


def ensure_connector_auto_sync_supervisor(root: Path) -> dict[str, Any]:
    """Start one daemon supervisor per deployment root, idempotently."""
    global _ATEXIT_REGISTERED
    resolved_root = root.resolve()
    key = str(resolved_root)
    if not _enabled():
        return {"enabled": False, "started": False, "root": key}
    with _STATE_LOCK:
        current = _SUPERVISORS.get(key)
        thread = current.get("thread") if isinstance(current, dict) else None
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return {
                "enabled": True,
                "started": False,
                "already_running": True,
                "root": key,
            }
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_supervisor_loop,
            args=(resolved_root, stop_event),
            name="qualibug-connector-auto-sync",
            daemon=True,
        )
        _SUPERVISORS[key] = {
            "thread": thread,
            "stop_event": stop_event,
            "started_at_utc": _utc(),
        }
        thread.start()
        if not _ATEXIT_REGISTERED:
            atexit.register(stop_all_connector_auto_sync_supervisors)
            _ATEXIT_REGISTERED = True
    return {"enabled": True, "started": True, "root": key}


def stop_connector_auto_sync_supervisor(
    root: Path,
    *,
    join_timeout: float = 5.0,
) -> dict[str, Any]:
    key = str(root.resolve())
    with _STATE_LOCK:
        entry = _SUPERVISORS.pop(key, None)
    if not isinstance(entry, dict):
        return {"stopped": False, "root": key}
    stop_event = entry.get("stop_event")
    thread = entry.get("thread")
    if isinstance(stop_event, threading.Event):
        stop_event.set()
    if isinstance(thread, threading.Thread) and thread is not threading.current_thread():
        thread.join(max(0.0, float(join_timeout)))
    return {
        "stopped": True,
        "root": key,
        "thread_alive": bool(
            isinstance(thread, threading.Thread) and thread.is_alive()
        ),
    }


def stop_all_connector_auto_sync_supervisors() -> None:
    with _STATE_LOCK:
        roots = list(_SUPERVISORS)
    for value in roots:
        stop_connector_auto_sync_supervisor(Path(value), join_timeout=1.0)


__all__ = [
    "connector_auto_sync_status",
    "ensure_connector_auto_sync_supervisor",
    "recover_managed_connector_checkpoint",
    "run_connector_auto_sync_sweep",
    "run_managed_connector_sync",
    "stop_all_connector_auto_sync_supervisors",
    "stop_connector_auto_sync_supervisor",
    "test_managed_connector_connection",
    "validate_connector_checkpoint",
]
