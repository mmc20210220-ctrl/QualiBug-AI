"""Signed local-runner bridge for connector-owned online material.

The local runner is an execution boundary, not a second connector or knowledge store.  The
control plane issues a signed, bounded task; the runner resolves credentials only from its local
encrypted state, performs the adapter's read-only discovery/materialization, and returns a signed
snapshot.  Accepted snapshots are always reconciled by ``connector_sync_authority`` and therefore
enter the existing Source Occurrence mainline.

Only connector capability metadata, fingerprints, encrypted cursors, and bounded receipts are
persisted by the control plane.  A signed task/result may contain a cursor or materialized source
bytes while in transit, but those values are never written to the control-plane runner ledger.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .artifact_redactor import redact_and_validate, scan_for_secrets, write_json_redacted
from .connector_registry import (
    ConnectorAdapter,
    ConnectorManifest,
    ConnectorRegistryError,
    build_default_connector_registry,
)
from .connector_remote_lifecycle import reconcile_connector_remote_lifecycle
from .connector_sync_authority import (
    ConnectorSyncError,
    connector_snapshot_observation_index,
    list_connector_instances,
    load_connector_sync_run,
    sync_connector_snapshot_batch,
)
from .credential_crypto import decrypt, encrypt, is_encrypted
from .enterprise_knowledge_center._common import MAX_SOURCE_BYTES, ROOT
from .enterprise_knowledge_center._utils import _require_manage_actor
from .private_pilot_json_io import _read_json_object
from .real_project_onboarding import _safe_project_id
from .ssrf_guard import SsrfBlockedError, validate_url


LOCAL_RUNNER_REGISTRY_SCHEMA = "qualibug.local-runner-registry.v1"
LOCAL_RUNNER_TASK_SCHEMA = "qualibug.local-runner-task.v1"
LOCAL_RUNNER_RESULT_SCHEMA = "qualibug.local-runner-result.v1"
LOCAL_RUNNER_STATE_SCHEMA = "qualibug.local-runner-state.v1"
LOCAL_RUNNER_PROTOCOL_VERSION = "1"
LOCAL_RUNNER_EXECUTION_MODE = "LOCAL_RUNNER"
LOCAL_RUNNER_DEFAULT_TTL_SECONDS = 900
LOCAL_RUNNER_MAX_TTL_SECONDS = 86_400
LOCAL_RUNNER_MAX_OBSERVATIONS = 20_000
LOCAL_RUNNER_MAX_RESULT_ITEMS = 20_000

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_RESULT_MODES = {"SANITIZED_SNAPSHOT", "STRUCTURED_ONLY"}
_SYNC_MODES = {"FULL", "INCREMENTAL"}
_DELETION_POLICIES = {"RETAIN", "RETIRE_MISSING"}
_TASK_STATES = {"ISSUED", "RESULT_READY", "ACCEPTED", "RETRYABLE_FAILURE"}


class LocalRunnerError(RuntimeError):
    """A signed local-runner operation cannot safely continue."""


def _text(value: Any, limit: int = 1_000) -> str:
    return str(value or "").strip()[:limit]


def _identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise LocalRunnerError(f"local_runner_{field}_invalid")
    return result


def _utc_datetime(value: Any = None) -> datetime:
    if value is None or value == "":
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(_text(value, 80).replace("Z", "+00:00"))
        except ValueError as exc:
            raise LocalRunnerError("local_runner_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise LocalRunnerError("local_runner_timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: Any = None) -> str:
    return _utc_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LocalRunnerError("local_runner_payload_not_canonicalizable") from exc


def _payload_fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _ensure_encryption(root: Path) -> None:
    """Provision the deployment-local key through the existing credential authority."""
    from .private_pilot_credentials_patch import (
        CredentialEncryptionUnavailableError,
        ensure_local_credential_encryption_key,
    )

    try:
        ensure_local_credential_encryption_key(root)
    except CredentialEncryptionUnavailableError as exc:
        raise LocalRunnerError("local_runner_encryption_unavailable") from exc


def _encrypt_text(root: Path, value: str, field: str) -> str:
    _ensure_encryption(root)
    ciphertext = encrypt(str(value))
    if not ciphertext or not is_encrypted(ciphertext):
        raise LocalRunnerError(f"local_runner_{field}_plaintext_persistence_refused")
    return ciphertext


def _decrypt_text(root: Path, value: Any, field: str) -> str:
    ciphertext = _text(value, 20_000_000)
    if not ciphertext or not is_encrypted(ciphertext):
        raise LocalRunnerError(f"local_runner_{field}_ciphertext_invalid")
    _ensure_encryption(root)
    try:
        result = decrypt(ciphertext)
    except Exception as exc:
        raise LocalRunnerError(f"local_runner_{field}_decryption_failed") from exc
    if not result:
        raise LocalRunnerError(f"local_runner_{field}_empty")
    return result


def _decode_key(value: Any) -> bytes:
    if isinstance(value, bytes):
        key = value
    else:
        raw = _text(value, 4_000)
        if not raw:
            raise LocalRunnerError("local_runner_task_key_required")
        try:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except (ValueError, TypeError) as exc:
            raise LocalRunnerError("local_runner_task_key_invalid") from exc
    if len(key) < 32:
        raise LocalRunnerError("local_runner_task_key_too_short")
    return bytes(key)


def _encode_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def _task_key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:32]


def _seal(value: Mapping[str, Any], key: bytes) -> dict[str, Any]:
    payload = dict(value)
    payload.pop("signature", None)
    payload["signature"] = hmac.new(
        key,
        _canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return payload


def _verify_seal(value: Mapping[str, Any], key: bytes) -> None:
    if not isinstance(value, Mapping):
        raise LocalRunnerError("local_runner_signed_payload_must_be_object")
    signature = _text(value.get("signature"), 200)
    if not signature:
        raise LocalRunnerError("local_runner_signature_missing")
    unsigned = dict(value)
    unsigned.pop("signature", None)
    expected = hmac.new(
        key,
        _canonical_json(unsigned).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise LocalRunnerError("local_runner_signature_invalid")


def _normalize_host(value: Any) -> str:
    raw = _text(value, 512).lower().rstrip(".")
    if not raw or "*" in raw or "/" in raw or "?" in raw or "#" in raw:
        raise LocalRunnerError("local_runner_allowlist_host_invalid")
    if "://" in raw:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or parsed.path not in {"", "/"}:
            raise LocalRunnerError("local_runner_allowlist_host_invalid")
        raw = (parsed.hostname or "").lower().rstrip(".")
    else:
        parsed = urllib.parse.urlsplit("//" + raw)
        if parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise LocalRunnerError("local_runner_allowlist_host_invalid")
        raw = (parsed.hostname or "").lower().rstrip(".")
    if not raw or not _HOST_RE.fullmatch(raw):
        raise LocalRunnerError("local_runner_allowlist_host_invalid")
    return raw


def _normalize_hosts(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        raise LocalRunnerError("local_runner_allowlist_hosts_must_be_list")
    result: list[str] = []
    for item in values:
        host = _normalize_host(item)
        if host not in result:
            result.append(host)
    if not result:
        raise LocalRunnerError("local_runner_allowlist_hosts_required")
    if len(result) > 100:
        raise LocalRunnerError("local_runner_allowlist_hosts_too_many")
    return result


def _normalize_connector_types(value: Any) -> list[str]:
    if value in (None, ""):
        value = []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        raise LocalRunnerError("local_runner_connector_types_must_be_list")
    result: list[str] = []
    registry = build_default_connector_registry()
    if not values:
        values = [
            manifest.connector_type
            for manifest in registry.manifests()
            if manifest.local_runner_supported
        ]
    for item in values:
        connector_type = _identifier(item, "connector_type").lower()
        if connector_type in result:
            raise LocalRunnerError("local_runner_connector_type_duplicate")
        try:
            manifest = registry.manifest(connector_type)
        except ConnectorRegistryError as exc:
            raise LocalRunnerError(
                f"local_runner_connector_type_not_registered:{connector_type}"
            ) from exc
        if not manifest.local_runner_supported:
            raise LocalRunnerError(
                f"local_runner_connector_type_not_supported:{connector_type}"
            )
        result.append(connector_type)
    if not result:
        raise LocalRunnerError("local_runner_connector_types_required")
    return result


def _scope_object(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    raw = _text(value, 20_000)
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LocalRunnerError("local_runner_resource_scope_invalid_json") from exc
    return raw


def _collect_urls(value: Any) -> list[str]:
    urls: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for child in item.values():
                visit(child)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
            return
        if not isinstance(item, str):
            return
        text = item.strip()
        candidates = [text]
        if "http://" in text or "https://" in text:
            candidates = re.findall(r"https?://[^\s\"'<>]+", text, flags=re.IGNORECASE)
        for candidate in candidates:
            parsed = urllib.parse.urlsplit(candidate)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                continue
            normalized = candidate.rstrip(",.;")
            if normalized not in urls:
                urls.append(normalized)

    visit(value)
    return urls


def _validate_scope_hosts(resource_scope: Any, allowed_hosts: Sequence[str]) -> str:
    urls = _collect_urls(_scope_object(resource_scope))
    if not urls:
        raise LocalRunnerError("local_runner_resource_scope_host_missing")
    allowed = set(allowed_hosts)
    hosts: list[str] = []
    for url in urls:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in allowed:
            raise LocalRunnerError("LOCAL_RUNNER_HOST_NOT_ALLOWLISTED")
        try:
            validate_url(url, allow_internal=False, approved_host=host)
        except SsrfBlockedError as exc:
            raise LocalRunnerError("LOCAL_RUNNER_SCOPE_URL_BLOCKED") from exc
        if host not in hosts:
            hosts.append(host)
    if len(hosts) != 1:
        raise LocalRunnerError("LOCAL_RUNNER_MULTIPLE_SCOPE_HOSTS_UNSUPPORTED")
    return hosts[0]


def _control_path(project: str, root: Path) -> Path:
    return (
        root
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "local_runner_registry.json"
    )


def _control_default(project: str) -> dict[str, Any]:
    now = _timestamp()
    return {
        "schema": LOCAL_RUNNER_REGISTRY_SCHEMA,
        "project_id": project,
        "created_at_utc": now,
        "updated_at_utc": now,
        "runners": [],
        "tasks": [],
        "cursor_records": [],
        "governance": {
            "source_credentials_persisted": False,
            "raw_source_content_persisted": False,
            "raw_cursor_values_persisted": False,
            "task_signatures_required": True,
            "result_signatures_required": True,
            "runner_allowlist_exact_host_only": True,
            "structured_only_mode_supported": True,
            "single_connector_sync_authority": True,
        },
    }


def _load_control(project_id: str, root: Path) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    path = _control_path(project, root)
    raw = _read_json_object(path)
    if not raw:
        return _control_default(project)
    if raw.get("schema") != LOCAL_RUNNER_REGISTRY_SCHEMA:
        raise LocalRunnerError("local_runner_registry_schema_invalid")
    if _text(raw.get("project_id"), 160) != project:
        raise LocalRunnerError("local_runner_registry_project_mismatch")
    result = _control_default(project)
    result.update(raw)
    for key in ("runners", "tasks", "cursor_records"):
        if not isinstance(result.get(key), list):
            raise LocalRunnerError(f"local_runner_registry_{key}_invalid")
    return result


def _save_control(project: str, root: Path, registry: dict[str, Any]) -> None:
    registry["updated_at_utc"] = _timestamp()
    write_json_redacted(_control_path(project, root), registry)


def _runner_row(registry: Mapping[str, Any], runner_id: str) -> dict[str, Any]:
    row = next(
        (
            dict(item)
            for item in registry.get("runners") or []
            if isinstance(item, Mapping)
            and _text(item.get("runner_id"), 160) == runner_id
        ),
        None,
    )
    if row is None:
        raise LocalRunnerError("local_runner_not_registered")
    if _text(row.get("status"), 32) != "ACTIVE":
        raise LocalRunnerError("local_runner_not_active")
    return row


def _task_row(registry: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    row = next(
        (
            item
            for item in registry.get("tasks") or []
            if isinstance(item, Mapping)
            and _text(item.get("task_id"), 160) == task_id
        ),
        None,
    )
    if row is None:
        raise LocalRunnerError("local_runner_task_not_found")
    return row


def _cursor_row(
    registry: Mapping[str, Any],
    connector_instance_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in registry.get("cursor_records") or []
            if isinstance(item, Mapping)
            and _text(item.get("connector_instance_id"), 160)
            == connector_instance_id
        ),
        None,
    )


def _public_runner(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "qualibug.local-runner-registration.v1",
        "runner_id": _text(row.get("runner_id"), 160),
        "status": _text(row.get("status"), 32),
        "runner_version": _text(row.get("runner_version"), 80),
        "protocol_version": _text(row.get("protocol_version"), 40),
        "allowed_hosts": list(row.get("allowed_hosts") or []),
        "supported_connector_types": list(row.get("supported_connector_types") or []),
        "task_key_fingerprint": _text(row.get("task_key_fingerprint"), 64),
        "registered_at_utc": _text(row.get("registered_at_utc"), 80),
        "updated_at_utc": _text(row.get("updated_at_utc"), 80),
        "credentials_returned": False,
        "source_credentials_persisted": False,
    }


def _instance(project: str, connector: str, root: Path) -> dict[str, Any]:
    rows = list_connector_instances(project, root=root, include_disabled=True).get(
        "connector_instances"
    ) or []
    row = next(
        (
            dict(item)
            for item in rows
            if isinstance(item, Mapping)
            and _text(item.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if row is None:
        raise LocalRunnerError("connector_instance_not_registered")
    if _text(row.get("status"), 32) != "ACTIVE":
        raise LocalRunnerError("connector_instance_not_active")
    return row


def _manifest(connector_type: str) -> ConnectorManifest:
    try:
        return build_default_connector_registry().manifest(connector_type)
    except ConnectorRegistryError as exc:
        raise LocalRunnerError("local_runner_connector_type_not_registered") from exc


def _adapter(connector_type: str) -> ConnectorAdapter:
    try:
        adapter = build_default_connector_registry().get(connector_type)
    except ConnectorRegistryError as exc:
        raise LocalRunnerError("local_runner_connector_adapter_not_registered") from exc
    manifest = adapter.manifest()
    if not manifest.local_runner_supported:
        raise LocalRunnerError("local_runner_connector_type_not_supported")
    return adapter


def _connector_types_for_registration(value: Any) -> list[str]:
    return _normalize_connector_types(value)


def register_local_runner(
    project_id: str,
    *,
    runner_id: str,
    allowed_hosts: Sequence[str],
    supported_connector_types: Sequence[str] | None = None,
    runner_version: str = "1.0.0",
    root: Path | None = None,
    actor: Mapping[str, Any] | None = None,
    task_key: Any = None,
    rotate_task_key: bool = False,
) -> dict[str, Any]:
    """Register a runner and return a one-time bootstrap key only when necessary."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    runner = _identifier(runner_id, "runner_id")
    clean_actor = _require_manage_actor(dict(actor or {}))
    hosts = _normalize_hosts(allowed_hosts)
    types = _connector_types_for_registration(supported_connector_types)
    version = _text(runner_version, 80)
    if not version:
        raise LocalRunnerError("local_runner_version_required")
    _version_tuple(version)
    registry = _load_control(project, resolved_root)
    existing = next(
        (
            row
            for row in registry["runners"]
            if isinstance(row, dict) and _text(row.get("runner_id"), 160) == runner
        ),
        None,
    )
    now = _timestamp()
    bootstrap_key = ""
    if existing is None:
        key = _decode_key(task_key) if task_key is not None else secrets.token_bytes(32)
        bootstrap_key = _encode_key(key)
        existing = {
            "schema": "qualibug.local-runner-registration.v1",
            "runner_id": runner,
            "status": "ACTIVE",
            "runner_version": version,
            "protocol_version": LOCAL_RUNNER_PROTOCOL_VERSION,
            "allowed_hosts": hosts,
            "supported_connector_types": types,
            "task_key_ciphertext": _encrypt_text(resolved_root, bootstrap_key, "task_key"),
            "task_key_fingerprint": _task_key_fingerprint(key),
            "registered_at_utc": now,
            "updated_at_utc": now,
            "registered_by": clean_actor,
        }
        registry["runners"].append(existing)
    else:
        if rotate_task_key or task_key is not None:
            if any(
                _text(row.get("runner_id"), 160) == runner
                and _text(row.get("status"), 32) in {"ISSUED", "RESULT_READY"}
                for row in registry.get("tasks") or []
                if isinstance(row, Mapping)
            ):
                raise LocalRunnerError("local_runner_key_rotation_blocked_by_active_task")
            key = _decode_key(task_key) if task_key is not None else secrets.token_bytes(32)
            bootstrap_key = _encode_key(key)
            existing["task_key_ciphertext"] = _encrypt_text(
                resolved_root, bootstrap_key, "task_key"
            )
            existing["task_key_fingerprint"] = _task_key_fingerprint(key)
        existing.update(
            {
                "status": "ACTIVE",
                "runner_version": version,
                "allowed_hosts": hosts,
                "supported_connector_types": types,
                "updated_at_utc": now,
                "updated_by": clean_actor,
            }
        )
    _save_control(project, resolved_root, registry)
    public = _public_runner(existing)
    result: dict[str, Any] = {
        "ok": True,
        "runner": public,
        "bootstrap_key_returned": bool(bootstrap_key),
        "credentials_returned": False,
        "source_credentials_persisted": False,
    }
    if bootstrap_key:
        result["bootstrap"] = {
            "schema": "qualibug.local-runner-bootstrap.v1",
            "runner_id": runner,
            "task_key": bootstrap_key,
            "allowed_hosts": hosts,
            "supported_connector_types": types,
            "runner_version": version,
            "protocol_version": LOCAL_RUNNER_PROTOCOL_VERSION,
        }
    return result


