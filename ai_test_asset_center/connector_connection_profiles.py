"""Encrypted connection-profile authority for enterprise knowledge connectors.

Connector instances persist only opaque profile references. This module owns the local
encrypted profile document and an encrypted operational checkpoint used by the private
service to resume connector CAS after process restarts.
"""
from __future__ import annotations

import copy
import hashlib
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .connector_registry import (
    ConnectorManifest,
    ConnectorRegistryError,
    build_default_connector_registry,
)
from .connector_sync_authority import (
    list_connector_instances,
    register_connector_instance,
)
from .credential_crypto import decrypt, encrypt, is_encrypted
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center._utils import (
    _now,
    _redact_text,
    _require_manage_actor,
)
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .real_project_onboarding import _safe_project_id

CONNECTOR_PROFILE_STORE_SCHEMA = "qualibug.connector-connection-profile-store.v1"
CONNECTOR_PROFILE_SCHEMA = "qualibug.connector-connection-profile.v1"
MASKED_SECRET = "********"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_PROFILE_REF_RE = re.compile(r"^vault-ref://connectors/([A-Za-z0-9_.:-]{1,160})$")


class ConnectorProfileError(RuntimeError):
    """Encrypted connector profile persistence or resolution failed safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ConnectorProfileError(f"{field}_invalid")
    return result


def connector_profile_ref(connector_instance_id: str) -> str:
    connector = _identifier(connector_instance_id, "connector_instance_id")
    return f"vault-ref://connectors/{connector}"


def _profile_id_from_ref(profile_ref: str) -> str:
    match = _PROFILE_REF_RE.fullmatch(_text(profile_ref, 500))
    if not match:
        raise ConnectorProfileError("connector_profile_ref_invalid")
    return match.group(1)


def _store_path(project: str, root: Path) -> Path:
    return (
        root
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_connection_profiles.json"
    )


def _default_store(project: str) -> dict[str, Any]:
    now = _now()
    return {
        "schema": CONNECTOR_PROFILE_STORE_SCHEMA,
        "project_id": project,
        "created_at_utc": now,
        "updated_at_utc": now,
        "profiles": [],
        "audit_events": [],
        "governance": {
            "encrypted_at_rest": True,
            "plaintext_returned_to_frontend": False,
            "connector_instance_stores_profile_reference_only": True,
            "operational_checkpoint_encrypted": True,
            "project_knowledge_transaction_serialized": True,
        },
    }


def _load_store(project_id: str, root: Path) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    raw = _read_json_object(_store_path(project, root))
    store = _default_store(project)
    if raw:
        store.update(raw)
    store["profiles"] = [
        row for row in store.get("profiles") or [] if isinstance(row, dict)
    ]
    store["audit_events"] = [
        row for row in store.get("audit_events") or [] if isinstance(row, dict)
    ]
    governance = dict(store.get("governance") or {})
    governance.update(_default_store(project)["governance"])
    store["governance"] = governance
    return store


def _save_store(project_id: str, root: Path, store: dict[str, Any]) -> None:
    project = _safe_project_id(project_id)
    store["updated_at_utc"] = _now()
    _write_json_object_atomic(_store_path(project, root), store)


def _profile_by_id(store: dict[str, Any], connector: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in store.get("profiles") or []
            if _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )


@contextmanager
def _profile_transaction(
    root: Path,
    project: str,
    *,
    operation: str,
    actor: dict[str, Any],
) -> Iterator[None]:
    try:
        with knowledge_transaction(
            root,
            project,
            operation=operation,
            actor=actor,
            wait_seconds=5.0,
        ):
            yield
    except KnowledgeTransactionBusy as exc:
        raise ConnectorProfileError(
            "connector_profile_transaction_busy"
        ) from exc


def _require_encryption(root: Path) -> str:
    # Lazy import avoids a package-composition cycle:
    # private_pilot_service -> connector handlers -> profile authority.
    from .private_pilot_credentials_patch import (
        CredentialEncryptionUnavailableError,
        ensure_local_credential_encryption_key,
    )

    try:
        return ensure_local_credential_encryption_key(root)
    except CredentialEncryptionUnavailableError as exc:
        raise ConnectorProfileError("connector_profile_encryption_unavailable") from exc


def _encrypted(value: str, field: str) -> str:
    if not value:
        raise ConnectorProfileError(f"connector_profile_{field}_required")
    ciphertext = encrypt(value)
    if not is_encrypted(ciphertext):
        raise ConnectorProfileError("connector_profile_plaintext_persistence_refused")
    return ciphertext


def _connector_manifest(connector_type: Any) -> ConnectorManifest:
    connector = _identifier(connector_type, "connector_type")
    try:
        return build_default_connector_registry().manifest(connector)
    except ConnectorRegistryError as exc:
        raise ConnectorProfileError(
            f"connector_type_not_registered:{connector}"
        ) from exc


def _normalized_auth_mode(
    value: Any,
    manifest: ConnectorManifest,
) -> str:
    mode = _text(value, 64).lower()
    if not manifest.auth_modes and not manifest.credential_fields:
        if mode:
            raise ConnectorProfileError("connector_profile_auth_mode_invalid")
        return ""
    if mode not in manifest.auth_modes:
        raise ConnectorProfileError("connector_profile_auth_mode_invalid")
    return mode


def _manifest_fields_for_auth_mode(
    manifest: ConnectorManifest,
    auth_mode: str,
) -> tuple[Any, ...]:
    if not manifest.auth_modes and not manifest.credential_fields:
        if auth_mode:
            raise ConnectorProfileError("connector_profile_auth_mode_invalid")
        return ()
    try:
        return manifest.credential_fields_for_auth_mode(auth_mode)
    except ConnectorRegistryError as exc:
        raise ConnectorProfileError("connector_profile_auth_mode_invalid") from exc


def _normalized_expiry(value: Any) -> str:
    raw = _text(value, 80)
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectorProfileError(
            "connector_credential_expiry_invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise ConnectorProfileError("connector_credential_expiry_timezone_required")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_encrypted_values(
    profile: dict[str, Any],
    *,
    manifest: ConnectorManifest,
    auth_mode: str,
    previous: dict[str, Any] | None,
) -> dict[str, str]:
    previous_values = (
        dict(previous.get("encrypted_values") or {})
        if isinstance(previous, dict)
        else {}
    )
    fields = _manifest_fields_for_auth_mode(manifest, auth_mode)
    declared = {field.name for field in fields}
    unknown = sorted(
        str(key)
        for key in profile
        if str(key) != "auth_mode" and str(key) not in declared
    )
    if unknown:
        raise ConnectorProfileError(
            "connector_profile_field_not_declared:" + ",".join(unknown)
        )
    result: dict[str, str] = {}
    for field in fields:
        incoming = profile.get(field.name)
        if incoming == MASKED_SECRET:
            incoming = ""
        value = str(incoming or "").strip()
        if value:
            result[field.name] = _encrypted(value, field.name)
        elif is_encrypted(str(previous_values.get(field.name) or "")):
            result[field.name] = str(previous_values[field.name])
        elif field.required:
            raise ConnectorProfileError(
                f"connector_profile_{field.name}_required"
            )
    return result


def _masked_profile(record: dict[str, Any]) -> dict[str, Any]:
    values = dict(record.get("encrypted_values") or {})
    auth_mode = _text(record.get("auth_mode"), 64)
    manifest = _connector_manifest(record.get("connector_type"))
    fields = _manifest_fields_for_auth_mode(manifest, auth_mode)
    configured = {
        field.name: bool(is_encrypted(str(values.get(field.name) or "")))
        for field in fields
    }
    required_fields_configured = all(
        configured[field.name]
        for field in fields
        if field.required
    )
    return {
        "schema": CONNECTOR_PROFILE_SCHEMA,
        "connector_instance_id": _text(record.get("connector_instance_id"), 160),
        "profile_ref": _text(record.get("profile_ref"), 500),
        "connector_type": _text(record.get("connector_type"), 160),
        "auth_mode": auth_mode,
        "configured_fields": configured,
        "credentials_configured": required_fields_configured,
        "checkpoint_configured": bool(
            is_encrypted(str(record.get("checkpoint_ciphertext") or ""))
        ),
        "checkpoint_fingerprint": _text(
            record.get("checkpoint_fingerprint"), 128
        ),
        "created_at_utc": _text(record.get("created_at_utc"), 80),
        "updated_at_utc": _text(record.get("updated_at_utc"), 80),
        "credential_status": _text(
            record.get("credential_status"), 64
        ) or "ACTIVE",
        "credential_expires_at_utc": _text(
            record.get("credential_expires_at_utc"), 80
        ),
        "reauthorization_required": bool(
            record.get("reauthorization_required")
        ),
        "reauthorization_reason": _text(
            record.get("reauthorization_reason"), 300
        ),
        "plaintext_returned": False,
    }


def list_connector_connection_profiles(
    project_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    store = _load_store(project_id, resolved_root)
    return {
        "schema": CONNECTOR_PROFILE_STORE_SCHEMA,
        "project_id": store["project_id"],
        "profiles": [_masked_profile(row) for row in store["profiles"]],
        "governance": dict(store.get("governance") or {}),
    }


def resolve_connector_profile(
    project_id: str,
    profile_ref: str,
    *,
    root: Path | None = None,
) -> dict[str, str]:
    resolved_root = root or ROOT
    _require_encryption(resolved_root)
    project = _safe_project_id(project_id)
    connector = _profile_id_from_ref(profile_ref)
    store = _load_store(project, resolved_root)
    record = _profile_by_id(store, connector)
    if record is None or _text(record.get("profile_ref"), 500) != profile_ref:
        raise ConnectorProfileError("connector_profile_not_found")
    manifest = _connector_manifest(record.get("connector_type"))
    auth_mode = _normalized_auth_mode(record.get("auth_mode"), manifest)
    fields = _manifest_fields_for_auth_mode(manifest, auth_mode)
    encrypted_values = dict(record.get("encrypted_values") or {})
    result = {"auth_mode": auth_mode}
    for field in fields:
        ciphertext = str(encrypted_values.get(field.name) or "")
        if not is_encrypted(ciphertext):
            if field.required:
                raise ConnectorProfileError(
                    f"connector_profile_{field.name}_ciphertext_invalid"
                )
            continue
        try:
            value = decrypt(ciphertext)
        except Exception as exc:
            raise ConnectorProfileError(
                f"connector_profile_{field.name}_decryption_failed"
            ) from exc
        if not value and field.required:
            raise ConnectorProfileError(f"connector_profile_{field.name}_empty")
        if value:
            result[field.name] = value
    return result


def resolve_connector_connection_profile(
    project_id: str,
    profile_ref: str,
    *,
    root: Path | None = None,
) -> dict[str, str]:
    """Compatibility facade for the generic connector profile resolver."""
    return resolve_connector_profile(project_id, profile_ref, root=root)


def load_connector_sync_checkpoint(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
) -> str:
    resolved_root = root or ROOT
    _require_encryption(resolved_root)
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    store = _load_store(project, resolved_root)
    record = _profile_by_id(store, connector)
    if record is None:
        raise ConnectorProfileError("connector_profile_not_found")
    ciphertext = str(record.get("checkpoint_ciphertext") or "")
    if not ciphertext:
        return ""
    if not is_encrypted(ciphertext):
        raise ConnectorProfileError("connector_checkpoint_ciphertext_invalid")
    try:
        checkpoint = decrypt(ciphertext)
    except Exception as exc:
        raise ConnectorProfileError("connector_checkpoint_decryption_failed") from exc
    expected = _text(record.get("checkpoint_fingerprint"), 128)
    actual = hashlib.sha256(checkpoint.encode("utf-8")).hexdigest()
    if not checkpoint or not expected or actual != expected:
        raise ConnectorProfileError("connector_checkpoint_integrity_failed")
    return checkpoint


def commit_connector_sync_checkpoint(
    project_id: str,
    connector_instance_id: str,
    checkpoint: str,
    *,
    sync_epoch_id: str,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    _require_encryption(resolved_root)
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    value = str(checkpoint or "").strip()
    if not value:
        raise ConnectorProfileError("connector_checkpoint_required")
    with _profile_transaction(
        resolved_root,
        project,
        operation="commit_connector_sync_checkpoint",
        actor=clean_actor,
    ):
        store = _load_store(project, resolved_root)
        record = _profile_by_id(store, connector)
        if record is None:
            raise ConnectorProfileError("connector_profile_not_found")
        record["checkpoint_ciphertext"] = _encrypted(value, "checkpoint")
        record["checkpoint_fingerprint"] = hashlib.sha256(
            value.encode("utf-8")
        ).hexdigest()
        record["checkpoint_sync_epoch_id"] = _identifier(
            sync_epoch_id, "sync_epoch_id"
        )
        record["checkpoint_updated_at_utc"] = _now()
        record["updated_at_utc"] = _now()
        store["audit_events"].append(
            {
                "event": "commit_connector_sync_checkpoint",
                "at_utc": _now(),
                "actor": clean_actor,
                "connector_instance_id": connector,
                "sync_epoch_id": sync_epoch_id,
                "checkpoint_fingerprint": record["checkpoint_fingerprint"],
                "checkpoint_plaintext_persisted": False,
            }
        )
        _save_store(project, resolved_root, store)
        fingerprint = record["checkpoint_fingerprint"]
    return {
        "ok": True,
        "connector_instance_id": connector,
        "sync_epoch_id": sync_epoch_id,
        "checkpoint_fingerprint": fingerprint,
        "checkpoint_encrypted_at_rest": True,
        "checkpoint_plaintext_returned": False,
    }


def _configure_connector_profile(
    project_id: str,
    *,
    connector_type: str,
    connector_instance_id: str,
    resource_scope: str,
    profile: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    display_name: str = "",
    status: str = "ACTIVE",
    operation: str,
    event_name: str = "",
    credential_expires_at_utc: Any = "",
    instance_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a Manifest-validated encrypted profile and bind its opaque ref."""
    resolved_root = root or ROOT
    _require_encryption(resolved_root)
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    manifest = _connector_manifest(connector_type)
    clean_actor = _require_manage_actor(actor)
    if not isinstance(profile, dict):
        raise ConnectorProfileError("connector_profile_must_be_object")
    if (
        isinstance(instance_metadata, Mapping)
        and "webhook_policy_json" in instance_metadata
        and manifest.webhook_supported is not True
    ):
        raise ConnectorProfileError("webhook_not_supported_by_connector")
    auth_mode = _normalized_auth_mode(profile.get("auth_mode"), manifest)
    normalized_expiry = _normalized_expiry(credential_expires_at_utc)
    profile_ref = connector_profile_ref(connector)

    with _profile_transaction(
        resolved_root,
        project,
        operation=operation,
        actor=clean_actor,
    ):
        store = _load_store(project, resolved_root)
        before_store = copy.deepcopy(store)
        record = _profile_by_id(store, connector)
        created = record is None
        previous_expiry = (
            _text(record.get("credential_expires_at_utc"), 80)
            if isinstance(record, dict)
            else ""
        )
        now = _now()
        if record is None:
            record = {
                "schema": CONNECTOR_PROFILE_SCHEMA,
                "connector_instance_id": connector,
                "profile_ref": profile_ref,
                "connector_type": manifest.connector_type,
                "created_at_utc": now,
                "created_by": clean_actor,
                "checkpoint_ciphertext": "",
                "checkpoint_fingerprint": "",
                "checkpoint_sync_epoch_id": "",
            }
            store["profiles"].append(record)
        elif _text(record.get("connector_type"), 160) != manifest.connector_type:
            raise ConnectorProfileError("connector_profile_connector_type_mismatch")
        record["auth_mode"] = auth_mode
        record["encrypted_values"] = _build_encrypted_values(
            profile,
            manifest=manifest,
            auth_mode=auth_mode,
            previous=record,
        )
        record["updated_at_utc"] = now
        record["updated_by"] = clean_actor
        record["plaintext_credentials_persisted"] = False
        record["credential_status"] = "ACTIVE"
        record["credential_expires_at_utc"] = normalized_expiry or previous_expiry
        record["reauthorization_required"] = False
        record["reauthorization_reason"] = ""
        store["audit_events"].append(
            {
                "event": event_name
                or (
                    "create_connector_connection_profile"
                    if created
                    else "update_connector_connection_profile"
                ),
                "at_utc": now,
                "actor": clean_actor,
                "connector_instance_id": connector,
                "profile_ref": profile_ref,
                "connector_type": manifest.connector_type,
                "manifest_version": manifest.version,
                "auth_mode": auth_mode,
                "plaintext_credentials_persisted": False,
            }
        )
        _save_store(project, resolved_root, store)

        existing_instance = next(
            (
                dict(row)
                for row in (
                    list_connector_instances(
                        project,
                        root=resolved_root,
                        include_disabled=True,
                    ).get("connector_instances")
                    or []
                )
                if isinstance(row, dict)
                and _text(row.get("connector_instance_id"), 160) == connector
            ),
            None,
        )
        metadata = {
            "profile_storage": "encrypted_local_authority",
            **dict((existing_instance or {}).get("metadata") or {}),
        }
        if instance_metadata is not None:
            if not isinstance(instance_metadata, Mapping):
                raise ConnectorProfileError("connector_instance_metadata_must_be_object")
            metadata.update(dict(instance_metadata))
        try:
            instance_receipt = register_connector_instance(
                project,
                root=resolved_root,
                actor=clean_actor,
                connector_instance_id=connector,
                connector_type=manifest.connector_type,
                display_name=display_name,
                resource_scope=resource_scope,
                connection_profile_ref=profile_ref,
                status=status,
                metadata=metadata,
            )
        except Exception:
            _save_store(project, resolved_root, before_store)
            raise
        masked = _masked_profile(record)

    return {
        "ok": True,
        "created": created,
        "connector_instance": instance_receipt["connector_instance"],
        "connection_profile": masked,
        "credential_storage": {
            "mode": "encrypted_at_rest",
            "plaintext_returned": False,
            "profile_reference_only_in_connector_registry": True,
            "project_transaction_serialized": True,
        },
    }


