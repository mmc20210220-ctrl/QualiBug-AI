"""Private-pilot adapters for historical authorization quarantine.

The adapter is intentionally above ``ReportLoadingMixin`` and
``CommandCenterBuilderMixin`` in the handler MRO. It loads the existing report
through ``super()``, detects a valid ledger containing quarantinable historical
authorization attempts, and returns an in-memory migrated view. It never writes the
scan artifact back to disk.

A generally invalid old ledger is left untouched so the existing quality projection
can report UNVERIFIABLE. An authorization-specific contradiction remains a hard
MainlineContractError rather than being hidden as legacy compatibility. The command
center receives the quarantine receipts and rerun queue as internal diagnostics only;
these rows are never copied into defects, risks, or formal customer counts.
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
    """Apply derived migration and expose its internal remediation projection."""

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

    @staticmethod
    def _historical_authorization_projection(
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        row = _dict(payload)
        nested = _dict(row.get("v12"))
        quarantine = _dict(
            row.get("historical_authorization_quarantine")
            or nested.get("historical_authorization_quarantine")
        )
        migration = _dict(
            row.get("historical_authorization_artifact_migration")
            or nested.get("historical_authorization_artifact_migration")
        )
        return quarantine, migration

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

    def _build_command_center(self, project_id: str, root: Path) -> dict[str, Any]:
        response = super()._build_command_center(project_id, root)
        row = _dict(response)
        data = _dict(row.get("data"))
        if not data:
            return response
        report = self._load_current_scan_report(project_id, root)
        if not report:
            report = self._load_v12_report(project_id, root)
        quarantine, migration = self._historical_authorization_projection(report)
        if not quarantine or int(quarantine.get("quarantine_count") or 0) <= 0:
            return response

        projected_data = dict(data)
        projected_data["historical_authorization_quarantine"] = quarantine
        projected_data["historical_authorization_rerun_queue"] = list(
            quarantine.get("rerun_queue") or []
        )
        if migration:
            projected_data[
                "historical_authorization_artifact_migration"
            ] = migration
        migration_status = _text(migration.get("status"))
        rebuild_reason = _text(migration.get("rebuild_reason"))
        diagnostics = {
            "status": _text(quarantine.get("status")),
            "migration_status": migration_status or "UNKNOWN",
            "registry_rebuild_reason": rebuild_reason,
            "canonical_registry_rebuilt": migration_status == "MIGRATED",
            "quarantine_count": int(
                quarantine.get("quarantine_count") or 0
            ),
            "rerun_required_count": int(
                quarantine.get("rerun_required_count") or 0
            ),
            "manual_recompile_required_count": int(
                quarantine.get("manual_recompile_required_count") or 0
            ),
            "customer_defect_publication_allowed": False,
            "scope": "internal_historical_authorization_remediation",
        }
        projected_data[
            "historical_authorization_quarantine_summary"
        ] = diagnostics
        scan_meta = _dict(projected_data.get("scan_meta"))
        if scan_meta:
            projected_scan_meta = dict(scan_meta)
            projected_scan_meta[
                "historical_authorization_quarantine"
            ] = diagnostics
            projected_data["scan_meta"] = projected_scan_meta
        task_board = _dict(projected_data.get("test_task_board"))
        if task_board:
            projected_task_board = dict(task_board)
            projected_task_board[
                "historical_authorization_remediation"
            ] = diagnostics
            projected_data["test_task_board"] = projected_task_board
        return {**row, "data": projected_data}


__all__ = ["HistoricalAuthorizationReportMigrationMixin"]
