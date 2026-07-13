"""Shared constants and utility functions — re-exported from grounded_probe_executor.
Import from here instead of grounded_probe_executor for utility-only dependencies.
"""
from __future__ import annotations

# Re-export constants and utilities from the canonical source module.
# This avoids circular imports while allowing future extraction.
from .grounded_probe_executor import (
    UNRESOLVED_PLACEHOLDER_RE,
    SENSITIVE_FIELD_RE,
    BUSINESS_FIELD_RE,
    NEGATIVE_NUMBER_KEY_RE,
    WRITE_METHODS,
    READ_METHODS,
    EXPECTED_AUTH_FAILURES,
    DEFAULT_NEGATIVE_WRITE_FAILURES,
    AUTH_BOUNDARY_RISKS,
    FIXTURE_BACKED_READ_RISKS,
    AUTH_HEADER_NAMES,
    SANDBOX_CLEANUP_STRATEGIES,
    PRODUCTION_HOST_RE,
    NON_PROD_HINT_RE,
    ProbeDecision,
    _now,
    _read_json,
    _write_json,
    _redact,
    _has_unresolved_placeholder,
    _safe_payload_summary,
    _json_clone,
    _dedupe,
    _safe_rate,
)

__all__ = [
    "UNRESOLVED_PLACEHOLDER_RE",
    "SENSITIVE_FIELD_RE",
    "BUSINESS_FIELD_RE",
    "NEGATIVE_NUMBER_KEY_RE",
    "WRITE_METHODS",
    "READ_METHODS",
    "EXPECTED_AUTH_FAILURES",
    "DEFAULT_NEGATIVE_WRITE_FAILURES",
    "AUTH_BOUNDARY_RISKS",
    "FIXTURE_BACKED_READ_RISKS",
    "AUTH_HEADER_NAMES",
    "SANDBOX_CLEANUP_STRATEGIES",
    "PRODUCTION_HOST_RE",
    "NON_PROD_HINT_RE",
    "ProbeDecision",
    "_now",
    "_read_json",
    "_write_json",
    "_redact",
    "_has_unresolved_placeholder",
    "_safe_payload_summary",
    "_json_clone",
    "_dedupe",
    "_safe_rate",
]
