"""Private-pilot report loader adapter for historical authorization quarantine.

The adapter is intentionally above ``ReportLoadingMixin`` in the handler MRO.  It
loads the existing report through ``super()``, detects a valid ledger containing
quarantinable historical authorization attempts, and returns an in-memory migrated
view.  It never writes the scan artifact back to disk.

A generally invalid old ledger is left untouched so the existing quality projection
can report UNVERIFIABLE.  An authorization-specific contradiction remains a hard
MainlineContractError rather than being hidden as legacy compatibility.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .discovery_mainline_contract import MainlineContractError
from .historical_authorization_artifact_migration import (
    HistoricalAuthorizationArtifactMigrationError,
    migrate_historical_authorization_scan_result,
)
from .historical_authorization_quarantine import (
    HistoricalAuthorizationQuarantineError,
    build_historical_authorization_quarantine_projection,
)
from .obligation_attempt_ledger import ObligationAttemptLedgerError


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


class HistoricalAuthorizationReportMigrationMixin:
    """Apply the derived migration only when a valid ledger needs quarantine."""

    @staticmethod
    def _historical_authorization_migrated_view(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        row = _dict(payload)
        if not row:
            return {}
        v12 = _dict(row.get("v12"))
        ledger = _dict(
            row.get("obligation_attempt_ledger")
            or v12.get("obligation_attempt_ledger")
        )
        mainline = _dict(row.get("mainline_run") or v12.get("mainline_run"))
        if not ledger or not mainline:
            return row
        old_registry = _dict(
            row.get("canonical_defect_registry")
            or v12.get("canonical_defect_registry")
        )
        try:
            preview = build_historical_authorization_quarantine_projection(
                ledger,
                superseded_registry_fingerprint=_text(
                    old_registry.get("registry_fingerprint")
                ),
            )
        except ObligationAttemptLedgerError:
            # Preserve the established stored-artifact behavior: the quality
            # projection will mark the whole authority UNVERIFIABLE.
            return row
        except HistoricalAuthorizationQuarantineError as exc:
            raise MainlineContractError(
                f"historical_authorization_quarantine_invalid:{exc}"
            ) from exc
        if int(preview.get("quarantine_count") or 0) == 0:
            return row
        try:
            return migrate_historical_authorization_scan_result(row)
        except HistoricalAuthorizationArtifactMigrationError as exc:
            raise MainlineContractError(
                f"historical_authorization_artifact_migration_invalid:{exc}"
            ) from exc

    def _load_v12_report(self, project_id: str, root: Path) -> dict[str, Any]:
        loaded = super()._load_v12_report(project_id, root)
        return self._historical_authorization_migrated_view(loaded)

    def _load_current_scan_report(
        self,
        project_id: str,
        root: Path,
    ) -> dict[str, Any]:
        loaded = super()._load_current_scan_report(project_id, root)
        return self._historical_authorization_migrated_view(loaded)


__all__ = ["HistoricalAuthorizationReportMigrationMixin"]
