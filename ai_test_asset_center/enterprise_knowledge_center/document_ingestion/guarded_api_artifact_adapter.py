"""Security guard for source-declared API artifacts.

The structural adapter must never receive live credential values. This guard sanitizes the
parsed source tree before the canonical JSON projection and JSON-Pointer blocks are built.
It is the only registered API-artifact adapter; the underlying adapter remains a structure
primitive and is not registered independently.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from .api_artifact_adapter import (
    API_ARTIFACT_REDACTION_SCHEMA,
    ApiArtifactDocumentAdapter,
    _artifact_kind,
    _decode_payload,
)
from .contract import DocumentSource

_GUARD_SCHEMA = "qualibug.api-artifact-secret-guard.v1"
_REDACTED = "<redacted>"
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:authorization|proxy[-_ ]?authorization|cookie|"
    r"set[-_ ]?cookie|token|access[-_ ]?token|refresh[-_ ]?token|id[-_ ]?token|"
    r"api[-_ ]?key|apikey|client[-_ ]?secret|password|passwd|secret|bearer|"
    r"private[-_ ]?key|session[-_ ]?id)(?:$|[^a-z0-9])"
)
_VALUE_FIELDS = {"value", "current", "initial", "default"}
_BODY_FIELDS = {"raw", "text", "body"}
_URL_FIELDS = {"url"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sensitive_name(value: Any) -> bool:
    return bool(_SENSITIVE_NAME_RE.search(_text(value)))


def _redact_url(value: str) -> tuple[str, int]:
    text = str(value or "")
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return text, 0
    if not parsed.query:
        return text, 0
    count = 0
    pairs: list[tuple[str, str]] = []
    for key, item_value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if _sensitive_name(key):
            pairs.append((key, _REDACTED))
            count += 1
        else:
            pairs.append((key, item_value))
    return (
        urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(pairs),
                parsed.fragment,
            )
        ),
        count,
    )


def _redact_form_text(value: str) -> tuple[str, int]:
    text = str(value or "")
    if "=" not in text or "\n" in text or "\r" in text:
        return text, 0
    try:
        pairs = urllib.parse.parse_qsl(text, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return text, 0
    if not pairs:
        return text, 0
    count = 0
    redacted: list[tuple[str, str]] = []
    for key, item_value in pairs:
        if _sensitive_name(key):
            redacted.append((key, _REDACTED))
            count += 1
        else:
            redacted.append((key, item_value))
    return urllib.parse.urlencode(redacted), count


def _sanitize_embedded(value: str) -> tuple[str, int]:
    text = str(value or "")
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if parsed is not None:
            sanitized, count = _sanitize(parsed)
            return (
                json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")),
                count,
            )
    url_value, url_count = _redact_url(text)
    if url_count:
        return url_value, url_count
    return _redact_form_text(text)


def _sanitize(value: Any, *, key_hint: str = "") -> tuple[Any, int]:
    if isinstance(value, list):
        rows: list[Any] = []
        count = 0
        for item in value:
            sanitized, child_count = _sanitize(item, key_hint=key_hint)
            rows.append(sanitized)
            count += child_count
        return rows, count

    if not isinstance(value, dict):
        if _sensitive_name(key_hint):
            return _REDACTED, 1
        return value, 0

    identity_name = _text(value.get("name") or value.get("key"))
    identity_sensitive = _sensitive_name(identity_name)
    result: dict[str, Any] = {}
    count = 0
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        key_lower = key.lower()
        if identity_sensitive and key_lower in _VALUE_FIELDS:
            result[key] = _REDACTED
            count += 1
            continue
        if _sensitive_name(key) and not isinstance(raw_value, (dict, list)):
            result[key] = _REDACTED
            count += 1
            continue
        if key_lower in _URL_FIELDS and isinstance(raw_value, str):
            result[key], child_count = _redact_url(raw_value)
            count += child_count
            continue
        if key_lower in _BODY_FIELDS and isinstance(raw_value, str):
            result[key], child_count = _sanitize_embedded(raw_value)
            count += child_count
            continue
        result[key], child_count = _sanitize(raw_value, key_hint=key)
        count += child_count
    return result, count


class GuardedApiArtifactDocumentAdapter(ApiArtifactDocumentAdapter):
    """Registered API-artifact authority with mandatory pre-parse redaction."""

    name = "guarded-api-artifact-json-pointer-structure"
    parser_version = "2"

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        payload, _decoded, error = _decode_payload(source)
        if payload is None or not _artifact_kind(payload):
            return super().extract(source)

        sanitized, redaction_count = _sanitize(payload)
        safe_source = DocumentSource(
            source_id=source.source_id,
            filename=source.filename,
            data=json.dumps(sanitized, ensure_ascii=False).encode("utf-8"),
            declared_mime=source.declared_mime,
            legacy_text="",
        )
        document_ir = super().extract(safe_source)
        receipt = dict(document_ir.get("structure_receipt") or {})
        receipt.update(
            {
                "api_artifact_secret_guard": True,
                "api_artifact_secret_guard_schema": _GUARD_SCHEMA,
                "redaction_schema": API_ARTIFACT_REDACTION_SCHEMA,
                "redaction_count": int(redaction_count),
                "pre_parse_secret_redaction_applied": True,
                "credential_values_retained": False,
                "original_source_bytes_exposed_to_structure_adapter": False,
            }
        )
        document_ir["structure_receipt"] = receipt
        return document_ir


__all__ = ["GuardedApiArtifactDocumentAdapter"]