def _profile_fingerprint(auth_mode: str, values: Mapping[str, str]) -> str:
    shape = {
        "auth_mode": auth_mode,
        "fields": sorted((str(key), len(str(value))) for key, value in values.items()),
    }
    return _payload_fingerprint(shape)


def _version_tuple(value: Any) -> tuple[int, ...]:
    raw = _text(value, 80)
    parts = raw.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise LocalRunnerError("local_runner_version_invalid")
    return tuple(int(part) for part in parts[:4])


def _normalize_source_profiles(
    source_profiles: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if source_profiles is None:
        return []
    if isinstance(source_profiles, Mapping):
        rows = []
        for connector_instance_id, profile in source_profiles.items():
            rows.append(
                {
                    "connector_instance_id": connector_instance_id,
                    "profile": profile,
                }
            )
    elif isinstance(source_profiles, Sequence) and not isinstance(source_profiles, (str, bytes)):
        rows = list(source_profiles)
    else:
        raise LocalRunnerError("local_runner_source_profiles_invalid")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise LocalRunnerError("local_runner_source_profile_invalid")
        connector = _identifier(row.get("connector_instance_id"), "connector_instance_id")
        profile = row.get("profile")
        if not isinstance(profile, Mapping):
            raise LocalRunnerError("local_runner_source_profile_must_be_object")
        result.append({"connector_instance_id": connector, "profile": dict(profile)})
    return result


def _state_path(runner_root: Path, runner_id: str) -> Path:
    return (
        runner_root.resolve()
        / "platform_workspace"
        / ".local_runner"
        / runner_id
        / "state.json"
    )


def _state_default(runner_id: str) -> dict[str, Any]:
    now = _timestamp()
    return {
        "schema": LOCAL_RUNNER_STATE_SCHEMA,
        "runner_id": runner_id,
        "created_at_utc": now,
        "updated_at_utc": now,
        "runner_version": "",
        "protocol_version": LOCAL_RUNNER_PROTOCOL_VERSION,
        "allowed_hosts": [],
        "supported_connector_types": [],
        "task_key_ciphertext": "",
        "source_profiles": [],
        "pending_tasks": [],
        "outbox_results": [],
        "governance": {
            "credentials_local_only": True,
            "credentials_encrypted_at_rest": True,
            "repository_code_executed": False,
            "build_or_test_scripts_executed": False,
            "writes_to_remote_sources": False,
            "result_upload_requires_control_acceptance": True,
        },
    }


def _load_state(runner_root: Path, runner_id: str) -> tuple[dict[str, Any], bytes]:
    runner = _identifier(runner_id, "runner_id")
    path = _state_path(runner_root, runner)
    raw = _read_json_object(path)
    if not raw:
        raise LocalRunnerError("local_runner_state_not_initialized")
    if raw.get("schema") != LOCAL_RUNNER_STATE_SCHEMA:
        raise LocalRunnerError("local_runner_state_schema_invalid")
    if _text(raw.get("runner_id"), 160) != runner:
        raise LocalRunnerError("local_runner_state_runner_mismatch")
    state = _state_default(runner)
    state.update(raw)
    for key in ("source_profiles", "pending_tasks", "outbox_results"):
        if not isinstance(state.get(key), list):
            raise LocalRunnerError(f"local_runner_state_{key}_invalid")
    key_b64 = _decrypt_text(runner_root.resolve(), state.get("task_key_ciphertext"), "task_key")
    key = _decode_key(key_b64)
    if _task_key_fingerprint(key) != _text(state.get("task_key_fingerprint"), 64):
        raise LocalRunnerError("local_runner_state_task_key_fingerprint_mismatch")
    return state, key


def _save_state(runner_root: Path, state: dict[str, Any]) -> None:
    state["updated_at_utc"] = _timestamp()
    write_json_redacted(_state_path(runner_root, _identifier(state.get("runner_id"), "runner_id")), state)


def initialize_local_runner(
    runner_root: Path,
    *,
    bootstrap: Mapping[str, Any],
    source_profiles: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Install one runner bootstrap and encrypt its source profiles locally."""
    if not isinstance(bootstrap, Mapping):
        raise LocalRunnerError("local_runner_bootstrap_invalid")
    bundle = bootstrap.get("bootstrap") if isinstance(bootstrap.get("bootstrap"), Mapping) else bootstrap
    runner = _identifier(bundle.get("runner_id"), "runner_id")
    root = Path(runner_root).resolve()
    path = _state_path(root, runner)
    existing_raw = _read_json_object(path)
    if existing_raw:
        existing, _ = _load_state(root, runner)
        existing_key = _decrypt_text(root, existing.get("task_key_ciphertext"), "task_key")
        incoming_key = _text(bundle.get("task_key"), 4_000)
        if incoming_key and incoming_key != existing_key:
            raise LocalRunnerError("local_runner_reinitialization_key_mismatch")
        key_b64 = existing_key
        state = existing
    else:
        key = _decode_key(bundle.get("task_key"))
        key_b64 = _encode_key(key)
        _ensure_encryption(root)
        state = _state_default(runner)
        state["task_key_ciphertext"] = _encrypt_text(root, key_b64, "task_key")
        state["task_key_fingerprint"] = _task_key_fingerprint(key)
    allowed_hosts = _normalize_hosts(bundle.get("allowed_hosts"))
    supported = _connector_types_for_registration(bundle.get("supported_connector_types"))
    state.update(
        {
            "runner_version": _text(bundle.get("runner_version"), 80),
            "protocol_version": _text(bundle.get("protocol_version"), 40),
            "allowed_hosts": allowed_hosts,
            "supported_connector_types": supported,
        }
    )
    _version_tuple(state["runner_version"])
    if state["protocol_version"] != LOCAL_RUNNER_PROTOCOL_VERSION:
        raise LocalRunnerError("local_runner_protocol_version_unsupported")

    profiles: list[dict[str, Any]] = []
    for row in _normalize_source_profiles(source_profiles):
        connector = row["connector_instance_id"]
        profile = dict(row["profile"])
        connector_type = _text(profile.pop("connector_type", ""), 160).lower()
        if not connector_type:
            raise LocalRunnerError("local_runner_source_profile_connector_type_required")
        if connector_type not in supported:
            raise LocalRunnerError("local_runner_source_profile_connector_type_not_supported")
        manifest = _manifest(connector_type)
        auth_mode = _text(profile.pop("auth_mode", ""), 80).lower()
        try:
            fields = manifest.credential_fields_for_auth_mode(auth_mode)
        except ConnectorRegistryError as exc:
            raise LocalRunnerError("local_runner_source_profile_auth_mode_invalid") from exc
        declared = {field.name for field in fields}
        unknown = sorted(set(profile) - declared)
        if unknown:
            raise LocalRunnerError("local_runner_source_profile_field_not_declared")
        encrypted_values: dict[str, str] = {}
        plain_values: dict[str, str] = {}
        for field in fields:
            value = _text(profile.get(field.name), 20_000)
            if not value:
                if field.required:
                    raise LocalRunnerError(
                        f"local_runner_source_profile_{field.name}_required"
                    )
                continue
            plain_values[field.name] = value
            encrypted_values[field.name] = _encrypt_text(
                root, value, f"source_profile_{field.name}"
            )
        profiles.append(
            {
                "connector_instance_id": connector,
                "connector_type": connector_type,
                "auth_mode": auth_mode,
                # Keep secret field names out of JSON object keys.  The shared artifact
                # redactor treats a key named ``token``/``password`` as a live secret even
                # when the value is already an authenticated ciphertext.
                "encrypted_values": [
                    {"field_name": name, "ciphertext": value}
                    for name, value in sorted(encrypted_values.items())
                ],
                "profile_fingerprint": _profile_fingerprint(auth_mode, plain_values),
                "credentials_configured": all(
                    bool(encrypted_values.get(field.name)) for field in fields if field.required
                ),
            }
        )
    if source_profiles is not None:
        state["source_profiles"] = profiles
    _save_state(root, state)
    return {
        "ok": True,
        "runner_id": runner,
        "runner_version": state["runner_version"],
        "protocol_version": state["protocol_version"],
        "source_profile_count": len(state.get("source_profiles") or []),
        "pending_task_count": len(state.get("pending_tasks") or []),
        "outbox_result_count": len(state.get("outbox_results") or []),
        "credentials_returned": False,
        "task_key_returned": False,
    }


def _local_profile(
    runner_root: Path,
    state: Mapping[str, Any],
    connector_instance_id: str,
    connector_type: str,
) -> dict[str, str]:
    row = next(
        (
            item
            for item in state.get("source_profiles") or []
            if isinstance(item, Mapping)
            and _text(item.get("connector_instance_id"), 160) == connector_instance_id
        ),
        None,
    )
    if row is None:
        raise LocalRunnerError("LOCAL_RUNNER_SOURCE_PROFILE_NOT_CONFIGURED")
    if _text(row.get("connector_type"), 160).lower() != connector_type:
        raise LocalRunnerError("LOCAL_RUNNER_SOURCE_PROFILE_CONNECTOR_TYPE_MISMATCH")
    profile = {"auth_mode": _text(row.get("auth_mode"), 80)}
    encrypted_values = row.get("encrypted_values") or []
    if not isinstance(encrypted_values, list):
        raise LocalRunnerError("local_runner_source_profile_encrypted_values_invalid")
    seen: set[str] = set()
    for raw_value in encrypted_values:
        if not isinstance(raw_value, Mapping):
            raise LocalRunnerError("local_runner_source_profile_encrypted_value_invalid")
        name = _identifier(raw_value.get("field_name"), "source_profile_field")
        if name in seen:
            raise LocalRunnerError("local_runner_source_profile_field_duplicate")
        seen.add(name)
        ciphertext = raw_value.get("ciphertext")
        profile[str(name)] = _decrypt_text(
            runner_root.resolve(), ciphertext, f"source_profile_{name}"
        )
    return profile


def _task_expiry(task: Mapping[str, Any], now: Any = None) -> None:
    issued = _utc_datetime(task.get("issued_at_utc"))
    expires = _utc_datetime(task.get("expires_at_utc"))
    current = _utc_datetime(now)
    if expires <= issued:
        raise LocalRunnerError("local_runner_task_expiry_invalid")
    if issued > current + timedelta(seconds=300):
        raise LocalRunnerError("local_runner_task_issued_in_future")
    if expires <= current:
        raise LocalRunnerError("local_runner_task_expired")


def _task_key_from_control(project: str, root: Path, runner: Mapping[str, Any]) -> bytes:
    key_b64 = _decrypt_text(root.resolve(), runner.get("task_key_ciphertext"), "task_key")
    key = _decode_key(key_b64)
    if _task_key_fingerprint(key) != _text(runner.get("task_key_fingerprint"), 64):
        raise LocalRunnerError("local_runner_control_task_key_fingerprint_mismatch")
    return key


def _safe_observations(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise LocalRunnerError("local_runner_previous_observations_invalid")
    if len(value) > LOCAL_RUNNER_MAX_OBSERVATIONS:
        raise LocalRunnerError("local_runner_previous_observations_too_many")
    result: dict[str, dict[str, Any]] = {}
    for key, raw in value.items():
        if not isinstance(raw, Mapping):
            raise LocalRunnerError("local_runner_previous_observation_invalid")
        result[_text(key, 4_000)] = dict(raw)
    return result


def issue_local_runner_task(
    project_id: str,
    *,
    connector_instance_id: str,
    runner_id: str,
    root: Path | None = None,
    actor: Mapping[str, Any] | None = None,
    result_mode: str = "SANITIZED_SNAPSHOT",
    ttl_seconds: int = LOCAL_RUNNER_DEFAULT_TTL_SECONDS,
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_resources: int = 5_000,
    timeout_seconds: float = 30.0,
    now_utc: Any = None,
) -> dict[str, Any]:
    """Create one immutable signed task without resolving any source credential."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    runner_key_id = _identifier(runner_id, "runner_id")
    clean_actor = _require_manage_actor(dict(actor or {}))
    mode = _text(result_mode, 40).upper()
    if mode not in _RESULT_MODES:
        raise LocalRunnerError("local_runner_result_mode_invalid")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
        raise LocalRunnerError("local_runner_task_ttl_invalid")
    if not 60 <= ttl_seconds <= LOCAL_RUNNER_MAX_TTL_SECONDS:
        raise LocalRunnerError("local_runner_task_ttl_out_of_range")
    policy = _text(deletion_policy, 40).upper() or "RETAIN"
    if policy not in _DELETION_POLICIES:
        raise LocalRunnerError("local_runner_deletion_policy_invalid")
    if not 0 <= int(max_retire_count) <= 10_000 or not 0.0 <= float(max_retire_ratio) <= 1.0:
        raise LocalRunnerError("local_runner_retirement_threshold_invalid")
    if not 1 <= int(max_resources) <= 100_000 or not 1.0 <= float(timeout_seconds) <= 300.0:
        raise LocalRunnerError("local_runner_execution_limit_invalid")

    registry = _load_control(project, resolved_root)
    runner = _runner_row(registry, runner_key_id)
    instance = _instance(project, connector, resolved_root)
    connector_type = _text(instance.get("connector_type"), 160).lower()
    manifest = _manifest(connector_type)
    if connector_type not in set(runner.get("supported_connector_types") or []):
        raise LocalRunnerError("local_runner_connector_type_not_enabled")
    if not manifest.local_runner_supported:
        raise LocalRunnerError("local_runner_connector_type_not_supported")
    resource_scope = _text(instance.get("resource_scope"), 20_000)
    if not resource_scope:
        raise LocalRunnerError("local_runner_resource_scope_required")
    scope_scan = scan_for_secrets({"resource_scope": resource_scope})
    if not scope_scan.get("safe"):
        raise LocalRunnerError("local_runner_resource_scope_contains_secret")
    approved_host = _validate_scope_hosts(resource_scope, runner.get("allowed_hosts") or [])

    if any(
        _text(row.get("connector_instance_id"), 160) == connector
        and _text(row.get("status"), 32) in {"ISSUED", "RESULT_READY"}
        for row in registry.get("tasks") or []
        if isinstance(row, Mapping)
    ):
        raise LocalRunnerError("local_runner_task_already_issued")

    instance_fingerprint = _text(instance.get("last_committed_cursor_fingerprint"), 128)
    cursor_record = _cursor_row(registry, connector)
    previous_cursor = ""
    if cursor_record is not None:
        previous_cursor = _decrypt_text(
            resolved_root, cursor_record.get("cursor_ciphertext"), "cursor"
        )
        expected = _text(cursor_record.get("cursor_fingerprint"), 128)
        if expected and hashlib.sha256(previous_cursor.encode("utf-8")).hexdigest() != expected:
            raise LocalRunnerError("local_runner_cursor_integrity_failed")
    if instance_fingerprint and not previous_cursor:
        raise LocalRunnerError("LOCAL_RUNNER_CURSOR_UNAVAILABLE")
    if instance_fingerprint and hashlib.sha256(previous_cursor.encode("utf-8")).hexdigest() != instance_fingerprint:
        raise LocalRunnerError("LOCAL_RUNNER_CURSOR_DIVERGED")

    observations = connector_snapshot_observation_index(
        project,
        connector_instance_id=connector,
        root=resolved_root,
    )
    safe_observations = _safe_observations(observations)
    issued = _utc_datetime(now_utc)
    expires = issued + timedelta(seconds=ttl_seconds)
    task_id = "lr-" + uuid.uuid4().hex
    task_base: dict[str, Any] = {
        "schema": LOCAL_RUNNER_TASK_SCHEMA,
        "protocol_version": LOCAL_RUNNER_PROTOCOL_VERSION,
        "execution_mode": LOCAL_RUNNER_EXECUTION_MODE,
        "task_id": task_id,
        "project_id": project,
        "runner_id": runner_key_id,
        "connector_instance_id": connector,
        "connector_type": connector_type,
        "connector_manifest_version": manifest.version,
        "connector_capability_contract_version": manifest.capability_contract_version,
        "resource_scope": resource_scope,
        "allowed_host": approved_host,
        "issued_at_utc": _timestamp(issued),
        "expires_at_utc": _timestamp(expires),
        "runner_version": _text(runner.get("runner_version"), 80),
        "min_runner_version": _text(runner.get("runner_version"), 80),
        "result_mode": mode,
        "previous_cursor": previous_cursor,
        "previous_cursor_fingerprint": hashlib.sha256(previous_cursor.encode("utf-8")).hexdigest()
        if previous_cursor
        else "",
        "previous_observations": safe_observations,
        "deletion_policy": policy,
        "max_retire_count": int(max_retire_count),
        "max_retire_ratio": float(max_retire_ratio),
        "max_resources": int(max_resources),
        "timeout_seconds": float(timeout_seconds),
        "source_credentials_in_task": False,
        "network_writes_allowed": False,
        "repository_code_execution_allowed": False,
    }
    key = _task_key_from_control(project, resolved_root, runner)
    task = _seal(task_base, key)
    task_fingerprint = _payload_fingerprint(task)
    registry["tasks"].append(
        {
            "schema": "qualibug.local-runner-task-ledger-row.v1",
            "task_id": task_id,
            "runner_id": runner_key_id,
            "project_id": project,
            "connector_instance_id": connector,
            "connector_type": connector_type,
            "task_fingerprint": task_fingerprint,
            "issued_at_utc": task["issued_at_utc"],
            "expires_at_utc": task["expires_at_utc"],
            "result_mode": mode,
            "previous_cursor_fingerprint": task["previous_cursor_fingerprint"],
            "status": "ISSUED",
            "issued_by": clean_actor,
            "sync_epoch_id": "lr-sync-" + task_id[3:],
            "raw_cursor_persisted": False,
            "source_content_persisted": False,
        }
    )
    _save_control(project, resolved_root, registry)
    return {
        "ok": True,
        "task": task,
        "task_fingerprint": task_fingerprint,
        "runner_id": runner_key_id,
        "connector_instance_id": connector,
        "source_credentials_returned": False,
        "source_content_returned": False,
        "raw_cursor_returned": True,
    }


def _wire_snapshot(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(snapshot, Mapping):
        raise LocalRunnerError("local_runner_materialized_snapshot_invalid")
    safe, redaction = redact_and_validate(dict(snapshot))
    if not isinstance(safe, dict):
        raise LocalRunnerError("local_runner_materialized_snapshot_invalid")
    content = safe.pop("content", None)
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        content_bytes = bytes(content)
    else:
        raise LocalRunnerError("local_runner_materialized_content_invalid")
    if len(content_bytes) > MAX_SOURCE_BYTES:
        raise LocalRunnerError("local_runner_materialized_content_too_large")
    safe["content_base64"] = base64.b64encode(content_bytes).decode("ascii")
    safe["content_encoding"] = "base64"
    return safe, redaction


def _descriptor_coverage(
    descriptor: Mapping[str, Any],
    *,
    reason_code: str,
    capability: Any = None,
) -> dict[str, Any]:
    remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
    kind = _text(descriptor.get("resource_kind"), 80) or "document"
    if not remote_id:
        raise LocalRunnerError("local_runner_descriptor_remote_id_missing")
    metadata = dict(descriptor.get("metadata") or {})
    if capability is not None:
        metadata.update(
            {
                "capability_contract_version": _text(
                    getattr(capability, "contract_version", ""), 160
                ),
                "capability_disposition": _text(
                    getattr(getattr(capability, "disposition", None), "value", ""), 80
                ),
            }
        )
    return {
        "remote_resource_id": remote_id,
        "resource_kind": kind,
        "state": "UNSUPPORTED",
        "reason_code": _identifier(reason_code, "coverage_reason_code"),
        "remote_object_type": _text(
            descriptor.get("obj_type")
            or getattr(capability, "remote_object_type", ""),
            80,
        ),
        "display_title": _text(descriptor.get("display_title"), 300),
        "retry_trigger": _text(
            getattr(capability, "retry_trigger", "ADAPTER_CAPABILITY_CHANGE"), 160
        )
        or "ADAPTER_CAPABILITY_CHANGE",
        "capability_contract_version": _text(
            getattr(capability, "contract_version", ""), 160
        ),
        "metadata": metadata,
    }


def _lifecycle_resource(descriptor: Mapping[str, Any], capability: Any) -> dict[str, Any]:
    return {
        "remote_resource_id": _text(descriptor.get("remote_resource_id"), 4_000),
        "resource_kind": _text(descriptor.get("resource_kind"), 160),
        "display_title": _text(descriptor.get("display_title"), 300),
        "parent_remote_id": _text(descriptor.get("parent_remote_id"), 4_000),
        "remote_space_id": _text(descriptor.get("branch_ref"), 600),
        "remote_revision": _text(descriptor.get("remote_revision"), 240),
        "materialization_state": (
            "UNSUPPORTED"
            if bool(getattr(capability, "observable_unsupported", False))
            else "MATERIALIZABLE"
        ),
    }


def _execute_task(
    runner_root: Path,
    state: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    transport: Any = None,
    sleeper: Callable[[float], None] | None = None,
    now_utc: Any = None,
) -> dict[str, Any]:
    runner = _identifier(task.get("runner_id"), "runner_id")
    connector = _identifier(task.get("connector_instance_id"), "connector_instance_id")
    connector_type = _identifier(task.get("connector_type"), "connector_type").lower()
    mode = _text(task.get("result_mode"), 40).upper()
    if mode not in _RESULT_MODES:
        raise LocalRunnerError("local_runner_result_mode_invalid")
    if _text(task.get("execution_mode"), 40) != LOCAL_RUNNER_EXECUTION_MODE:
        raise LocalRunnerError("local_runner_execution_mode_invalid")
    _task_expiry(task, now_utc)
    if connector_type not in set(state.get("supported_connector_types") or []):
        raise LocalRunnerError("LOCAL_RUNNER_CONNECTOR_TYPE_NOT_ENABLED")
    approved_host = _validate_scope_hosts(
        task.get("resource_scope"), state.get("allowed_hosts") or []
    )
    if approved_host != _text(task.get("allowed_host"), 512).lower().rstrip("."):
        raise LocalRunnerError("local_runner_task_allowlist_binding_mismatch")
    adapter = _adapter(connector_type)
    profile = _local_profile(runner_root, state, connector, connector_type)
    previous_observations = _safe_observations(task.get("previous_observations") or {})
    context: dict[str, Any] = {
        "project_id": _text(task.get("project_id"), 160),
        "connector_instance_id": connector,
        "connector_type": connector_type,
        "resource_scope": task.get("resource_scope"),
        "connection_profile": profile,
        "_resolved_connection_profile": profile,
        "previous_observations": previous_observations,
        "approved_host": approved_host,
        "approved_hosts": list(state.get("allowed_hosts") or []),
        "transport": transport,
        "timeout": float(task.get("timeout_seconds") or 30.0),
        "sleeper": sleeper,
        "platform_event_id": "LOCAL_RUNNER",
    }
    discovery = adapter.discover(
        context,
        _text(task.get("previous_cursor"), 20_000_000),
    )
    if not isinstance(discovery, Mapping):
        raise LocalRunnerError("local_runner_discovery_result_invalid")
    descriptors = discovery.get("descriptors") or []
    if not isinstance(descriptors, list) or len(descriptors) > LOCAL_RUNNER_MAX_RESULT_ITEMS:
        raise LocalRunnerError("local_runner_discovery_descriptors_invalid")
    coverage: list[dict[str, Any]] = []
    raw_coverage = dict(discovery.get("coverage") or {}).get("observations") or []
    if not isinstance(raw_coverage, list):
        raise LocalRunnerError("local_runner_discovery_coverage_invalid")
    coverage.extend(dict(row) for row in raw_coverage if isinstance(row, Mapping))
    items: list[dict[str, Any]] = []
    lifecycle_resources: list[dict[str, Any]] = []
    represented: set[str] = set()
    redaction_events: list[dict[str, Any]] = []
    for raw_descriptor in descriptors:
        if not isinstance(raw_descriptor, Mapping):
            raise LocalRunnerError("local_runner_descriptor_invalid")
        descriptor = dict(raw_descriptor)
        remote_id = _text(descriptor.get("remote_resource_id"), 4_000)
        if not remote_id:
            raise LocalRunnerError("local_runner_descriptor_remote_id_missing")
        represented.add(remote_id)
        capability = adapter.classify_resource(descriptor)
        lifecycle_resources.append(_lifecycle_resource(descriptor, capability))
        if bool(getattr(capability, "observable_unsupported", False)):
            coverage.append(
                _descriptor_coverage(
                    descriptor,
                    reason_code=_text(getattr(capability, "reason_code", ""), 160)
                    or "LOCAL_RUNNER_RESOURCE_UNSUPPORTED",
                    capability=capability,
                )
            )
            continue
        if not bool(getattr(capability, "materializable", False)):
            raise LocalRunnerError("local_runner_descriptor_not_materializable")
        if mode == "STRUCTURED_ONLY":
            coverage.append(
                _descriptor_coverage(
                    descriptor,
                    reason_code="LOCAL_RUNNER_STRUCTURED_ONLY_CONTENT_OMITTED",
                    capability=capability,
                )
            )
            continue
        snapshot = adapter.materialize(context, descriptor)
        wire, redaction = _wire_snapshot(snapshot)
        items.append(wire)
        redaction_events.extend(list(redaction.get("redaction", {}).get("events") or [])[:20])

    lifecycle = [
        dict(row)
        for row in discovery.get("lifecycle") or []
        if isinstance(row, Mapping)
    ]
    removed = {
        _text(row.get("remote_resource_id"), 4_000)
        for row in lifecycle
        if _text(row.get("event"), 120)
        in {"GIT_FILE_DELETED", "GIT_RESOURCE_REMOVED"}
    }
    unchanged: list[dict[str, Any]] = []
    for remote_id, observation in previous_observations.items():
        if remote_id in represented or remote_id in removed:
            continue
        metadata = dict(observation.get("source_metadata") or {})
        unchanged.append(
            {
                "remote_resource_id": remote_id,
                "resource_kind": _text(metadata.get("resource_kind"), 80) or "document",
                "metadata": metadata,
            }
        )

    sync_mode = _text(discovery.get("sync_mode"), 32).upper() or "INCREMENTAL"
    if sync_mode not in _SYNC_MODES:
        raise LocalRunnerError("local_runner_sync_mode_invalid")
    snapshot_complete = bool(discovery.get("snapshot_complete")) and not coverage
    next_cursor = "" if mode == "STRUCTURED_ONLY" else _text(
        discovery.get("next_cursor"), 20_000_000
    )
    result_base: dict[str, Any] = {
        "schema": LOCAL_RUNNER_RESULT_SCHEMA,
        "protocol_version": LOCAL_RUNNER_PROTOCOL_VERSION,
        "execution_mode": LOCAL_RUNNER_EXECUTION_MODE,
        "task_id": _text(task.get("task_id"), 160),
        "task_fingerprint": _payload_fingerprint(task),
        "runner_id": runner,
        "project_id": _text(task.get("project_id"), 160),
        "connector_instance_id": connector,
        "connector_type": connector_type,
        "result_mode": mode,
        "issued_at_utc": _timestamp(),
        "expires_at_utc": _text(task.get("expires_at_utc"), 80),
        "previous_cursor": _text(task.get("previous_cursor"), 20_000_000),
        "next_cursor": next_cursor,
        "sync_mode": sync_mode,
        "snapshot_complete": snapshot_complete if mode != "STRUCTURED_ONLY" else False,
        "items": items,
        "unchanged_observations": unchanged,
        "coverage_observations": coverage,
        "lifecycle_resources": lifecycle_resources,
        "lifecycle_events": lifecycle,
        "discovered_resource_count": len(descriptors),
        "materialized_resource_count": len(items),
        "unchanged_resource_count": len(unchanged),
        "coverage_observation_count": len(coverage),
        "source_content_returned": bool(items),
        "credentials_persisted": False,
        "repository_code_executed": False,
        "build_or_test_scripts_executed": False,
        "structured_only_content_omitted": mode == "STRUCTURED_ONLY",
        "redaction_event_count": len(redaction_events),
        "redaction_events": redaction_events[:100],
    }
    safe_result, _ = redact_and_validate(result_base)
    if not isinstance(safe_result, dict):
        raise LocalRunnerError("local_runner_result_redaction_invalid")
    key = _decode_key(_decrypt_text(runner_root.resolve(), state.get("task_key_ciphertext"), "task_key"))
    return _seal(safe_result, key)


def execute_local_runner_task(
    task: Mapping[str, Any],
    *,
    runner_root: Path,
    transport: Any = None,
    sleeper: Callable[[float], None] | None = None,
    now_utc: Any = None,
) -> dict[str, Any]:
    """Verify, queue, execute, and durably place one signed result in the outbox."""
    if not isinstance(task, Mapping):
        raise LocalRunnerError("local_runner_task_invalid")
    runner_id = _identifier(task.get("runner_id"), "runner_id")
    state, key = _load_state(Path(runner_root), runner_id)
    _verify_seal(task, key)
    if _text(task.get("schema"), 100) != LOCAL_RUNNER_TASK_SCHEMA:
        raise LocalRunnerError("local_runner_task_schema_invalid")
    if _text(task.get("protocol_version"), 40) != LOCAL_RUNNER_PROTOCOL_VERSION:
        raise LocalRunnerError("local_runner_protocol_version_unsupported")
    if _text(task.get("runner_id"), 160) != _text(state.get("runner_id"), 160):
        raise LocalRunnerError("local_runner_task_runner_mismatch")
    if _version_tuple(state.get("runner_version")) < _version_tuple(
        task.get("min_runner_version")
    ):
        raise LocalRunnerError("LOCAL_RUNNER_VERSION_TOO_OLD")
    task_id = _identifier(task.get("task_id"), "task_id")
    task_fingerprint = _payload_fingerprint(task)
    for row in state.get("outbox_results") or []:
        if isinstance(row, Mapping) and _text(row.get("task_id"), 160) == task_id:
            if _text(row.get("task_fingerprint"), 128) != task_fingerprint:
                raise LocalRunnerError("local_runner_task_fingerprint_conflict")
            result_json = _decrypt_text(
                Path(runner_root).resolve(), row.get("result_ciphertext"), "result"
            )
            try:
                result = json.loads(result_json)
            except json.JSONDecodeError as exc:
                raise LocalRunnerError("local_runner_outbox_result_invalid") from exc
            if not isinstance(result, dict):
                raise LocalRunnerError("local_runner_outbox_result_invalid")
            return result

    pending = next(
        (
            row
            for row in state.get("pending_tasks") or []
            if isinstance(row, Mapping) and _text(row.get("task_id"), 160) == task_id
        ),
        None,
    )
    if pending is not None and _text(pending.get("task_fingerprint"), 128) != task_fingerprint:
        raise LocalRunnerError("local_runner_task_fingerprint_conflict")
    if pending is None:
        state["pending_tasks"].append(
            {
                "task_id": task_id,
                "task_fingerprint": task_fingerprint,
                "state": "ISSUED",
                "task_ciphertext": _encrypt_text(
                    Path(runner_root).resolve(), _canonical_json(dict(task)), "task"
                ),
                "attempt_count": 0,
                "queued_at_utc": _timestamp(),
                "last_error_code": "",
            }
        )
    _save_state(Path(runner_root), state)
    try:
        result = _execute_task(
            Path(runner_root),
            state,
            task,
            transport=transport,
            sleeper=sleeper,
            now_utc=now_utc,
        )
    except Exception as exc:
        current = next(
            row
            for row in state["pending_tasks"]
            if _text(row.get("task_id"), 160) == task_id
        )
        current["state"] = "RETRYABLE_FAILURE"
        current["attempt_count"] = int(current.get("attempt_count") or 0) + 1
        current["last_error_code"] = type(exc).__name__ + ":" + _text(
            str(exc).split(":", 1)[0], 120
        )
        current["last_error_at_utc"] = _timestamp()
        _save_state(Path(runner_root), state)
        raise
    result_fingerprint = _payload_fingerprint(result)
    state["outbox_results"].append(
        {
            "task_id": task_id,
            "task_fingerprint": task_fingerprint,
            "result_fingerprint": result_fingerprint,
            "result_ciphertext": _encrypt_text(
                Path(runner_root).resolve(), _canonical_json(result), "result"
            ),
            "created_at_utc": _timestamp(),
            "acknowledged": False,
        }
    )
    current = next(
        row for row in state["pending_tasks"] if _text(row.get("task_id"), 160) == task_id
    )
    current.update(
        {
            "state": "RESULT_READY",
            "result_fingerprint": result_fingerprint,
            "completed_at_utc": _timestamp(),
        }
    )
    _save_state(Path(runner_root), state)
    return result


def accept_local_runner_result(
    project_id: str,
    *,
    result: Mapping[str, Any],
    root: Path | None = None,
    actor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify one result and reconcile it through the existing snapshot authority."""
    if not isinstance(result, Mapping):
        raise LocalRunnerError("local_runner_result_invalid")
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    clean_actor = _require_manage_actor(dict(actor or {}))
    runner_id = _identifier(result.get("runner_id"), "runner_id")
    task_id = _identifier(result.get("task_id"), "task_id")
    registry = _load_control(project, resolved_root)
    runner = _runner_row(registry, runner_id)
    key = _task_key_from_control(project, resolved_root, runner)
    _verify_seal(result, key)
    if _text(result.get("schema"), 100) != LOCAL_RUNNER_RESULT_SCHEMA:
        raise LocalRunnerError("local_runner_result_schema_invalid")
    if _text(result.get("protocol_version"), 40) != LOCAL_RUNNER_PROTOCOL_VERSION:
        raise LocalRunnerError("local_runner_protocol_version_unsupported")
    task = _task_row(registry, task_id)
    if _text(task.get("runner_id"), 160) != runner_id:
        raise LocalRunnerError("local_runner_result_runner_mismatch")
    result_fingerprint = _payload_fingerprint(result)
    prior_status = _text(task.get("status"), 40)
    if prior_status == "ACCEPTED":
        if _text(task.get("result_fingerprint"), 128) != result_fingerprint:
            raise LocalRunnerError("local_runner_result_conflicts_with_accepted_task")
        receipt = dict(task.get("acceptance_receipt") or {})
        receipt["idempotent_replay"] = True
        return receipt
    if prior_status not in {"ISSUED", "RESULT_READY", "RETRYABLE_FAILURE"}:
        raise LocalRunnerError("local_runner_task_not_accepting_result")
    _task_expiry(task)
    if _text(result.get("project_id"), 160) != project:
        raise LocalRunnerError("local_runner_result_project_mismatch")
    connector = _identifier(result.get("connector_instance_id"), "connector_instance_id")
    if _text(task.get("connector_instance_id"), 160) != connector:
        raise LocalRunnerError("local_runner_result_connector_mismatch")
    if _text(result.get("connector_type"), 160).lower() != _text(
        task.get("connector_type"), 160
    ).lower():
        raise LocalRunnerError("local_runner_result_connector_type_mismatch")
    mode = _text(result.get("result_mode"), 40).upper()
    if mode not in _RESULT_MODES or mode != _text(task.get("result_mode"), 40).upper():
        raise LocalRunnerError("local_runner_result_mode_mismatch")
    task_fingerprint = _text(result.get("task_fingerprint"), 128)
    if task_fingerprint != _text(task.get("task_fingerprint"), 128):
        raise LocalRunnerError("local_runner_result_task_fingerprint_mismatch")
    previous_cursor = _text(result.get("previous_cursor"), 20_000_000)
    previous_fingerprint = (
        hashlib.sha256(previous_cursor.encode("utf-8")).hexdigest()
        if previous_cursor
        else ""
    )
    if previous_fingerprint != _text(task.get("previous_cursor_fingerprint"), 128):
        raise LocalRunnerError("local_runner_result_previous_cursor_mismatch")

    # The runner must have performed its own redaction before signing.  A signed payload that
    # still changes at the upload boundary is rejected rather than silently rewritten.
    safe_result, _ = redact_and_validate(dict(result))
    if safe_result != dict(result):
        raise LocalRunnerError("local_runner_result_redaction_required_before_upload")

    raw_items = result.get("items") or []
    if not isinstance(raw_items, list) or len(raw_items) > LOCAL_RUNNER_MAX_RESULT_ITEMS:
        raise LocalRunnerError("local_runner_result_items_invalid")
    if mode == "STRUCTURED_ONLY" and raw_items:
        raise LocalRunnerError("local_runner_structured_only_contains_content")
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise LocalRunnerError("local_runner_result_item_invalid")
        row = dict(raw)
        if "content" in row or _text(row.get("content_encoding"), 40) != "base64":
            raise LocalRunnerError("local_runner_result_content_encoding_invalid")
        encoded = row.pop("content_base64", None)
        if not isinstance(encoded, str):
            raise LocalRunnerError("local_runner_result_content_missing")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise LocalRunnerError("local_runner_result_content_base64_invalid") from exc
        if len(content) > MAX_SOURCE_BYTES:
            raise LocalRunnerError("local_runner_result_content_too_large")
        row.pop("content_encoding", None)
        row["content"] = content
        safe_item, _ = redact_and_validate(row)
        if not isinstance(safe_item, dict):
            raise LocalRunnerError("local_runner_result_item_invalid")
        items.append(safe_item)

    raw_unchanged = result.get("unchanged_observations") or []
    raw_coverage = result.get("coverage_observations") or []
    if not isinstance(raw_unchanged, list) or not isinstance(raw_coverage, list):
        raise LocalRunnerError("local_runner_result_observations_invalid")
    next_cursor = _text(result.get("next_cursor"), 20_000_000)
    if mode == "STRUCTURED_ONLY" and next_cursor:
        raise LocalRunnerError("local_runner_structured_only_cursor_advance_forbidden")
    snapshot_complete = bool(result.get("snapshot_complete")) and mode != "STRUCTURED_ONLY"
    sync_mode = _text(result.get("sync_mode"), 32).upper()
    if sync_mode not in _SYNC_MODES:
        raise LocalRunnerError("local_runner_result_sync_mode_invalid")
    sync_epoch_id = _text(task.get("sync_epoch_id"), 160)
    if not sync_epoch_id:
        raise LocalRunnerError("local_runner_sync_epoch_missing")
    try:
        run = load_connector_sync_run(
            project,
            connector_instance_id=connector,
            sync_epoch_id=sync_epoch_id,
            root=resolved_root,
        )
        if _text(run.get("status"), 32) == "RUNNING":
            raise LocalRunnerError("local_runner_sync_epoch_recovery_required")
    except KeyError:
        run = sync_connector_snapshot_batch(
            project,
            connector_instance_id=connector,
            items=items,
            unchanged_observations=[dict(row) for row in raw_unchanged],
            coverage_observations=[dict(row) for row in raw_coverage],
            root=resolved_root,
            actor=clean_actor,
            sync_mode=sync_mode,
            previous_cursor=previous_cursor,
            next_cursor=next_cursor if mode != "STRUCTURED_ONLY" else "",
            deletion_policy="RETAIN",
            snapshot_complete=snapshot_complete,
            max_retire_count=10_000,
            max_retire_ratio=1.0,
            sync_epoch_id=sync_epoch_id,
        )

    lifecycle_result: dict[str, Any] = {
        "status": "NOT_REQUESTED",
        "evidence_persisted": False,
    }
    lifecycle_resources = result.get("lifecycle_resources") or []
    if mode != "STRUCTURED_ONLY" and isinstance(lifecycle_resources, list) and lifecycle_resources:
        if run.get("status") == "COMPLETE":
            persisted = run.get("remote_lifecycle")
            if isinstance(persisted, Mapping):
                lifecycle_result = dict(persisted)
                lifecycle_result["evidence_persisted"] = True
            else:
                lifecycle_result = reconcile_connector_remote_lifecycle(
                    project,
                    connector_instance_id=connector,
                    present_resources=[dict(row) for row in lifecycle_resources],
                    sync_epoch_id=sync_epoch_id,
                    root=resolved_root,
                    actor=clean_actor,
                    deletion_policy=_text(task.get("deletion_policy"), 40) or "RETAIN",
                    authoritative_snapshot_complete=snapshot_complete,
                    max_retire_count=int(task.get("max_retire_count") or 100),
                    max_retire_ratio=float(task.get("max_retire_ratio") or 0.25),
                )
                lifecycle_result["evidence_persisted"] = True
                run = load_connector_sync_run(
                    project,
                    connector_instance_id=connector,
                    sync_epoch_id=sync_epoch_id,
                    root=resolved_root,
                )

    if run.get("cursor_checkpoint_committed") is True and mode != "STRUCTURED_ONLY":
        if not next_cursor:
            raise LocalRunnerError("local_runner_committed_cursor_missing")
        cursor_hash = hashlib.sha256(next_cursor.encode("utf-8")).hexdigest()
        existing_cursor = _cursor_row(registry, connector)
        if existing_cursor is None:
            registry["cursor_records"].append(
                {
                    "schema": "qualibug.local-runner-cursor.v1",
                    "connector_instance_id": connector,
                    "runner_id": runner_id,
                    "cursor_ciphertext": _encrypt_text(
                        resolved_root, next_cursor, "cursor"
                    ),
                    "cursor_fingerprint": cursor_hash,
                    "updated_at_utc": _timestamp(),
                    "raw_cursor_persisted": False,
                }
            )
        else:
            existing_cursor.update(
                {
                    "runner_id": runner_id,
                    "cursor_ciphertext": _encrypt_text(
                        resolved_root, next_cursor, "cursor"
                    ),
                    "cursor_fingerprint": cursor_hash,
                    "updated_at_utc": _timestamp(),
                    "raw_cursor_persisted": False,
                }
            )

    receipt = {
        "schema": "qualibug.local-runner-acceptance.v1",
        "ok": True,
        "accepted": True,
        "idempotent_replay": False,
        "project_id": project,
        "runner_id": runner_id,
        "task_id": task_id,
        "connector_instance_id": connector,
        "connector_type": _text(result.get("connector_type"), 160),
        "result_fingerprint": result_fingerprint,
        "sync_epoch_id": sync_epoch_id,
        "sync_status": _text(run.get("status"), 32),
        "knowledge_coverage_status": _text(run.get("knowledge_coverage_status"), 80),
        "knowledge_coverage_complete": bool(run.get("knowledge_coverage_complete")),
        "materialized_success_count": int(run.get("materialized_success_count") or 0),
        "unchanged_success_count": int(run.get("unchanged_success_count") or 0),
        "coverage_observation_count": int(run.get("coverage_observation_count") or 0),
        "failure_count": int(run.get("failure_count") or 0),
        "cursor_checkpoint_committed": bool(run.get("cursor_checkpoint_committed")),
        "lifecycle_status": _text(lifecycle_result.get("status"), 80),
        "lifecycle_evidence_persisted": bool(lifecycle_result.get("evidence_persisted")),
        "source_content_returned": False,
        "source_credentials_returned": False,
        "raw_cursor_returned": False,
        "retry_required": _text(run.get("status"), 32) != "COMPLETE"
        or not bool(run.get("knowledge_coverage_complete")),
    }
    task.update(
        {
            "status": "ACCEPTED",
            "result_fingerprint": result_fingerprint,
            "accepted_at_utc": _timestamp(),
            "acceptance_receipt": receipt,
            "raw_cursor_persisted": False,
            "source_content_persisted": False,
        }
    )
    _save_control(project, resolved_root, registry)
    return receipt


def acknowledge_local_runner_result(
    runner_root: Path,
    *,
    runner_id: str,
    task_id: str,
    acceptance: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove an outbox result only after the control plane accepted that exact result."""
    if not isinstance(acceptance, Mapping) or acceptance.get("accepted") is not True:
        raise LocalRunnerError("local_runner_ack_requires_control_acceptance")
    state, _ = _load_state(Path(runner_root), runner_id)
    task = _identifier(task_id, "task_id")
    row = next(
        (
            item
            for item in state.get("outbox_results") or []
            if isinstance(item, Mapping) and _text(item.get("task_id"), 160) == task
        ),
        None,
    )
    if row is None:
        raise LocalRunnerError("local_runner_outbox_result_not_found")
    expected = _text(row.get("result_fingerprint"), 128)
    if expected != _text(acceptance.get("result_fingerprint"), 128):
        raise LocalRunnerError("local_runner_ack_result_fingerprint_mismatch")
    state["outbox_results"] = [
        item
        for item in state["outbox_results"]
        if not (isinstance(item, Mapping) and _text(item.get("task_id"), 160) == task)
    ]
    for item in state.get("pending_tasks") or []:
        if isinstance(item, dict) and _text(item.get("task_id"), 160) == task:
            item.update(
                {
                    "state": "ACKED",
                    "acknowledged_at_utc": _timestamp(),
                }
            )
    _save_state(Path(runner_root), state)
    return {
        "ok": True,
        "task_id": task,
        "result_fingerprint": expected,
        "outbox_removed": True,
        "credentials_returned": False,
    }


def local_runner_status(runner_root: Path, *, runner_id: str) -> dict[str, Any]:
    state, _ = _load_state(Path(runner_root), runner_id)
    return {
        "schema": "qualibug.local-runner-status.v1",
        "runner_id": _text(state.get("runner_id"), 160),
        "runner_version": _text(state.get("runner_version"), 80),
        "protocol_version": _text(state.get("protocol_version"), 40),
        "allowed_hosts": list(state.get("allowed_hosts") or []),
        "supported_connector_types": list(state.get("supported_connector_types") or []),
        "source_profile_count": len(state.get("source_profiles") or []),
        "pending_task_count": len(
            [
                row
                for row in state.get("pending_tasks") or []
                if isinstance(row, Mapping) and _text(row.get("state"), 40) != "ACKED"
            ]
        ),
        "outbox_result_count": len(state.get("outbox_results") or []),
        "credentials_returned": False,
        "source_credentials_persisted": True,
    }


def list_local_runner_registrations(
    project_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Project-scoped control-plane projection without keys, cursors, or source content."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    registry = _load_control(project, resolved_root)
    runners = []
    for raw in registry.get("runners") or []:
        if not isinstance(raw, Mapping):
            continue
        runner = _public_runner(raw)
        runner_id = _text(raw.get("runner_id"), 160)
        task_rows = [
            row
            for row in registry.get("tasks") or []
            if isinstance(row, Mapping)
            and _text(row.get("runner_id"), 160) == runner_id
        ]
        runner["task_count"] = len(task_rows)
        runner["active_task_count"] = sum(
            _text(row.get("status"), 40) in {"ISSUED", "RESULT_READY"}
            for row in task_rows
        )
        runner["last_task_status"] = _text(
            max(
                task_rows,
                key=lambda row: _text(row.get("issued_at_utc"), 80),
                default={},
            ).get("status"),
            40,
        )
        runners.append(runner)
    return {
        "schema": "qualibug.local-runner-control-status.v1",
        "project_id": project,
        "runners": runners,
        "runner_count": len(runners),
        "source_content_returned": False,
        "source_credentials_returned": False,
        "raw_cursor_returned": False,
    }


def _read_json_file(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalRunnerError(f"local_runner_json_file_invalid:{path}") from exc
    return value


def _cli_actor(args: argparse.Namespace) -> dict[str, str]:
    return {
        "name": _text(getattr(args, "actor_name", "local-runner-cli"), 160)
        or "local-runner-cli",
        "role": _text(getattr(args, "actor_role", "qa_lead"), 80) or "qa_lead",
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Small file-exchange CLI; network transport remains adapter-owned."""
    parser = argparse.ArgumentParser(prog="qualibug-local-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    register = subparsers.add_parser("register")
    register.add_argument("--control-root", type=Path, required=True)
    register.add_argument("--project", required=True)
    register.add_argument("--runner-id", required=True)
    register.add_argument("--allowed-host", action="append", required=True)
    register.add_argument("--connector-type", action="append", default=[])
    register.add_argument("--runner-version", default="1.0.0")
    register.add_argument("--actor-name", default="local-runner-cli")
    register.add_argument("--actor-role", default="qa_lead")
    register.add_argument("--output", type=Path)

    initialize = subparsers.add_parser("init")
    initialize.add_argument("--root", type=Path, required=True)
    initialize.add_argument("--bootstrap-file", type=Path, required=True)
    initialize.add_argument("--profiles-file", type=Path)

    execute = subparsers.add_parser("execute")
    execute.add_argument("--root", type=Path, required=True)
    execute.add_argument("--task-file", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)

    status = subparsers.add_parser("status")
    status.add_argument("--root", type=Path, required=True)
    status.add_argument("--runner-id", required=True)

    args = parser.parse_args(argv)
    if args.command == "register":
        result = register_local_runner(
            args.project,
            runner_id=args.runner_id,
            allowed_hosts=args.allowed_host,
            supported_connector_types=args.connector_type,
            runner_version=args.runner_version,
            root=args.control_root,
            actor=_cli_actor(args),
        )
        if args.output:
            write_json_redacted(args.output, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "init":
        bootstrap = _read_json_file(args.bootstrap_file)
        profiles = _read_json_file(args.profiles_file) if args.profiles_file else None
        result = initialize_local_runner(
            args.root,
            bootstrap=bootstrap,
            source_profiles=profiles,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "execute":
        task = _read_json_file(args.task_file)
        result = execute_local_runner_task(task, runner_root=args.root)
        write_json_redacted(args.output, result)
        print(json.dumps({"ok": True, "task_id": result.get("task_id")}, ensure_ascii=False))
        return 0
    if args.command == "status":
        print(
            json.dumps(
                local_runner_status(args.root, runner_id=args.runner_id),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    raise LocalRunnerError("local_runner_cli_command_unhandled")


__all__ = [
    "LOCAL_RUNNER_DEFAULT_TTL_SECONDS",
    "LOCAL_RUNNER_EXECUTION_MODE",
    "LOCAL_RUNNER_PROTOCOL_VERSION",
    "LOCAL_RUNNER_REGISTRY_SCHEMA",
    "LOCAL_RUNNER_RESULT_SCHEMA",
    "LOCAL_RUNNER_STATE_SCHEMA",
    "LOCAL_RUNNER_TASK_SCHEMA",
    "LocalRunnerError",
    "accept_local_runner_result",
    "acknowledge_local_runner_result",
    "execute_local_runner_task",
    "initialize_local_runner",
    "issue_local_runner_task",
    "local_runner_status",
    "list_local_runner_registrations",
    "main",
    "register_local_runner",
]


if __name__ == "__main__":
    raise SystemExit(main())
