"""Canonical public surface for connector-neutral tenant acceptance.

The implementation remains shared with the historical Feishu compatibility entrypoint so
there is one threshold, evidence, persistence, and redaction authority. This module gives
new connector integrations a vendor-neutral import path and contract types.
"""
from __future__ import annotations

from typing import Any, TypedDict

from .feishu_tenant_acceptance import (
    ACCEPTANCE_PROFILES,
    CONNECTOR_TENANT_ACCEPTANCE_SCHEMA,
    FeishuTenantAcceptanceError,
    connector_main,
    run_connector_tenant_acceptance,
)


class ConnectorAcceptanceProfile(TypedDict):
    """Bounded, non-secret thresholds used by one generic acceptance profile."""

    runs: int
    min_discovered_resources: int
    min_coverage_ratio: float
    max_unsupported_ratio: float
    max_run_duration_seconds: float


class ConnectorAcceptanceReport(TypedDict, total=False):
    """Publicly persisted acceptance report envelope.

    Raw source content, credentials, cursors, and receipt paths are intentionally absent from
    this type; the runtime projector stores only fingerprints for opaque identities.
    """

    schema: str
    acceptance_id: str
    project_id: str
    connector_instance_id: str
    profile: str
    verdict: str
    acceptance_ready: bool
    started_at_utc: str
    completed_at_utc: str
    thresholds: ConnectorAcceptanceProfile
    runs: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    summary: dict[str, Any]
    execution_error: dict[str, str] | None
    governance: dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    """Run the generic connector acceptance CLI without duplicating the parser."""
    return connector_main(argv)


__all__ = [
    "ACCEPTANCE_PROFILES",
    "CONNECTOR_TENANT_ACCEPTANCE_SCHEMA",
    "ConnectorAcceptanceProfile",
    "ConnectorAcceptanceReport",
    "FeishuTenantAcceptanceError",
    "main",
    "run_connector_tenant_acceptance",
]
