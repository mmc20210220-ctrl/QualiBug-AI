"""Encrypted connection-profile authority for enterprise knowledge connectors.

Connector instances persist only opaque profile references. This module owns the local
encrypted profile document and an encrypted operational checkpoint used by the private
service to resume connector CAS after process restarts.
"""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any

from .connector_sync_authority import register_connector_instance
from .credential_crypto import decrypt, encrypt, is_encrypted
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center._utils import _now, _require_manage_actor
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .real_project_onboarding import _safe_project_id

CONNECTOR_PROFILE_STORE_SCHEMA = "qualibug.connector-connection-profile-store.v1"
CONNECTOR_PROFILE_SCHEMA = "qualibug.connector-connection-profile.v1"
MASKED_SECRET = "********"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_PROFILE_REF_RE = re.compile(r"^vault-ref://connectors/([A-Za-z0-9_.:-]{1,160})$")
_AUTH_FIELDS = {
    "internal_app": ("app_id", "app_secret"),
    "tenant_access_token": ("tenant_access_token",),
    "user_access_token": ("user_access_token",),
}


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


def _normalized_auth_mode(value: Any) -> str:
    mode = _text(value, 64).lower()
    if mode not in _AUTH_FIELDS:
        raise ConnectorProfileError("connector_profile_auth_mode_invalid")
    return mode


def _build_encrypted_values(
    profile: dict[str, Any],
    *,
    auth_mode: str,
    previous: dict[str, Any] | None,
) -> dict[str, str]:
    previous_values = (
        dict(previous.get("encrypted_values") or {})
        if isinstance(previous, dict)
        else {}
    )
    result: dict[str, str] = {}
    for field in _AUTH_FIELDS[auth_mode]:
        incoming = profile.get(field)
        if incoming == MASKED_SECRET:
            incoming = ""
        value = str(incoming or "").strip()
        if value:
            result[field] = _encrypted(value, field)
        elif is_encrypted(str(previous_values.get(field) or "")):
            result[field] = str(previous_values[field])
        else:
            raise ConnectorProfileError(f"connector_profile_{field}_required")
    return result


def _masked_profile(record: dict[str, Any]) -> dict[str, Any]:
    values = dict(record.get("encrypted_values") or {})
    auth_mode = _text(record.get("auth_mode"), 64)
    configured = {
        field: bool(is_encrypted(str(values.get(field) or "")))
        for field in _AUTH_FIELDS.get(auth_mode, ())
    }
    return {
        "schema": CONNECTOR_PROFILE_SCHEMA,
        "connector_instance_id": _text(record.get("connector_instance_id"), 160),
        "profile_ref": _text(record.get("profile_ref"), 500),
        "connector_type": _text(record.get("connector_type"), 160),
        "auth_mode": auth_mode,
        "configured_fields": configured,
        "credentials_configured": bool(configured) and all(configured.values()),
        "checkpoint_configured": bool(
            is_encrypted(str(record.get("checkpoint_ciphertext") or ""))
        ),
        "checkpoint_fingerprint": _text(
            record.get("checkpoint_fingerprint"), 128
        ),
        "created_at_utc": _text(record.get("created_at_utc"), 80),
        "updated_at_utc": _text(record.get("updated_at_utc"), 80),
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


def resolve_connector_connection_profile(
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
    auth_mode = _normalized_auth_mode(record.get("auth_mode"))
    encrypted_values = dict(record.get("encrypted_values") or {})
    result = {"auth_mode": auth_mode}
    for field in _AUTH_FIELDS[auth_mode]:
        ciphertext = str(encrypted_values.get(field) or "")
        if not is_encrypted(ciphertext):
            raise ConnectorProfileError(
                f"connector_profile_{field}_ciphertext_invalid"
            )
        try:
            value = decrypt(ciphertext)
        except Exception as exc:
            raise ConnectorProfileError(
                f"connector_profile_{field}_decryption_failed"
            ) from exc
        if not value:
            raise ConnectorProfileError(f"connector_profile_{field}_empty")
        result[field] = value
    return result


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
    return {
        "ok": True,
        "connector_instance_id": connector,
        "sync_epoch_id": sync_epoch_id,
        "checkpoint_fingerprint": record["checkpoint_fingerprint"],
        "checkpoint_encrypted_at_rest": True,
        "checkpoint_plaintext_returned": False,
    }


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
) -> dict[str, Any]:
    """Persist an encrypted profile, then bind its opaque ref to the instance."""
    resolved_root = root or ROOT
    _require_encryption(resolved_root)
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    if not isinstance(profile, dict):
        raise ConnectorProfileError("connector_profile_must_be_object")
    auth_mode = _normalized_auth_mode(profile.get("auth_mode"))
    profile_ref = connector_profile_ref(connector)

    store = _load_store(project, resolved_root)
    before_store = copy.deepcopy(store)
    record = _profile_by_id(store, connector)
    created = record is None
    now = _now()
    if record is None:
        record = {
            "schema": CONNECTOR_PROFILE_SCHEMA,
            "connector_instance_id": connector,
            "profile_ref": profile_ref,
            "connector_type": "feishu",
            "created_at_utc": now,
            "created_by": clean_actor,
            "checkpoint_ciphertext": "",
            "checkpoint_fingerprint": "",
            "checkpoint_sync_epoch_id": "",
        }
        store["profiles"].append(record)
    record["auth_mode"] = auth_mode
    record["encrypted_values"] = _build_encrypted_values(
        profile,
        auth_mode=auth_mode,
        previous=record,
    )
    record["updated_at_utc"] = now
    record["updated_by"] = clean_actor
    record["plaintext_credentials_persisted"] = False
    store["audit_events"].append(
        {
            "event": (
                "create_connector_connection_profile"
                if created
                else "update_connector_connection_profile"
            ),
            "at_utc": now,
            "actor": clean_actor,
            "connector_instance_id": connector,
            "profile_ref": profile_ref,
            "auth_mode": auth_mode,
            "plaintext_credentials_persisted": False,
        }
    )
    _save_store(project, resolved_root, store)

    try:
        instance_receipt = register_connector_instance(
            project,
            root=resolved_root,
            actor=clean_actor,
            connector_instance_id=connector,
            connector_type="feishu",
            display_name=display_name,
            resource_scope=resource_scope,
            connection_profile_ref=profile_ref,
            status=status,
            metadata={"profile_storage": "encrypted_local_authority"},
        )
    except Exception:
        _save_store(project, resolved_root, before_store)
        raise

    return {
        "ok": True,
        "created": created,
        "connector_instance": instance_receipt["connector_instance"],
        "connection_profile": _masked_profile(record),
        "credential_storage": {
            "mode": "encrypted_at_rest",
            "plaintext_returned": False,
            "profile_reference_only_in_connector_registry": True,
        },
    }


__all__ = [
    "CONNECTOR_PROFILE_SCHEMA",
    "CONNECTOR_PROFILE_STORE_SCHEMA",
    "ConnectorProfileError",
    "MASKED_SECRET",
    "commit_connector_sync_checkpoint",
    "configure_feishu_connector",
    "connector_profile_ref",
    "list_connector_connection_profiles",
    "load_connector_sync_checkpoint",
    "resolve_connector_connection_profile",
]