def configure_connector_profile(
    project_id: str,
    *,
    connector_type: str,
    connector_instance_id: str,
    resource_scope: str,
    profile: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    display_name: str = "",
    status: str = "ACTIVE",
    credential_expires_at_utc: Any = "",
    instance_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _configure_connector_profile(
        project_id,
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
        resource_scope=resource_scope,
        profile=profile,
        root=root,
        actor=actor,
        display_name=display_name,
        status=status,
        operation="configure_connector_profile",
        credential_expires_at_utc=credential_expires_at_utc,
        instance_metadata=instance_metadata,
    )


def configure_feishu_connector(
    project_id: str,
    *,
    connector_instance_id: str,
    resource_scope: str,
    profile: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    display_name: str = "",
    status: str = "ACTIVE",
    credential_expires_at_utc: Any = "",
    instance_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility facade for the generic Manifest-driven profile authority."""
    return _configure_connector_profile(
        project_id,
        connector_type="feishu",
        connector_instance_id=connector_instance_id,
        resource_scope=resource_scope,
        profile=profile,
        root=root,
        actor=actor,
        display_name=display_name,
        status=status,
        operation="configure_feishu_connector",
        credential_expires_at_utc=credential_expires_at_utc,
        instance_metadata=instance_metadata,
    )


def _connector_instance_for_profile(
    project: str,
    connector: str,
    root: Path,
) -> dict[str, Any]:
    rows = list_connector_instances(
        project,
        root=root,
        include_disabled=True,
    ).get("connector_instances") or []
    instance = next(
        (
            dict(row)
            for row in rows
            if isinstance(row, dict)
            and _text(row.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if instance is None:
        raise ConnectorProfileError("connector_instance_not_registered")
    connector_type = _text(instance.get("connector_type"), 160)
    if not connector_type:
        raise ConnectorProfileError("connector_instance_type_missing")
    return instance


def rotate_connector_credentials(
    project_id: str,
    *,
    connector_instance_id: str,
    profile: dict[str, Any],
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    credential_expires_at_utc: Any = "",
) -> dict[str, Any]:
    """Replace encrypted credentials without changing scope or source identity."""
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    instance = _connector_instance_for_profile(project, connector, resolved_root)
    return _configure_connector_profile(
        project,
        connector_type=_text(instance.get("connector_type"), 160),
        connector_instance_id=connector,
        resource_scope=_text(instance.get("resource_scope"), 20000),
        profile=profile,
        root=resolved_root,
        actor=actor,
        display_name=_text(instance.get("display_name"), 240),
        status=_text(instance.get("status"), 32) or "ACTIVE",
        operation="rotate_connector_credentials",
        event_name="rotate_connector_credentials",
        credential_expires_at_utc=credential_expires_at_utc,
    )


def mark_connector_reauthorization_required(
    project_id: str,
    connector_instance_id: str,
    *,
    required: bool = True,
    reason: str = "",
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    _require_encryption(resolved_root)
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    if not isinstance(required, bool):
        raise ConnectorProfileError("connector_reauthorization_required_invalid")
    clean_reason = _redact_text(reason, 300)
    with _profile_transaction(
        resolved_root,
        project,
        operation="mark_connector_reauthorization_required",
        actor=clean_actor,
    ):
        store = _load_store(project, resolved_root)
        record = _profile_by_id(store, connector)
        if record is None:
            raise ConnectorProfileError("connector_profile_not_found")
        _connector_manifest(record.get("connector_type"))
        record["reauthorization_required"] = required
        record["reauthorization_reason"] = clean_reason if required else ""
        if required:
            record["credential_status"] = "REAUTHORIZATION_REQUIRED"
        elif _text(record.get("credential_status"), 64) == "REAUTHORIZATION_REQUIRED":
            record["credential_status"] = "ACTIVE"
        record["updated_at_utc"] = _now()
        record["updated_by"] = clean_actor
        store["audit_events"].append(
            {
                "event": "mark_connector_reauthorization_required",
                "at_utc": _now(),
                "actor": clean_actor,
                "connector_instance_id": connector,
                "required": required,
                "reason": clean_reason,
                "plaintext_credentials_persisted": False,
            }
        )
        _save_store(project, resolved_root, store)
        masked = _masked_profile(record)
    return {
        "ok": True,
        "connector_instance_id": connector,
        "connection_profile": masked,
        "credential_values_returned": False,
    }


def _parse_utc(value: Any, field: str) -> datetime:
    raw = _text(value, 80)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectorProfileError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ConnectorProfileError(f"{field}_timezone_required")
    return parsed.astimezone(timezone.utc)


def connector_credential_expiry_status(
    project_id: str,
    connector_instance_id: str,
    *,
    now_utc: Any = "",
    expiring_within_seconds: int = 86_400,
    root: Path | None = None,
) -> dict[str, Any]:
    resolved_root = root or ROOT
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    if not isinstance(expiring_within_seconds, int) or expiring_within_seconds < 0:
        raise ConnectorProfileError("connector_expiry_window_invalid")
    store = _load_store(project, resolved_root)
    record = _profile_by_id(store, connector)
    if record is None:
        raise ConnectorProfileError("connector_profile_not_found")
    _connector_manifest(record.get("connector_type"))
    checked_at = (
        _parse_utc(now_utc, "connector_expiry_check_time")
        if _text(now_utc, 80)
        else _parse_utc(_now(), "connector_expiry_check_time")
    )
    expires_at_raw = _text(record.get("credential_expires_at_utc"), 80)
    expires_at = (
        _parse_utc(expires_at_raw, "connector_credential_expiry")
        if expires_at_raw
        else None
    )
    if bool(record.get("reauthorization_required")):
        status = "REAUTHORIZATION_REQUIRED"
    else:
        stored_status = _text(record.get("credential_status"), 64) or "ACTIVE"
        if stored_status != "ACTIVE":
            status = stored_status
        elif expires_at is None:
            status = "ACTIVE"
        elif expires_at <= checked_at:
            status = "EXPIRED"
        elif (
            expires_at - checked_at
        ).total_seconds() <= expiring_within_seconds:
            status = "EXPIRING"
        else:
            status = "ACTIVE"
    return {
        "ok": True,
        "connector_instance_id": connector,
        "status": status,
        "credential_expires_at_utc": expires_at_raw,
        "reauthorization_required": bool(
            record.get("reauthorization_required")
        ),
        "reauthorization_reason": _text(
            record.get("reauthorization_reason"), 300
        ),
        "checked_at_utc": checked_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plaintext_returned": False,
    }


__all__ = [
    "CONNECTOR_PROFILE_SCHEMA",
    "CONNECTOR_PROFILE_STORE_SCHEMA",
    "ConnectorProfileError",
    "MASKED_SECRET",
    "commit_connector_sync_checkpoint",
    "configure_connector_profile",
    "configure_feishu_connector",
    "connector_credential_expiry_status",
    "connector_profile_ref",
    "list_connector_connection_profiles",
    "load_connector_sync_checkpoint",
    "mark_connector_reauthorization_required",
    "resolve_connector_connection_profile",
    "resolve_connector_profile",
    "rotate_connector_credentials",
]
