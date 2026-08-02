"""Manifest-driven OAuth authorization and reauthorization authority.

The connector profile authority remains the only owner of encrypted connector credentials.
This module owns only the short-lived OAuth transaction, PKCE binding, token exchange, and
safe authorization projection.  OAuth events never write source material, advance a cursor,
or infer remote deletion.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .connector_connection_profiles import (
    ConnectorProfileError,
    connector_credential_expiry_status,
    mark_connector_reauthorization_required,
    resolve_connector_profile,
    rotate_connector_credentials,
)
from .connector_registry import (
    ConnectorManifest,
    ConnectorRegistryError,
    build_default_connector_registry,
)
from .connector_sync_authority import list_connector_instances
from .credential_crypto import decrypt, encrypt, is_encrypted
from .enterprise_knowledge_center._common import ROOT
from .enterprise_knowledge_center._utils import _now, _require_manage_actor
from .enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .real_project_onboarding import _safe_project_id
from .ssrf_guard import SsrfBlockedError, safe_urlopen

CONNECTOR_OAUTH_SCHEMA = "qualibug.connector-oauth-authority.v1"
CONNECTOR_OAUTH_LEDGER_SCHEMA = "qualibug.connector-oauth-ledger.v1"
_OAUTH_TYPE = "oauth2_authorization_code"
_TRANSACTION_STATUSES = {
    "PENDING",
    "PROCESSING",
    "SUCCEEDED",
    "FAILED",
    "EXPIRED",
    "SUPERSEDED",
}
_TERMINAL_TRANSACTION_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "EXPIRED",
    "SUPERSEDED",
}
_OAUTH_AUTH_STATUSES = {
    "AUTHORIZED",
    "EXPIRING",
    "EXPIRED",
    "REAUTHORIZATION_REQUIRED",
    "PERMISSION_INSUFFICIENT",
    "REVOKED",
}
_MAX_SCOPE_COUNT = 100
_MAX_TRANSACTION_COUNT = 64
_DEFAULT_TRANSACTION_TTL_SECONDS = 600
_MAX_TRANSACTION_TTL_SECONDS = 900
_MAX_TOKEN_EXPIRY_SECONDS = 31_536_000


class ConnectorOAuthError(RuntimeError):
    """An OAuth transaction failed a declared contract or a safe transport boundary."""


TokenRequester = Callable[
    [str, Mapping[str, str], Mapping[str, str], float], Mapping[str, Any]
]


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _identifier(value: Any, field: str) -> str:
    result = _text(value, 160)
    if not result or any(
        character not in "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
        for character in result
    ):
        raise ConnectorOAuthError(f"oauth_{field}_invalid")
    return result


def _fingerprint(value: Any) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def _actor_fingerprint(actor: Mapping[str, Any]) -> str:
    clean = _require_manage_actor(dict(actor))
    return _fingerprint(json.dumps(clean, ensure_ascii=False, sort_keys=True))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: Any, field: str) -> datetime:
    raw = _text(value, 80)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectorOAuthError(f"oauth_{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ConnectorOAuthError(f"oauth_{field}_timezone_required")
    return parsed.astimezone(timezone.utc)


def _scope_values(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ConnectorOAuthError(f"oauth_{field}_invalid")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = _text(raw, 200)
        if not item or any(character.isspace() for character in item):
            raise ConnectorOAuthError(f"oauth_{field}_invalid")
        if item not in seen:
            seen.add(item)
            result.append(item)
    if len(result) > _MAX_SCOPE_COUNT:
        raise ConnectorOAuthError(f"oauth_{field}_too_many")
    return result


def _oauth_config(manifest: ConnectorManifest) -> dict[str, Any]:
    raw = dict(manifest.oauth_schema or {})
    if not raw:
        raise ConnectorOAuthError("oauth_not_supported_by_connector")
    if _text(raw.get("type"), 80) != _OAUTH_TYPE:
        raise ConnectorOAuthError("oauth_schema_type_invalid")
    config = {
        "authorization_endpoint": _text(raw.get("authorization_endpoint"), 2000),
        "token_endpoint": _text(raw.get("token_endpoint"), 2000),
        "client_id": _text(raw.get("client_id"), 500),
        "redirect_uri": _text(raw.get("redirect_uri"), 2000),
        "auth_mode": _text(raw.get("auth_mode"), 80),
        "client_auth_method": _text(raw.get("client_auth_method"), 80) or "none",
        "client_secret_field": _text(raw.get("client_secret_field"), 160),
        "access_token_field": _text(raw.get("access_token_field"), 160),
        "refresh_token_field": _text(raw.get("refresh_token_field"), 160),
        "scope_field": _text(raw.get("scope_field"), 160),
        "token_type_field": _text(raw.get("token_type_field"), 160),
        "minimum_scopes": _scope_values(raw.get("minimum_scopes", []), "minimum_scopes"),
        "optional_scopes": _scope_values(raw.get("optional_scopes", []), "optional_scopes"),
    }
    for key in (
        "authorization_endpoint",
        "token_endpoint",
        "client_id",
        "redirect_uri",
        "auth_mode",
        "access_token_field",
        "refresh_token_field",
    ):
        if not config[key]:
            raise ConnectorOAuthError(f"oauth_{key}_missing")
    if config["client_auth_method"] not in {
        "none",
        "client_secret_basic",
        "client_secret_post",
    }:
        raise ConnectorOAuthError("oauth_client_auth_method_invalid")
    if config["client_auth_method"] != "none" and not config["client_secret_field"]:
        raise ConnectorOAuthError("oauth_client_secret_field_missing")
    if set(config["minimum_scopes"]) & set(config["optional_scopes"]):
        raise ConnectorOAuthError("oauth_scope_declared_twice")
    _validate_oauth_endpoint(config["authorization_endpoint"], "authorization_endpoint")
    _validate_oauth_endpoint(config["token_endpoint"], "token_endpoint")
    _validate_redirect_uri(config["redirect_uri"])
    fields = {
        field.name: field
        for field in manifest.credential_fields_for_auth_mode(config["auth_mode"])
    }
    for key in (
        "access_token_field",
        "refresh_token_field",
        "scope_field",
        "token_type_field",
        "client_secret_field",
    ):
        field_name = config[key]
        if field_name and field_name not in fields:
            raise ConnectorOAuthError(f"oauth_{key}_not_declared")
    for key in ("access_token_field", "refresh_token_field", "client_secret_field"):
        field_name = config[key]
        if field_name and not fields[field_name].secret:
            raise ConnectorOAuthError(f"oauth_{key}_must_be_secret")
    return config


def _validate_oauth_endpoint(value: str, field: str) -> None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ConnectorOAuthError(f"oauth_{field}_must_be_https")
    if parsed.username or parsed.password or parsed.fragment:
        raise ConnectorOAuthError(f"oauth_{field}_invalid")


def _validate_redirect_uri(value: str) -> None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ConnectorOAuthError("oauth_redirect_uri_invalid")
    if parsed.username or parsed.password or parsed.fragment:
        raise ConnectorOAuthError("oauth_redirect_uri_invalid")


def _manifest_and_instance(
    project: str,
    connector: str,
    root: Path,
) -> tuple[dict[str, Any], ConnectorManifest, dict[str, Any]]:
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
        raise ConnectorOAuthError("oauth_connector_not_found")
    connector_type = _text(instance.get("connector_type"), 160)
    if not connector_type:
        raise ConnectorOAuthError("oauth_connector_type_missing")
    try:
        manifest = build_default_connector_registry().manifest(connector_type)
    except ConnectorRegistryError as exc:
        raise ConnectorOAuthError("oauth_connector_manifest_unavailable") from exc
    config = _oauth_config(manifest) if manifest.oauth_schema else {}
    return instance, manifest, config


def _ledger_path(project: str, connector: str, root: Path) -> Path:
    safe_connector = urllib.parse.quote(connector, safe="")
    return (
        root.resolve()
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_oauth"
        / f"{safe_connector}.json"
    )


def _default_ledger(project: str, connector: str) -> dict[str, Any]:
    now = _now()
    return {
        "schema": CONNECTOR_OAUTH_LEDGER_SCHEMA,
        "project_id": project,
        "connector_instance_id": connector,
        "created_at_utc": now,
        "updated_at_utc": now,
        "transactions": [],
        "last_success": {},
        "last_failure": {},
        "last_refresh_success": {},
        "last_refresh_failure": {},
        "governance": {
            "state_plaintext_persisted": False,
            "authorization_code_persisted": False,
            "pkce_verifier_encrypted_at_rest": True,
            "access_token_encrypted_at_rest": True,
            "refresh_token_encrypted_at_rest": True,
            "source_content_mutated": False,
            "source_history_preserved": True,
            "checkpoint_preserved": True,
            "oauth_failure_never_infers_remote_deletion": True,
        },
    }


def _load_ledger(project: str, connector: str, root: Path) -> dict[str, Any]:
    path = _ledger_path(project, connector, root)
    try:
        raw = _read_json_object(path)
    except (OSError, ValueError) as exc:
        raise ConnectorOAuthError("oauth_ledger_unreadable") from exc
    ledger = _default_ledger(project, connector)
    if raw:
        ledger.update(raw)
    if _text(ledger.get("schema"), 120) != CONNECTOR_OAUTH_LEDGER_SCHEMA:
        raise ConnectorOAuthError("oauth_ledger_schema_invalid")
    if _text(ledger.get("project_id"), 160) != project:
        raise ConnectorOAuthError("oauth_ledger_project_mismatch")
    if _text(ledger.get("connector_instance_id"), 160) != connector:
        raise ConnectorOAuthError("oauth_ledger_connector_mismatch")
    transactions = ledger.get("transactions")
    if not isinstance(transactions, list) or any(
        not isinstance(row, dict) for row in transactions
    ):
        raise ConnectorOAuthError("oauth_ledger_transactions_invalid")
    for row in transactions:
        status = _text(row.get("status"), 32)
        if status not in _TRANSACTION_STATUSES:
            raise ConnectorOAuthError("oauth_ledger_transaction_status_invalid")
        if not _text(row.get("transaction_id"), 160):
            raise ConnectorOAuthError("oauth_ledger_transaction_id_missing")
        if not _text(row.get("state_hash"), 128) or not _text(
            row.get("code_challenge"), 128
        ):
            raise ConnectorOAuthError("oauth_ledger_transaction_binding_missing")
        if "state" in row or "authorization_code" in row or "code_verifier" in row:
            raise ConnectorOAuthError("oauth_ledger_plaintext_secret_present")
    ledger["transactions"] = transactions
    ledger["last_success"] = (
        dict(ledger.get("last_success"))
        if isinstance(ledger.get("last_success"), dict)
        else {}
    )
    ledger["last_failure"] = (
        dict(ledger.get("last_failure"))
        if isinstance(ledger.get("last_failure"), dict)
        else {}
    )
    ledger["last_refresh_success"] = (
        dict(ledger.get("last_refresh_success"))
        if isinstance(ledger.get("last_refresh_success"), dict)
        else {}
    )
    ledger["last_refresh_failure"] = (
        dict(ledger.get("last_refresh_failure"))
        if isinstance(ledger.get("last_refresh_failure"), dict)
        else {}
    )
    governance = dict(ledger.get("governance") or {})
    governance.update(_default_ledger(project, connector)["governance"])
    ledger["governance"] = governance
    return ledger


def _save_ledger(project: str, connector: str, root: Path, ledger: dict[str, Any]) -> None:
    ledger["updated_at_utc"] = _now()
    try:
        _write_json_object_atomic(_ledger_path(project, connector, root), ledger)
    except OSError as exc:
        raise ConnectorOAuthError("oauth_ledger_write_failed") from exc


@contextmanager
def _oauth_transaction(
    root: Path,
    project: str,
    *,
    operation: str,
    actor: dict[str, Any],
):
    try:
        with knowledge_transaction(
            root,
            project,
            operation=operation,
            actor=_require_manage_actor(actor),
            wait_seconds=5.0,
        ):
            yield
    except KnowledgeTransactionBusy as exc:
        raise ConnectorOAuthError("oauth_transaction_busy") from exc


def _encrypted_verifier(value: str, root: Path) -> str:
    from .private_pilot_credentials_patch import (
        CredentialEncryptionUnavailableError,
        ensure_local_credential_encryption_key,
    )

    try:
        ensure_local_credential_encryption_key(root)
        ciphertext = encrypt(value)
    except (CredentialEncryptionUnavailableError, RuntimeError) as exc:
        raise ConnectorOAuthError("oauth_pkce_encryption_unavailable") from exc
    if not is_encrypted(ciphertext):
        raise ConnectorOAuthError("oauth_pkce_plaintext_persistence_refused")
    return ciphertext


def _decrypt_verifier(value: Any) -> str:
    ciphertext = _text(value, 4000)
    if not is_encrypted(ciphertext):
        raise ConnectorOAuthError("oauth_pkce_ciphertext_invalid")
    try:
        verifier = decrypt(ciphertext)
    except (ValueError, RuntimeError) as exc:
        raise ConnectorOAuthError("oauth_pkce_decryption_failed") from exc
    if not verifier:
        raise ConnectorOAuthError("oauth_pkce_verifier_empty")
    return verifier


def _prune_transactions(ledger: dict[str, Any]) -> None:
    rows = list(ledger.get("transactions") or [])
    while len(rows) > _MAX_TRANSACTION_COUNT:
        removable = next(
            (
                index
                for index, row in enumerate(rows)
                if _text(row.get("status"), 32) in _TERMINAL_TRANSACTION_STATUSES
            ),
            None,
        )
        if removable is None:
            raise ConnectorOAuthError("oauth_transaction_capacity_exhausted")
        rows.pop(removable)
    ledger["transactions"] = rows


def _transaction_by_id(ledger: dict[str, Any], transaction_id: str) -> dict[str, Any]:
    row = next(
        (
            item
            for item in ledger.get("transactions") or []
            if _text(item.get("transaction_id"), 160) == transaction_id
        ),
        None,
    )
    if row is None:
        raise ConnectorOAuthError("oauth_transaction_not_found")
    return row


def _terminalize(
    project: str,
    connector: str,
    transaction_id: str,
    *,
    status: str,
    result: Mapping[str, Any],
    root: Path,
    actor: dict[str, Any],
) -> dict[str, Any]:
    if status not in _TERMINAL_TRANSACTION_STATUSES:
        raise ConnectorOAuthError("oauth_terminal_status_invalid")
    with _oauth_transaction(
        root,
        project,
        operation="connector_oauth_finalize",
        actor=actor,
    ):
        ledger = _load_ledger(project, connector, root)
        row = _transaction_by_id(ledger, transaction_id)
        current_status = _text(row.get("status"), 32)
        if current_status in _TERMINAL_TRANSACTION_STATUSES:
            return dict(row)
        row["status"] = status
        row["completed_at_utc"] = _now()
        row["code_verifier_ciphertext"] = ""
        safe_result = {
            "authorization_status": _text(result.get("authorization_status"), 80),
            "permission_status": _text(result.get("permission_status"), 80),
            "failure_reason": _text(result.get("failure_reason"), 160),
            "granted_scopes": _scope_values(
                result.get("granted_scopes", []), "granted_scopes"
            ),
            "required_scopes": _scope_values(
                result.get("required_scopes", []), "required_scopes"
            ),
            "missing_scopes": _scope_values(
                result.get("missing_scopes", []), "missing_scopes"
            ),
            "completed_at_utc": _now(),
        }
        row["result"] = safe_result
        if status == "SUCCEEDED":
            ledger["last_success"] = {
                "transaction_id": transaction_id,
                **safe_result,
            }
        else:
            ledger["last_failure"] = {
                "transaction_id": transaction_id,
                "status": status,
                **safe_result,
            }
        _save_ledger(project, connector, root, ledger)
        return dict(row)


def _authorization_url(
    endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    params.extend(
        (
            ("response_type", "code"),
            ("client_id", client_id),
            ("redirect_uri", redirect_uri),
            ("scope", " ".join(scopes)),
            ("state", state),
            ("code_challenge", code_challenge),
            ("code_challenge_method", "S256"),
        )
    )
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(params))
    )


def _additional_scopes(config: Mapping[str, Any], value: Any) -> list[str]:
    requested = _scope_values(value, "additional_scopes")
    optional = set(config.get("optional_scopes") or [])
    if any(scope not in optional for scope in requested):
        raise ConnectorOAuthError("oauth_scope_not_declared_optional")
    return requested


def start_connector_oauth(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    additional_scopes: Any = None,
    transaction_ttl_seconds: Any = _DEFAULT_TRANSACTION_TTL_SECONDS,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    try:
        ttl = int(transaction_ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise ConnectorOAuthError("oauth_transaction_ttl_invalid") from exc
    if not 300 <= ttl <= _MAX_TRANSACTION_TTL_SECONDS:
        raise ConnectorOAuthError("oauth_transaction_ttl_invalid")
    instance, _manifest, config = _manifest_and_instance(project, connector, resolved_root)
    if not config:
        raise ConnectorOAuthError("oauth_not_supported_by_connector")
    profile_ref = _text(instance.get("connection_profile_ref"), 500)
    if not profile_ref:
        raise ConnectorOAuthError("oauth_connection_profile_required")
    try:
        current_profile = resolve_connector_profile(
            project,
            profile_ref,
            root=resolved_root,
        )
    except ConnectorProfileError as exc:
        raise ConnectorOAuthError("oauth_connection_profile_unavailable") from exc
    if _text(current_profile.get("auth_mode"), 80) != config["auth_mode"]:
        raise ConnectorOAuthError("oauth_auth_mode_mismatch")
    scopes = list(config["minimum_scopes"])
    for scope in _additional_scopes(config, additional_scopes):
        if scope not in scopes:
            scopes.append(scope)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    transaction_id = f"oauth-{uuid.uuid4().hex}"
    now = _utc_now()
    expires = now + timedelta(seconds=ttl)
    transaction = {
        "transaction_id": transaction_id,
        "state_hash": _fingerprint(state),
        "code_challenge": code_challenge,
        "code_verifier_ciphertext": _encrypted_verifier(verifier, resolved_root),
        "connector_type": _text(instance.get("connector_type"), 160),
        "manifest_version": _text(_manifest.version, 80),
        "profile_ref_fingerprint": _fingerprint(profile_ref),
        "actor_fingerprint": _actor_fingerprint(clean_actor),
        "redirect_uri": config["redirect_uri"],
        "requested_scopes": scopes,
        "required_scopes": list(config["minimum_scopes"]),
        "created_at_utc": _utc_text(now),
        "expires_at_utc": _utc_text(expires),
        "status": "PENDING",
        "completed_at_utc": "",
        "result": {},
    }
    with _oauth_transaction(
        resolved_root,
        project,
        operation="connector_oauth_start",
        actor=clean_actor,
    ):
        ledger = _load_ledger(project, connector, resolved_root)
        for prior in ledger["transactions"]:
            if _text(prior.get("status"), 32) == "PENDING":
                prior["status"] = "SUPERSEDED"
                prior["completed_at_utc"] = _now()
                prior["code_verifier_ciphertext"] = ""
                prior["result"] = {
                    "authorization_status": "SUPERSEDED",
                    "failure_reason": "new_authorization_started",
                    "granted_scopes": [],
                    "required_scopes": list(config["minimum_scopes"]),
                    "completed_at_utc": _now(),
                }
        ledger["transactions"].append(transaction)
        _prune_transactions(ledger)
        _save_ledger(project, connector, resolved_root, ledger)
    return {
        "schema": CONNECTOR_OAUTH_SCHEMA,
        "connector_instance_id": connector,
        "transaction_id": transaction_id,
        "authorization_url": _authorization_url(
            config["authorization_endpoint"],
            client_id=config["client_id"],
            redirect_uri=config["redirect_uri"],
            scopes=scopes,
            state=state,
            code_challenge=code_challenge,
        ),
        "requested_scopes": scopes,
        "expires_at_utc": _utc_text(expires),
        "state_returned_only_inside_authorization_url": True,
        "pkce_method": "S256",
        "state_persisted_as_hash": True,
        "credential_values_returned": False,
        "source_content_returned": False,
    }


def _default_token_requester(
    endpoint: str,
    body: Mapping[str, str],
    headers: Mapping[str, str],
    timeout: float,
) -> Mapping[str, Any]:
    encoded = urllib.parse.urlencode(dict(body)).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=encoded,
        headers=dict(headers),
        method="POST",
    )
    try:
        with safe_urlopen(request, timeout=timeout) as response:
            raw_status = getattr(response, "status", None)
            status = int(raw_status if raw_status is not None else response.getcode())
            raw = response.read()
    except (OSError, SsrfBlockedError) as exc:
        raise ConnectorOAuthError("oauth_token_exchange_transport_failed") from exc
    if status < 200 or status >= 300:
        try:
            error_payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ConnectorOAuthError("oauth_token_exchange_http_failed")
        if isinstance(error_payload, dict) and isinstance(
            error_payload.get("error"), str
        ) and error_payload["error"].strip():
            if body.get("grant_type") == "refresh_token":
                raise ConnectorOAuthError("oauth_refresh_token_rejected")
            raise ConnectorOAuthError("oauth_token_response_error")
        raise ConnectorOAuthError("oauth_token_exchange_http_failed")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectorOAuthError("oauth_token_response_invalid_json") from exc
    if not isinstance(payload, dict):
        raise ConnectorOAuthError("oauth_token_response_must_be_object")
    return payload


def _apply_client_auth(
    config: Mapping[str, Any],
    *,
    body: dict[str, str],
    headers: dict[str, str],
    current_profile: Mapping[str, Any],
) -> None:
    method = config["client_auth_method"]
    client_id = config["client_id"]
    if method == "none":
        body["client_id"] = client_id
        return
    secret_field = config["client_secret_field"]
    client_secret = _text(current_profile.get(secret_field), 4000)
    if not client_secret:
        raise ConnectorOAuthError("oauth_client_secret_missing")
    if method == "client_secret_post":
        body["client_id"] = client_id
        body["client_secret"] = client_secret
        return
    basic = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("ascii")
    headers["Authorization"] = f"Basic {basic}"


def _token_request_body(
    config: Mapping[str, Any],
    *,
    code: str,
    redirect_uri: str,
    verifier: str,
    current_profile: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    _apply_client_auth(
        config,
        body=body,
        headers=headers,
        current_profile=current_profile,
    )
    return body, headers


def _refresh_token_request_body(
    config: Mapping[str, Any],
    *,
    refresh_token: str,
    current_profile: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    _apply_client_auth(
        config,
        body=body,
        headers=headers,
        current_profile=current_profile,
    )
    return body, headers


def _token_text(payload: Mapping[str, Any], key: str, limit: int = 4000) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _token_expiry(payload: Mapping[str, Any]) -> str:
    if "expires_in" not in payload:
        return ""
    value = payload.get("expires_in")
    if isinstance(value, bool):
        raise ConnectorOAuthError("oauth_expires_in_invalid")
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ConnectorOAuthError("oauth_expires_in_invalid") from exc
    if not 1 <= seconds <= _MAX_TOKEN_EXPIRY_SECONDS:
        raise ConnectorOAuthError("oauth_expires_in_invalid")
    return _utc_text(_utc_now() + timedelta(seconds=seconds))


def _granted_scopes(payload: Mapping[str, Any], requested: list[str]) -> tuple[list[str], str]:
    raw = payload.get("scope")
    if raw is None:
        return list(requested), "NOT_REPORTED"
    if not isinstance(raw, str):
        raise ConnectorOAuthError("oauth_granted_scope_invalid")
    scopes = [item for item in raw.split() if item]
    if len(scopes) > _MAX_SCOPE_COUNT or len(set(scopes)) != len(scopes):
        raise ConnectorOAuthError("oauth_granted_scope_invalid")
    return scopes, "OBSERVED"


def _begin_callback(
    project: str,
    connector: str,
    *,
    state: str,
    root: Path,
    actor: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    state_hash = _fingerprint(state)
    actor_hash = _actor_fingerprint(actor)
    with _oauth_transaction(
        root,
        project,
        operation="connector_oauth_callback_begin",
        actor=actor,
    ):
        ledger = _load_ledger(project, connector, root)
        transaction = next(
            (
                row
                for row in ledger["transactions"]
                if _text(row.get("state_hash"), 128) == state_hash
            ),
            None,
        )
        if transaction is None:
            raise ConnectorOAuthError("oauth_state_invalid")
        transaction_id = _text(transaction.get("transaction_id"), 160)
        status = _text(transaction.get("status"), 32)
        if status != "PENDING":
            raise ConnectorOAuthError("oauth_state_replayed")
        if _text(transaction.get("actor_fingerprint"), 128) != actor_hash:
            raise ConnectorOAuthError("oauth_state_actor_mismatch")
        if _parse_utc(transaction.get("expires_at_utc"), "transaction_expiry") <= _utc_now():
            transaction["status"] = "EXPIRED"
            transaction["completed_at_utc"] = _now()
            transaction["code_verifier_ciphertext"] = ""
            transaction["result"] = {
                "authorization_status": "EXPIRED",
                "failure_reason": "oauth_state_expired",
                "granted_scopes": [],
                "required_scopes": list(
                    transaction.get("required_scopes")
                    or transaction.get("requested_scopes")
                    or []
                ),
                "completed_at_utc": _now(),
            }
            ledger["last_failure"] = {
                "transaction_id": transaction_id,
                "status": "EXPIRED",
                **dict(transaction["result"]),
            }
            _save_ledger(project, connector, root, ledger)
            raise ConnectorOAuthError("oauth_state_expired")
        verifier = _decrypt_verifier(transaction.get("code_verifier_ciphertext"))
        transaction["status"] = "PROCESSING"
        transaction["processing_at_utc"] = _now()
        _save_ledger(project, connector, root, ledger)
        return dict(transaction), verifier


def _failure_result(
    *,
    reason: str,
    status: str = "FAILED",
    granted_scopes: list[str] | None = None,
    required_scopes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "authorization_status": status,
        "permission_status": status,
        "failure_reason": reason,
        "granted_scopes": list(granted_scopes or []),
        "required_scopes": list(required_scopes or []),
    }


def handle_connector_oauth_callback(
    project_id: str,
    connector_instance_id: str,
    params: Mapping[str, Any],
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    token_requester: TokenRequester | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    if not isinstance(params, Mapping):
        raise ConnectorOAuthError("oauth_callback_parameters_invalid")
    state = _text(params.get("state"), 4000)
    if not state:
        raise ConnectorOAuthError("oauth_state_required")
    instance, _manifest, config = _manifest_and_instance(project, connector, resolved_root)
    if not config:
        raise ConnectorOAuthError("oauth_not_supported_by_connector")
    transaction, verifier = _begin_callback(
        project,
        connector,
        state=state,
        root=resolved_root,
        actor=clean_actor,
    )
    transaction_id = _text(transaction.get("transaction_id"), 160)
    required_scopes = list(
        transaction.get("required_scopes")
        or transaction.get("requested_scopes")
        or []
    )
    supplied_redirect_uri = _text(params.get("redirect_uri"), 2000)
    if supplied_redirect_uri and supplied_redirect_uri != _text(
        transaction.get("redirect_uri"), 2000
    ):
        result = _failure_result(
            reason="oauth_redirect_uri_mismatch",
            required_scopes=required_scopes,
        )
        _terminalize(
            project,
            connector,
            transaction_id,
            status="FAILED",
            result=result,
            root=resolved_root,
            actor=clean_actor,
        )
        raise ConnectorOAuthError("oauth_redirect_uri_mismatch")
    provider_error = _text(params.get("error"), 160)
    if provider_error:
        _terminalize(
            project,
            connector,
            transaction_id,
            status="FAILED",
            result=_failure_result(
                reason="oauth_provider_denied",
                required_scopes=required_scopes,
            ),
            root=resolved_root,
            actor=clean_actor,
        )
        raise ConnectorOAuthError("oauth_provider_denied")
    code = _text(params.get("code"), 4000)
    if not code:
        _terminalize(
            project,
            connector,
            transaction_id,
            status="FAILED",
            result=_failure_result(
                reason="oauth_authorization_code_missing",
                required_scopes=required_scopes,
            ),
            root=resolved_root,
            actor=clean_actor,
        )
        raise ConnectorOAuthError("oauth_authorization_code_required")
    profile_ref = _text(instance.get("connection_profile_ref"), 500)
    if not profile_ref or _fingerprint(profile_ref) != _text(
        transaction.get("profile_ref_fingerprint"), 128
    ):
        _terminalize(
            project,
            connector,
            transaction_id,
            status="FAILED",
            result=_failure_result(
                reason="oauth_profile_binding_changed",
                required_scopes=required_scopes,
            ),
            root=resolved_root,
            actor=clean_actor,
        )
        raise ConnectorOAuthError("oauth_profile_binding_changed")
    try:
        current_profile = resolve_connector_profile(
            project,
            profile_ref,
            root=resolved_root,
        )
        body, headers = _token_request_body(
            config,
            code=code,
            redirect_uri=_text(transaction.get("redirect_uri"), 2000),
            verifier=verifier,
            current_profile=current_profile,
        )
        requester = token_requester or _default_token_requester
        payload = requester(
            config["token_endpoint"],
            body,
            headers,
            float(timeout),
        )
        if not isinstance(payload, Mapping):
            raise ConnectorOAuthError("oauth_token_response_must_be_object")
        access_token = _token_text(payload, "access_token")
        if not access_token:
            raise ConnectorOAuthError("oauth_access_token_missing")
        refresh_token = _token_text(payload, "refresh_token")
        if not refresh_token:
            refresh_token = _text(
                current_profile.get(config["refresh_token_field"]),
                4000,
            )
        if not refresh_token:
            raise ConnectorOAuthError("oauth_refresh_token_missing")
        granted_scopes, permission_status = _granted_scopes(
            payload,
            required_scopes,
        )
        missing_scopes = [
            scope for scope in required_scopes if scope not in set(granted_scopes)
        ]
        if missing_scopes:
            mark_connector_reauthorization_required(
                project,
                connector,
                required=True,
                reason="oauth_permission_insufficient",
                root=resolved_root,
                actor=clean_actor,
            )
            result = _failure_result(
                reason="oauth_permission_insufficient",
                status="PERMISSION_INSUFFICIENT",
                granted_scopes=granted_scopes,
                required_scopes=required_scopes,
            )
            result["missing_scopes"] = missing_scopes
            _terminalize(
                project,
                connector,
                transaction_id,
                status="FAILED",
                result=result,
                root=resolved_root,
                actor=clean_actor,
            )
            raise ConnectorOAuthError("oauth_permission_insufficient")
        profile = dict(current_profile)
        profile[config["access_token_field"]] = access_token
        profile[config["refresh_token_field"]] = refresh_token
        if config["scope_field"] and isinstance(payload.get("scope"), str):
            profile[config["scope_field"]] = _text(payload.get("scope"), 4000)
        if config["token_type_field"] and _token_text(payload, "token_type", 80):
            profile[config["token_type_field"]] = _token_text(payload, "token_type", 80)
        rotated = rotate_connector_credentials(
            project,
            connector_instance_id=connector,
            profile=profile,
            root=resolved_root,
            actor=clean_actor,
            credential_expires_at_utc=_token_expiry(payload),
            preserve_credential_expiry=False,
        )
    except ConnectorOAuthError as exc:
        _terminalize(
            project,
            connector,
            transaction_id,
            status="FAILED",
            result=_failure_result(
                reason=str(exc),
                required_scopes=required_scopes,
            ),
            root=resolved_root,
            actor=clean_actor,
        )
        raise
    except ConnectorProfileError as exc:
        _terminalize(
            project,
            connector,
            transaction_id,
            status="FAILED",
            result=_failure_result(
                reason="oauth_profile_rotation_failed",
                required_scopes=required_scopes,
            ),
            root=resolved_root,
            actor=clean_actor,
        )
        raise ConnectorOAuthError("oauth_profile_rotation_failed") from exc
    result = {
        "authorization_status": "AUTHORIZED",
        "permission_status": permission_status,
        "granted_scopes": granted_scopes,
        "required_scopes": required_scopes,
    }
    _terminalize(
        project,
        connector,
        transaction_id,
        status="SUCCEEDED",
        result=result,
        root=resolved_root,
        actor=clean_actor,
    )
    return {
        "schema": CONNECTOR_OAUTH_SCHEMA,
        "connector_instance_id": connector,
        "transaction_id": transaction_id,
        "authorization_status": "AUTHORIZED",
        "permission_status": permission_status,
        "granted_scopes": granted_scopes,
        "required_scopes": required_scopes,
        "connection_profile": dict(rotated.get("connection_profile") or {}),
        "credential_values_returned": False,
        "authorization_code_persisted": False,
        "access_token_returned": False,
        "refresh_token_returned": False,
        "source_identity_preserved": True,
        "checkpoint_preserved": True,
        "source_content_returned": False,
        "remote_deletion_inferred": False,
    }


def _safe_refresh_result(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    return {
        "status": _text(raw.get("status"), 32),
        "permission_status": _text(raw.get("permission_status"), 80),
        "failure_reason": _text(raw.get("failure_reason"), 160),
        "granted_scopes": _scope_values(
            raw.get("granted_scopes", []), "granted_scopes"
        ),
        "required_scopes": _scope_values(
            raw.get("required_scopes", []), "required_scopes"
        ),
        "missing_scopes": _scope_values(
            raw.get("missing_scopes", []), "missing_scopes"
        ),
        "completed_at_utc": _text(raw.get("completed_at_utc"), 80),
    }


def _record_refresh_outcome(
    project: str,
    connector: str,
    *,
    success: bool,
    result: Mapping[str, Any],
    root: Path,
    actor: dict[str, Any],
) -> dict[str, Any]:
    safe_result = _safe_refresh_result(
        {
            **dict(result),
            "status": "SUCCEEDED" if success else "FAILED",
            "completed_at_utc": _text(result.get("completed_at_utc"), 80)
            or _now(),
        }
    )
    with _oauth_transaction(
        root,
        project,
        operation="connector_oauth_refresh_finalize",
        actor=actor,
    ):
        ledger = _load_ledger(project, connector, root)
        key = "last_refresh_success" if success else "last_refresh_failure"
        ledger[key] = safe_result
        _save_ledger(project, connector, root, ledger)
    return safe_result


def _refresh_requires_reauthorization(reason: str) -> bool:
    return reason in {
        "oauth_refresh_token_missing",
        "oauth_refresh_token_rejected",
        "oauth_client_secret_missing",
        "oauth_permission_insufficient",
    }


def _request_refresh_token(
    requester: TokenRequester,
    endpoint: str,
    body: Mapping[str, str],
    headers: Mapping[str, str],
    timeout: float,
) -> Mapping[str, Any]:
    try:
        payload = requester(endpoint, body, headers, timeout)
    except ConnectorOAuthError:
        raise
    except (OSError, SsrfBlockedError) as exc:
        raise ConnectorOAuthError("oauth_refresh_transport_failed") from exc
    if not isinstance(payload, Mapping):
        raise ConnectorOAuthError("oauth_token_response_must_be_object")
    if _token_text(payload, "error", 160):
        raise ConnectorOAuthError("oauth_refresh_token_rejected")
    return payload


def refresh_connector_oauth(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    token_requester: TokenRequester | None = None,
    timeout: float = 30.0,
    expiring_within_seconds: int = 86_400,
    force: bool = False,
) -> dict[str, Any]:
    """Refresh an expiring OAuth access token without changing source or cursor state."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    clean_actor = _require_manage_actor(actor)
    if not isinstance(expiring_within_seconds, int) or expiring_within_seconds < 0:
        raise ConnectorOAuthError("oauth_refresh_expiry_window_invalid")
    if not isinstance(force, bool):
        raise ConnectorOAuthError("oauth_refresh_force_invalid")
    instance, _manifest, config = _manifest_and_instance(
        project,
        connector,
        resolved_root,
    )
    if not config:
        return {
            "schema": CONNECTOR_OAUTH_SCHEMA,
            "connector_instance_id": connector,
            "supported": False,
            "attempted": False,
            "refreshed": False,
            "refresh_status": "NOT_SUPPORTED",
            "credential_values_returned": False,
            "source_identity_preserved": True,
            "checkpoint_preserved": True,
            "remote_deletion_inferred": False,
        }
    profile_ref = _text(instance.get("connection_profile_ref"), 500)
    if not profile_ref:
        raise ConnectorOAuthError("oauth_connection_profile_required")
    expiry = connector_credential_expiry_status(
        project,
        connector,
        root=resolved_root,
        expiring_within_seconds=expiring_within_seconds,
    )
    expiry_status = _text(expiry.get("status"), 64) or "UNKNOWN"
    if not force and expiry_status not in {"EXPIRING", "EXPIRED"}:
        return {
            "schema": CONNECTOR_OAUTH_SCHEMA,
            "connector_instance_id": connector,
            "supported": True,
            "attempted": False,
            "refreshed": False,
            "refresh_status": "NOT_DUE",
            "credential_status": expiry_status,
            "credential_expires_at_utc": _text(
                expiry.get("credential_expires_at_utc"), 80
            ),
            "credential_values_returned": False,
            "source_identity_preserved": True,
            "checkpoint_preserved": True,
            "remote_deletion_inferred": False,
        }
    current_profile = resolve_connector_profile(
        project,
        profile_ref,
        root=resolved_root,
    )
    if _text(current_profile.get("auth_mode"), 80) != config["auth_mode"]:
        raise ConnectorOAuthError("oauth_auth_mode_mismatch")
    refresh_token = _text(current_profile.get(config["refresh_token_field"]), 4000)
    required_scopes = list(config["minimum_scopes"])
    ledger = _load_ledger(project, connector, resolved_root)
    prior_success = dict(ledger.get("last_success") or {})
    prior_refresh_success = dict(ledger.get("last_refresh_success") or {})
    prior_scopes = _scope_values(
        prior_refresh_success.get("granted_scopes")
        or prior_success.get("granted_scopes")
        or required_scopes,
        "granted_scopes",
    )
    granted_scopes = list(prior_scopes)
    missing_scopes: list[str] = []
    requester = token_requester or _default_token_requester
    try:
        if not refresh_token:
            raise ConnectorOAuthError("oauth_refresh_token_missing")
        body, headers = _refresh_token_request_body(
            config,
            refresh_token=refresh_token,
            current_profile=current_profile,
        )
        payload = _request_refresh_token(
            requester,
            config["token_endpoint"],
            body,
            headers,
            float(timeout),
        )
        access_token = _token_text(payload, "access_token")
        if not access_token:
            raise ConnectorOAuthError("oauth_access_token_missing")
        rotated_refresh_token = _token_text(payload, "refresh_token") or refresh_token
        granted_scopes, permission_status = _granted_scopes(
            payload,
            prior_scopes,
        )
        missing_scopes = [
            scope for scope in required_scopes if scope not in set(granted_scopes)
        ]
        if missing_scopes:
            raise ConnectorOAuthError("oauth_permission_insufficient")
        profile = dict(current_profile)
        profile[config["access_token_field"]] = access_token
        profile[config["refresh_token_field"]] = rotated_refresh_token
        if config["scope_field"] and isinstance(payload.get("scope"), str):
            profile[config["scope_field"]] = _text(payload.get("scope"), 4000)
        if config["token_type_field"] and _token_text(payload, "token_type", 80):
            profile[config["token_type_field"]] = _token_text(
                payload,
                "token_type",
                80,
            )
        rotated = rotate_connector_credentials(
            project,
            connector_instance_id=connector,
            profile=profile,
            root=resolved_root,
            actor=clean_actor,
            credential_expires_at_utc=_token_expiry(payload),
            preserve_credential_expiry=False,
        )
        result = {
            "permission_status": permission_status,
            "granted_scopes": granted_scopes,
            "required_scopes": required_scopes,
            "missing_scopes": [],
        }
        _record_refresh_outcome(
            project,
            connector,
            success=True,
            result=result,
            root=resolved_root,
            actor=clean_actor,
        )
        return {
            "schema": CONNECTOR_OAUTH_SCHEMA,
            "connector_instance_id": connector,
            "supported": True,
            "attempted": True,
            "refreshed": True,
            "refresh_status": "SUCCEEDED",
            "credential_status": "ACTIVE",
            "credential_expires_at_utc": _text(
                (rotated.get("connection_profile") or {}).get(
                    "credential_expires_at_utc"
                ),
                80,
            ),
            "permission_status": permission_status,
            "granted_scopes": granted_scopes,
            "required_scopes": required_scopes,
            "credential_values_returned": False,
            "source_identity_preserved": True,
            "checkpoint_preserved": True,
            "remote_deletion_inferred": False,
        }
    except ConnectorOAuthError as exc:
        reason = str(exc)
        if _refresh_requires_reauthorization(reason):
            mark_connector_reauthorization_required(
                project,
                connector,
                required=True,
                reason=reason,
                root=resolved_root,
                actor=clean_actor,
            )
        _record_refresh_outcome(
            project,
            connector,
            success=False,
            result={
                "permission_status": (
                    "PERMISSION_INSUFFICIENT"
                    if reason == "oauth_permission_insufficient"
                    else "REAUTHORIZATION_REQUIRED"
                    if _refresh_requires_reauthorization(reason)
                    else "NOT_MEASURED"
                ),
                "failure_reason": reason,
                "granted_scopes": granted_scopes,
                "required_scopes": required_scopes,
                "missing_scopes": missing_scopes,
            },
            root=resolved_root,
            actor=clean_actor,
        )
        raise
    except ConnectorProfileError as exc:
        reason = "oauth_profile_rotation_failed"
        _record_refresh_outcome(
            project,
            connector,
            success=False,
            result={
                "permission_status": "NOT_MEASURED",
                "failure_reason": reason,
                "granted_scopes": granted_scopes,
                "required_scopes": required_scopes,
                "missing_scopes": missing_scopes,
            },
            root=resolved_root,
            actor=clean_actor,
        )
        raise ConnectorOAuthError(reason) from exc


