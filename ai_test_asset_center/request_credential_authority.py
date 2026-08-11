"""Fail-closed authority for credential references embedded in request bodies.

Compile artifacts carry opaque credential coordinates, never secret values.
Immediately before transport this module resolves those coordinates through the
project's existing credential authorities:

* ``secret_ref:test_accounts:<selector>`` -> declared test-account catalog;
* ``secret_ref:service:<service>:<role>`` -> EnterpriseCredentialManager.

The request field itself constrains which secret type may be used. Password
fields never receive an API key, API-key fields never receive a password, and a
generic ``secret``/``clientSecret`` is resolved only when exactly one declared
secret material exists. Test-account material is then passed through the shared
at-rest decryption authority; an ``enc$v1$`` envelope is never sent to the target.
Unresolved references remain a named GAP and receipts never contain secret values.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .declared_credential_material import resolve_declared_credential_material

SCHEMA_VERSION = "qualibug.request-credential-authority.v1"
_TEST_ACCOUNT_REF_RE = re.compile(r"^secret_ref:test_accounts:([^:\s]+)$")
_SERVICE_REF_RE = re.compile(r"^secret_ref:service:([^:\s]+):([^:\s]+)$")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _field_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).casefold())


def _credential_kind(field_name: str) -> str:
    token = _field_token(field_name)
    if token in {
        "password",
        "passwd",
        "passphrase",
        "newpassword",
        "oldpassword",
        "currentpassword",
    } or token.endswith("password"):
        return "password"
    if token in {"apikey", "apiaccesskey", "accesskey"} or token.endswith("apikey"):
        return "api_key"
    if token in {"secret", "clientsecret", "credential"} or token.endswith("secret"):
        return "generic_secret"
    return "credential"


def _test_account_rows(root: Any, project: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            payload = {}
        if isinstance(payload, dict):
            rows = [
                dict(row)
                for row in (
                    payload.get("accounts")
                    or payload.get("actors")
                    or payload.get("users")
                    or []
                )
                if isinstance(row, dict)
            ]
            if not rows:
                rows = [
                    {
                        **child,
                        "account_ref": _text(child.get("account_ref") or key),
                    }
                    for key, child in payload.items()
                    if isinstance(child, dict)
                    and key not in {"schema", "schema_version", "meta"}
                ]
        elif isinstance(payload, list):
            rows = [dict(row) for row in payload if isinstance(row, dict)]
    if rows:
        return rows
    try:
        from .experiment_runtime_credentials import _parse_test_accounts_md

        return [
            dict(row)
            for row in _parse_test_accounts_md(Path(root), str(project))
            if isinstance(row, dict)
        ]
    except Exception:
        return []


def _account_matches(row: dict[str, Any], selector: str) -> bool:
    wanted = _text(selector)
    if not wanted:
        return False
    return wanted in {
        _text(row.get(key))
        for key in (
            "account_ref",
            "email",
            "username",
            "profile",
            "name",
            "id",
        )
        if _text(row.get(key))
    }


def _unique_nonempty(values: list[Any]) -> str:
    unique = list(dict.fromkeys(_text(value) for value in values if _text(value)))
    return unique[0] if len(unique) == 1 else ""


def _declared_account_secret_candidate(
    row: dict[str, Any],
    field_name: str,
) -> tuple[str, str, str]:
    """Select one declared secret *coordinate* before decrypting its material."""

    kind = _credential_kind(field_name)
    if kind == "password":
        value = _unique_nonempty(
            [row.get("password"), row.get("pass"), row.get("passphrase")]
        )
        return value, "password" if value else "", (
            "" if value else "REQUEST_CREDENTIAL_MATERIAL_UNRESOLVED"
        )
    if kind == "api_key":
        value = _unique_nonempty(
            [row.get("api_key"), row.get("apiKey"), row.get("access_key")]
        )
        return value, "api_key" if value else "", (
            "" if value else "REQUEST_CREDENTIAL_MATERIAL_UNRESOLVED"
        )

    candidates = [
        ("password", row.get("password") or row.get("pass") or row.get("passphrase")),
        ("api_key", row.get("api_key") or row.get("apiKey") or row.get("access_key")),
        ("client_secret", row.get("client_secret") or row.get("clientSecret")),
        ("credential", row.get("credential")),
    ]
    nonempty = [(name, _text(value)) for name, value in candidates if _text(value)]
    unique_values = list(dict.fromkeys(value for _, value in nonempty))
    if len(unique_values) != 1:
        return "", "", "REQUEST_CREDENTIAL_MATERIAL_UNRESOLVED"
    kinds = list(
        dict.fromkeys(name for name, value in nonempty if value == unique_values[0])
    )
    material_kind = kinds[0] if len(kinds) == 1 else "unique_declared_secret"
    return unique_values[0], material_kind, ""


def _account_secret(
    row: dict[str, Any],
    field_name: str,
    *,
    root: Any,
) -> tuple[str, str, str, dict[str, Any]]:
    raw, material_kind, selection_reason = _declared_account_secret_candidate(
        row,
        field_name,
    )
    if selection_reason:
        return "", "", selection_reason, {}
    resolved, material_receipt = resolve_declared_credential_material(
        raw,
        root=Path(root),
    )
    if not resolved:
        reason = _text(material_receipt.get("reason_code"))
        if reason == "DECLARED_CREDENTIAL_DECRYPT_FAILED":
            reason = "REQUEST_CREDENTIAL_DECRYPT_FAILED"
        elif reason.startswith("DECLARED_CREDENTIAL_KEY_"):
            reason = "REQUEST_CREDENTIAL_DECRYPT_KEY_UNAVAILABLE"
        else:
            reason = "REQUEST_CREDENTIAL_MATERIAL_UNRESOLVED"
        return "", "", reason, material_receipt
    return resolved, material_kind, "", material_receipt


def _resolve_test_account_ref(
    selector: str,
    *,
    field_name: str,
    root: Any,
    project: str,
) -> tuple[str, str, str, dict[str, Any]]:
    matches = [
        row for row in _test_account_rows(root, project) if _account_matches(row, selector)
    ]
    if len(matches) != 1:
        return "", "", (
            "REQUEST_CREDENTIAL_ACCOUNT_NOT_FOUND"
            if not matches
            else "REQUEST_CREDENTIAL_ACCOUNT_AMBIGUOUS"
        ), {}
    value, material_kind, reason, material_receipt = _account_secret(
        matches[0],
        field_name,
        root=root,
    )
    if not value:
        return "", "", reason or "REQUEST_CREDENTIAL_MATERIAL_UNRESOLVED", material_receipt
    return value, f"test_accounts:{material_kind}", "", material_receipt


def _service_secret(credential: Any, field_name: str) -> tuple[str, str]:
    kind = _credential_kind(field_name)
    password = _text(getattr(credential, "password", ""))
    api_key = _text(getattr(credential, "api_key", ""))
    if kind == "password":
        return (password, "service_password") if password else ("", "")
    if kind == "api_key":
        return (api_key, "service_api_key") if api_key else ("", "")
    values = list(dict.fromkeys(value for value in (password, api_key) if value))
    if len(values) != 1:
        return "", ""
    source = "service_password" if values[0] == password else "service_api_key"
    return values[0], source


def _resolve_service_ref(
    service: str,
    role: str,
    *,
    field_name: str,
    root: Any,
    project: str,
) -> tuple[str, str, str]:
    try:
        from .experiment_runtime_credentials import _configured_credential_manager

        manager = _configured_credential_manager(Path(root), str(project))
    except Exception:
        manager = None
    if manager is None:
        return "", "", "REQUEST_CREDENTIAL_SERVICE_MANAGER_UNAVAILABLE"
    credential = manager.store.get(_text(service), _text(role))
    if credential is None:
        return "", "", "REQUEST_CREDENTIAL_SERVICE_ROLE_NOT_FOUND"
    value, source = _service_secret(credential, field_name)
    if not value:
        return "", "", "REQUEST_CREDENTIAL_MATERIAL_UNRESOLVED"
    return value, source, ""


def _resolve_scalar(
    value: str,
    *,
    field_name: str,
    root: Any,
    project: str,
) -> tuple[str, dict[str, Any] | None]:
    text = _text(value)
    test_match = _TEST_ACCOUNT_REF_RE.match(text)
    service_match = _SERVICE_REF_RE.match(text)
    if not test_match and not service_match:
        if text.startswith("secret_ref:"):
            return text, {
                "credential_ref": text,
                "field": _text(field_name),
                "status": "UNRESOLVED",
                "reason_code": "REQUEST_CREDENTIAL_REFERENCE_UNSUPPORTED",
                "material_source": "",
                "secret_value_persisted": False,
            }
        return value, None

    material_receipt: dict[str, Any] = {}
    if test_match:
        resolved, source, reason, material_receipt = _resolve_test_account_ref(
            test_match.group(1),
            field_name=field_name,
            root=root,
            project=project,
        )
    else:
        resolved, source, reason = _resolve_service_ref(
            service_match.group(1),
            role=service_match.group(2),
            field_name=field_name,
            root=root,
            project=project,
        )
    receipt = {
        "credential_ref": text,
        "field": _text(field_name),
        "credential_kind": _credential_kind(field_name),
        "status": "RESOLVED" if resolved else "UNRESOLVED",
        "reason_code": reason,
        "material_source": source,
        "encrypted_at_rest": bool(material_receipt.get("encrypted_at_rest")),
        "material_authority": _text(material_receipt.get("authority")),
        "material_key_source": _text(material_receipt.get("key_source")),
        "secret_value_persisted": False,
    }
    return (resolved if resolved else text), receipt


def resolve_request_credentials(
    value: Any,
    *,
    root: Any,
    project: str,
) -> tuple[Any, dict[str, Any]]:
    """Resolve all request credential refs and return a non-secret receipt."""

    rows: list[dict[str, Any]] = []

    def walk(node: Any, field_name: str = "") -> Any:
        if isinstance(node, dict):
            return {
                key: walk(child, str(key))
                for key, child in node.items()
            }
        if isinstance(node, list):
            return [walk(child, field_name) for child in node]
        if not isinstance(node, str):
            return node
        resolved, receipt = _resolve_scalar(
            node,
            field_name=field_name,
            root=root,
            project=project,
        )
        if receipt is not None:
            rows.append(receipt)
        return resolved

    resolved_value = walk(value)
    unresolved = [row for row in rows if row.get("status") == "UNRESOLVED"]
    return resolved_value, {
        "schema_version": SCHEMA_VERSION,
        "status": "UNRESOLVED" if unresolved else "RESOLVED",
        "reference_count": len(rows),
        "resolved_count": len(rows) - len(unresolved),
        "unresolved_count": len(unresolved),
        "rows": rows,
        "secret_value_persisted": False,
    }


__all__ = ["SCHEMA_VERSION", "resolve_request_credentials"]
