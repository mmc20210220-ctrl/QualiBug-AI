"""Evaluator-owned authentication for architecture import-trace receipts."""
from __future__ import annotations

from typing import Any

from ai_test_asset_center.evaluator_receipt_auth import (
    seal_evaluator_artifact,
    verify_evaluator_artifact,
)


IMPORT_TRACE_SCHEMA = "qualibug.python-import-trace.v1"
IMPORT_TRACE_AUTH_DOMAIN = "qualibug.python-import-trace.authentication.v1"
IMPORT_TRACE_FINGERPRINT_FIELD = "trace_fingerprint"
IMPORT_TRACE_AUTHENTICATION_FIELD = "trace_authentication"


class ArchitectureImportTraceError(ValueError):
    """An evaluator trace is malformed before authentication."""


def seal_architecture_import_trace(
    payload: dict[str, Any],
    *,
    signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Seal one externally observed trace without changing its evidence."""
    if not isinstance(payload, dict):
        raise ArchitectureImportTraceError("import_trace_not_object")
    if payload.get("schema_version") != IMPORT_TRACE_SCHEMA:
        raise ArchitectureImportTraceError("import_trace_schema_invalid")
    return seal_evaluator_artifact(
        payload,
        signing_key=signing_key,
        domain=IMPORT_TRACE_AUTH_DOMAIN,
        fingerprint_field=IMPORT_TRACE_FINGERPRINT_FIELD,
        authentication_field=IMPORT_TRACE_AUTHENTICATION_FIELD,
    )


def verify_architecture_import_trace(
    payload: dict[str, Any],
    *,
    signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Verify evaluator identity and content integrity fail closed."""
    return verify_evaluator_artifact(
        payload,
        signing_key=signing_key,
        domain=IMPORT_TRACE_AUTH_DOMAIN,
        fingerprint_field=IMPORT_TRACE_FINGERPRINT_FIELD,
        authentication_field=IMPORT_TRACE_AUTHENTICATION_FIELD,
    )
