from __future__ import annotations

"""Credential safety helpers for the private-pilot service.

Masking, encryption-key provisioning, and storage-status helpers live here.
GET/SAVE handlers bind these helpers first-class in
``CredentialsHandlerMixin``; ``install_service_credentials_patch`` only records
runtime-support status for health/compatibility surfaces.
"""

import json
import os
import secrets
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.real_project_onboarding import _safe_project_id

CREDENTIAL_KEY_ENV = "QUALIBUG_CRED_ENC_KEY"
MASKED_SECRET = "********"


class CredentialEncryptionUnavailableError(RuntimeError):
    """Credential persistence cannot guarantee encryption at rest."""


def text(value: Any) -> str:
    return str(value or "").strip()


def credential_ref(project: str, service: str, path: str) -> str:
    safe_project = _safe_project_id(project)
    safe_service = _safe_project_id(service or "service")
    safe_path = str(path or "secret").replace("/", ".").replace(" ", "_")
    return f"qualibug://credentials/{safe_project}/{safe_service}/{safe_path}"


def ensure_local_credential_encryption_key(root: Path) -> str:
    """Ensure private deployments encrypt service secrets at rest by default."""
    existing = os.environ.get(CREDENTIAL_KEY_ENV, "").strip()
    if existing:
        return "env"
    key_dir = root / "platform_workspace" / ".secrets"
    key_path = key_dir / "credential_encryption.key"
    try:
        key_dir.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            key = key_path.read_text(encoding="utf-8").strip()
        else:
            key = secrets.token_urlsafe(48)
            try:
                with key_path.open("x", encoding="utf-8") as handle:
                    handle.write(key)
            except FileExistsError:
                key = key_path.read_text(encoding="utf-8").strip()
            key_path.chmod(0o600)
    except Exception as exc:
        raise CredentialEncryptionUnavailableError(
            f"credential encryption key provisioning failed: {exc}"
        ) from exc
    if not key:
        raise CredentialEncryptionUnavailableError(
            f"credential encryption key file is empty: {key_path}"
        )
    os.environ[CREDENTIAL_KEY_ENV] = key
    return "local_private_key_file"


def mask_secret_field(container: dict[str, Any], key: str, project: str, service: str, ref_path: str) -> None:
    value = container.get(key)
    if value is None or value == "":
        return
    container[key] = MASKED_SECRET
    container[f"{key}_configured"] = True
    container[f"{key}_ref"] = credential_ref(project, service, ref_path)
    container[f"{key}_masked"] = True


def mask_service_credentials_for_frontend(project: str, services: list[Any]) -> list[dict[str, Any]]:
    """Return service configs with credential values replaced by masked refs."""
    masked_services: list[dict[str, Any]] = []
    for raw in services:
        if not isinstance(raw, dict):
            continue
        svc = json.loads(json.dumps(raw, ensure_ascii=False, default=str))
        service_name = text(svc.get("name")) or "service"

        for top_key in ("admin_pass", "bearer_token", "api_key", "token", "password"):
            mask_secret_field(svc, top_key, project, service_name, f"service.{top_key}")

        auth = svc.get("auth")
        if isinstance(auth, dict):
            for role, role_cfg in list(auth.items()):
                if isinstance(role_cfg, dict):
                    mask_secret_field(role_cfg, "password", project, service_name, f"auth.{role}.password")
                    mask_secret_field(role_cfg, "token", project, service_name, f"auth.{role}.token")
                    mask_secret_field(role_cfg, "api_key", project, service_name, f"auth.{role}.api_key")
                elif role in {"bearer_token", "api_key", "token", "password"} and role_cfg:
                    auth[role] = MASKED_SECRET
                    auth[f"{role}_configured"] = True
                    auth[f"{role}_ref"] = credential_ref(project, service_name, f"auth.{role}")
                    auth[f"{role}_masked"] = True

        db_cfg = svc.get("db")
        if isinstance(db_cfg, dict):
            mask_secret_field(db_cfg, "password", project, service_name, "db.password")

        role_accounts = svc.get("role_accounts")
        if isinstance(role_accounts, list):
            for idx, account in enumerate(role_accounts):
                if isinstance(account, dict):
                    role = text(account.get("role")) or str(idx)
                    mask_secret_field(account, "password", project, service_name, f"role_accounts.{role}.password")

        masked_services.append(svc)
    return masked_services


def credential_storage_status(root: Path, *, key_source: str | None = None) -> dict[str, Any]:
    source = key_source or ensure_local_credential_encryption_key(root)
    return {
        "mode": "encrypted_at_rest",
        "key_source": source,
        "returns_plaintext": False,
        "frontend_secret_policy": "masked_refs_only",
        "config_file_policy": "encrypt_before_write",
    }


def load_services_config(root: Path, project: str) -> list[Any]:
    config_path = root / "platform_workspace" / project / "multi_service_config.json"
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid credential config: {config_path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"credential config must be an object: {config_path}")
    raw_services = data.get("services", [])
    if not isinstance(raw_services, list) or any(not isinstance(item, dict) for item in raw_services):
        raise ValueError(f"credential config services must be a list of objects: {config_path}")
    return raw_services


def install_service_credentials_patch(*, patch_source: str) -> None:
    """Mark credential safety as installed; handlers are already first-class."""
    if str(getattr(_service, "_SERVICE_CREDENTIALS_PATCH_SOURCE", "") or "").strip():
        return
    _service._SERVICE_CREDENTIALS_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    _service._SERVICE_CREDENTIALS_MODE = "first_class"  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS = None  # type: ignore[attr-defined]


def restore_service_credentials_patch() -> None:
    """Clear credential-safety runtime-support status."""
    _service._ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS = None  # type: ignore[attr-defined]
    _service._SERVICE_CREDENTIALS_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    if hasattr(_service, "_SERVICE_CREDENTIALS_MODE"):
        delattr(_service, "_SERVICE_CREDENTIALS_MODE")