def _safe_failure(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    return {
        "transaction_id": _text(raw.get("transaction_id"), 160),
        "status": _text(raw.get("status"), 32),
        "failure_reason": _text(raw.get("failure_reason"), 160),
        "permission_status": _text(raw.get("permission_status"), 80),
        "granted_scopes": _scope_values(raw.get("granted_scopes", []), "granted_scopes"),
        "required_scopes": _scope_values(raw.get("required_scopes", []), "required_scopes"),
        "missing_scopes": _scope_values(raw.get("missing_scopes", []), "missing_scopes"),
        "completed_at_utc": _text(raw.get("completed_at_utc"), 80),
    }


def project_connector_oauth(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _identifier(connector_instance_id, "connector_instance_id")
    instance, manifest, config = _manifest_and_instance(project, connector, resolved_root)
    if not config:
        return {
            "schema": CONNECTOR_OAUTH_SCHEMA,
            "connector_instance_id": connector,
            "connector_type": _text(manifest.connector_type, 160),
            "supported": False,
            "configured": False,
            "status": "NOT_SUPPORTED",
            "required_scopes": [],
            "granted_scopes": [],
            "permission_status": "NOT_APPLICABLE",
            "last_authorized_at_utc": "",
            "last_failure": None,
            "automatic_refresh_supported": False,
            "automatic_refresh_status": "NOT_SUPPORTED",
            "last_refresh_success": None,
            "last_refresh_failure": None,
            "pending_transaction_count": 0,
            "authorization_code_returned": False,
            "access_token_returned": False,
            "refresh_token_returned": False,
            "credential_values_returned": False,
            "source_identity_preserved": True,
            "checkpoint_preserved": True,
            "remote_deletion_inferred": False,
            "governance": dict(_default_ledger(project, connector)["governance"]),
        }
    try:
        ledger = _load_ledger(project, connector, resolved_root)
    except ConnectorOAuthError as exc:
        return {
            "schema": CONNECTOR_OAUTH_SCHEMA,
            "connector_instance_id": connector,
            "connector_type": _text(instance.get("connector_type"), 160),
            "supported": True,
            "status": "NOT_AVAILABLE",
            "error_code": str(exc).split(":", 1)[0],
            "required_scopes": list(config["minimum_scopes"]),
            "governance": dict(_default_ledger(project, connector)["governance"]),
        }
    profile_ref = _text(instance.get("connection_profile_ref"), 500)
    profile_status = "NOT_CONFIGURED"
    profile = {}
    if profile_ref:
        try:
            profile = connector_credential_expiry_status(
                project,
                connector,
                root=resolved_root,
            )
            profile_status = _text(profile.get("status"), 64) or "UNKNOWN"
        except ConnectorProfileError:
            profile_status = "NOT_CONFIGURED"
    success = dict(ledger.get("last_success") or {})
    failure = _safe_failure(ledger.get("last_failure"))
    refresh_success = _safe_refresh_result(ledger.get("last_refresh_success"))
    refresh_failure = _safe_refresh_result(ledger.get("last_refresh_failure"))
    if profile_status == "REAUTHORIZATION_REQUIRED":
        status = "REAUTHORIZATION_REQUIRED"
    elif profile_status in {"EXPIRED", "EXPIRING"}:
        status = profile_status
    elif profile_status == "REVOKED":
        status = "REVOKED"
    elif not success:
        status = "NOT_AUTHORIZED"
    else:
        status = "AUTHORIZED"
    failure_is_latest = (
        not success
        or _text(failure.get("completed_at_utc"), 80)
        >= _text(success.get("completed_at_utc"), 80)
    )
    if (
        failure_is_latest
        and _text(failure.get("permission_status"), 80)
        == "PERMISSION_INSUFFICIENT"
    ):
        status = "PERMISSION_INSUFFICIENT"
    refresh_permission_status = _text(
        refresh_success.get("permission_status"), 80
    ) or _text(refresh_failure.get("permission_status"), 80)
    refresh_success_at = _text(refresh_success.get("completed_at_utc"), 80)
    refresh_failure_at = _text(refresh_failure.get("completed_at_utc"), 80)
    refresh_failure_is_latest = bool(
        refresh_failure_at and refresh_failure_at >= refresh_success_at
    )
    refresh_status = (
        _text(
            refresh_failure.get("status") if refresh_failure_is_latest else refresh_success.get("status"),
            32,
        )
        or "NOT_MEASURED"
    )
    granted_scopes = _scope_values(
        refresh_success.get("granted_scopes")
        or success.get("granted_scopes", []),
        "granted_scopes",
    )
    missing_scopes = _scope_values(
        refresh_failure.get("missing_scopes")
        or failure.get("missing_scopes", []),
        "missing_scopes",
    )
    return {
        "schema": CONNECTOR_OAUTH_SCHEMA,
        "connector_instance_id": connector,
        "connector_type": _text(manifest.connector_type, 160),
        "supported": True,
        "configured": bool(profile_ref),
        "status": status,
        "credential_status": profile_status,
        "required_scopes": list(config["minimum_scopes"]),
        "granted_scopes": granted_scopes,
        "missing_scopes": missing_scopes,
        "permission_status": _text(
            success.get("permission_status"), 80
        )
        or _text(failure.get("permission_status"), 80)
        or refresh_permission_status
        or "NOT_MEASURED",
        "last_authorized_at_utc": _text(success.get("completed_at_utc"), 80),
        "last_failure": failure if failure.get("transaction_id") else None,
        "automatic_refresh_supported": bool(config.get("refresh_token_field")),
        "automatic_refresh_status": refresh_status,
        "last_refresh_at_utc": _text(
            refresh_failure.get("completed_at_utc")
            if refresh_failure_is_latest
            else refresh_success.get("completed_at_utc"),
            80,
        ),
        "last_refresh_success": (
            refresh_success if refresh_success.get("completed_at_utc") else None
        ),
        "last_refresh_failure": (
            refresh_failure if refresh_failure.get("completed_at_utc") else None
        ),
        "pending_transaction_count": sum(
            _text(row.get("status"), 32) in {"PENDING", "PROCESSING"}
            for row in ledger["transactions"]
        ),
        "authorization_code_returned": False,
        "access_token_returned": False,
        "refresh_token_returned": False,
        "credential_values_returned": False,
        "source_identity_preserved": True,
        "checkpoint_preserved": True,
        "remote_deletion_inferred": False,
        "governance": dict(ledger.get("governance") or {}),
    }


__all__ = [
    "CONNECTOR_OAUTH_LEDGER_SCHEMA",
    "CONNECTOR_OAUTH_SCHEMA",
    "ConnectorOAuthError",
    "TokenRequester",
    "handle_connector_oauth_callback",
    "project_connector_oauth",
    "refresh_connector_oauth",
    "start_connector_oauth",
]
