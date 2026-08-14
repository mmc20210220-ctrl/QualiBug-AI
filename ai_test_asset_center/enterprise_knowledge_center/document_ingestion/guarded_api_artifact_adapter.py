"""Security and exact-structure guard for source-declared API artifacts.

The structural primitive never receives live credential values. This is the only registered
API-artifact adapter: it sanitizes the parsed tree, delegates operation/Postman/HAR structure
to the existing adapter, normalizes every JSON Pointer to the canonical exact-block address,
and enriches OpenAPI with field-level schema evidence.
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
from .openapi_path_parameter_projection import (
    apply_openapi_path_parameter_schema_projection,
)
from .openapi_schema_projection import (
    apply_openapi_schema_projection,
    normalize_api_json_pointer_locators,
)

_GUARD_SCHEMA = "qualibug.api-artifact-secret-guard.v2"
_REDACTED = "<redacted>"
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:authorization|proxy[-_ ]?authorization|cookie|"
    r"set[-_ ]?cookie|token|access[-_ ]?token|refresh[-_ ]?token|id[-_ ]?token|"
    r"api[-_ ]?key|apikey|client[-_ ]?secret|password|passwd|secret|bearer|"
    r"private[-_ ]?key|session[-_ ]?id)(?:$|[^a-z0-9])"
)
_VALUE_FIELDS = {"value", "current", "initial", "default", "const", "example"}
_COLLECTION_VALUE_FIELDS = {"enum", "examples"}
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


def _redacted_collection(value: Any) -> tuple[Any, int]:
    if isinstance(value, list):
        return [_REDACTED for _item in value], len(value)
    if isinstance(value, dict):
        return {str(key): _REDACTED for key in value}, len(value)
    return _REDACTED, 1


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
    parent_sensitive = _sensitive_name(key_hint)
    value_container_sensitive = identity_sensitive or parent_sensitive
    result: dict[str, Any] = {}
    count = 0
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        key_lower = key.lower()
        if value_container_sensitive and key_lower in _VALUE_FIELDS:
            result[key] = _REDACTED
            count += 1
            continue
        if value_container_sensitive and key_lower in _COLLECTION_VALUE_FIELDS:
            result[key], child_count = _redacted_collection(raw_value)
            count += child_count
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


def _apply_legacy_json_pointer_locator_alias(
    document_ir: dict[str, Any],
    *,
    filename: str,
) -> dict[str, Any]:
    """Keep old locator suffixes while retaining the new exact ``#block=`` marker."""

    result = dict(document_ir or {})
    for collection_name in ("blocks", "sections", "tables", "pages"):
        rows: list[dict[str, Any]] = []
        for raw in result.get(collection_name) or []:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            pointer = _text(row.get("json_pointer"))
            if pointer:
                exact_locator = f"{filename}#block=json-pointer:{pointer};legacy=#json-pointer={pointer}"
                row["source_locator"] = exact_locator
                row["legacy_source_locator"] = f"{filename}#json-pointer={pointer}"
                address = dict(row.get("evidence_address") or {})
                address.update(
                    {
                        "address_kind": "EXACT_SOURCE_LOCATOR",
                        "source_locator": exact_locator,
                        "json_pointer": pointer,
                    }
                )
                row["evidence_address"] = address
            rows.append(row)
        if collection_name in result or rows:
            result[collection_name] = rows
    unsupported: list[dict[str, Any]] = []
    for raw in result.get("unsupported_content") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        pointer = _text(row.get("json_pointer"))
        if pointer:
            row["source_locator"] = (
                f"{filename}#block=json-pointer:{pointer};legacy=#json-pointer={pointer}"
            )
            row["legacy_source_locator"] = f"{filename}#json-pointer={pointer}"
        unsupported.append(row)
    result["unsupported_content"] = unsupported
    return result


class GuardedApiArtifactDocumentAdapter(ApiArtifactDocumentAdapter):
    """Registered API-artifact authority with redaction and exact structural evidence."""

    name = "guarded-api-artifact-json-pointer-structure"
    parser_version = "4"

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        payload, _decoded, error = _decode_payload(source)
        kind = _artifact_kind(payload) if payload is not None else ""
        if payload is None or not kind:
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
        document_ir = normalize_api_json_pointer_locators(
            document_ir,
            filename=source.filename,
        )
        if kind == "openapi":
            document_ir = apply_openapi_schema_projection(
                document_ir,
                payload=dict(sanitized),
                source=safe_source,
            )
            document_ir = apply_openapi_path_parameter_schema_projection(
                document_ir,
                payload=dict(sanitized),
                source=safe_source,
            )
        document_ir = _apply_legacy_json_pointer_locator_alias(
            document_ir,
            filename=source.filename,
        )

        receipt = dict(document_ir.get("structure_receipt") or {})
        receipt.update(
            {
                "api_artifact_secret_guard": True,
                "api_artifact_secret_guard_schema": _GUARD_SCHEMA,
                "redaction_schema": API_ARTIFACT_REDACTION_SCHEMA,
                "redaction_count": int(redaction_count),
                "pre_parse_secret_redaction_applied": True,
                "sensitive_schema_defaults_and_examples_redacted": True,
                "credential_values_retained": False,
                "original_source_bytes_exposed_to_structure_adapter": False,
                "json_pointer_exact_source_addresses": True,
                "json_pointer_locator_migration_compatibility": True,
            }
        )
        document_ir["structure_receipt"] = receipt
        return document_ir


__all__ = ["GuardedApiArtifactDocumentAdapter"]
