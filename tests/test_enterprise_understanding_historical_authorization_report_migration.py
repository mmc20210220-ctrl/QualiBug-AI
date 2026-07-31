"""Private-pilot loaders apply historical authorization migration in memory."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from ai_test_asset_center.discovery_mainline_contract import MainlineContractError
from ai_test_asset_center.historical_authorization_quarantine import (
    HistoricalAuthorizationQuarantineError,
)
from ai_test_asset_center.obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
)
from ai_test_asset_center import private_pilot_historical_authorization_migration as adapter
from ai_test_asset_center.private_pilot_historical_authorization_migration import (
    HistoricalAuthorizationReportMigrationMixin,
)


def _payload() -> dict:
    return {
        "mainline_run": {"run_id": "run:1"},
        "obligation_attempt_ledger": {"run_id": "run:1"},
        "canonical_defect_registry": {"registry_fingerprint": "a" * 64},
    }


class _BaseLoader:
    def __init__(self, value: dict) -> None:
        self.value = value

    def _load_v12_report(self, project_id: str, root: Path) -> dict:
        return self.value

    def _load_current_scan_report(self, project_id: str, root: Path) -> dict:
        return self.value


class _MigratingLoader(HistoricalAuthorizationReportMigrationMixin, _BaseLoader):
    pass


class _BaseCommandCenter(_BaseLoader):
    def _build_command_center(self, project_id: str, root: Path) -> dict:
        return {
            "ok": True,
            "data": {
                "defects": [{"finding_id": "finding:valid"}],
                "risks": [{"finding_id": "finding:valid"}],
                "scan_meta": {},
                "test_task_board": {},
            },
        }


class _MigratingCommandCenter(
    HistoricalAuthorizationReportMigrationMixin,
    _BaseCommandCenter,
):
    pass


def test_clear_report_is_returned_without_migration(monkeypatch, tmp_path: Path) -> None:
    source = _payload()
    snapshot = deepcopy(source)
    calls: list[str] = []
    monkeypatch.setattr(
        adapter,
        "build_historical_authorization_quarantine_projection",
        lambda ledger, superseded_registry_fingerprint="": {
            "quarantine_count": 0
        },
    )
    monkeypatch.setattr(
        adapter,
        "migrate_historical_authorization_scan_result",
        lambda value: calls.append("migrated") or {"unexpected": True},
    )

    output = _MigratingLoader(source)._load_v12_report("demo", tmp_path)

    assert output == snapshot
    assert calls == []


def test_quarantined_report_returns_in_memory_migrated_view(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _payload()
    snapshot = deepcopy(source)
    migrated = {
        **deepcopy(source),
        "historical_authorization_quarantine": {"status": "QUARANTINED"},
    }
    monkeypatch.setattr(
        adapter,
        "build_historical_authorization_quarantine_projection",
        lambda ledger, superseded_registry_fingerprint="": {
            "quarantine_count": 1
        },
    )
    monkeypatch.setattr(
        adapter,
        "migrate_historical_authorization_scan_result",
        lambda value: deepcopy(migrated),
    )

    loader = _MigratingLoader(source)
    assert loader._load_v12_report("demo", tmp_path) == migrated
    assert loader._load_current_scan_report("demo", tmp_path) == migrated
    assert source == snapshot


def test_generally_invalid_ledger_keeps_existing_unverifiable_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _payload()
    monkeypatch.setattr(
        adapter,
        "build_historical_authorization_quarantine_projection",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ObligationAttemptLedgerError("obligation_attempt_gate_bundle_invalid")
        ),
    )

    assert _MigratingLoader(source)._load_v12_report("demo", tmp_path) == source


def test_authorization_contradiction_remains_a_hard_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _payload()
    monkeypatch.setattr(
        adapter,
        "build_historical_authorization_quarantine_projection",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HistoricalAuthorizationQuarantineError(
                "historical_authorization_contradiction:fingerprint"
            )
        ),
    )

    with pytest.raises(
        MainlineContractError,
        match="historical_authorization_quarantine_invalid",
    ):
        _MigratingLoader(source)._load_current_scan_report("demo", tmp_path)


def test_command_center_exposes_internal_rerun_queue_without_touching_defects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    quarantine = {
        "status": "QUARANTINED",
        "quarantine_count": 1,
        "rerun_required_count": 1,
        "manual_recompile_required_count": 0,
        "rerun_queue": [
            {
                "finding_id": "finding:auth",
                "action": "RERUN_REQUIRED",
            }
        ],
    }
    source = {
        **_payload(),
        "historical_authorization_quarantine": quarantine,
        "historical_authorization_artifact_migration": {
            "status": "MIGRATED",
            "rebuild_reason": "",
        },
    }
    monkeypatch.setattr(
        adapter,
        "build_historical_authorization_quarantine_projection",
        lambda *args, **kwargs: {"quarantine_count": 0},
    )

    response = _MigratingCommandCenter(source)._build_command_center(
        "demo",
        tmp_path,
    )
    data = response["data"]

    assert data["defects"] == [{"finding_id": "finding:valid"}]
    assert data["risks"] == [{"finding_id": "finding:valid"}]
    assert data["historical_authorization_quarantine"] == quarantine
    assert data["historical_authorization_rerun_queue"] == quarantine[
        "rerun_queue"
    ]
    assert data["historical_authorization_quarantine_summary"] == {
        "status": "QUARANTINED",
        "migration_status": "MIGRATED",
        "registry_rebuild_reason": "",
        "canonical_registry_rebuilt": True,
        "quarantine_count": 1,
        "rerun_required_count": 1,
        "manual_recompile_required_count": 0,
        "quarantined_authorization_publication_allowed": False,
        "other_formal_defect_publication_preserved": True,
        "scope": "internal_historical_authorization_remediation",
    }


def test_private_pilot_handler_mro_applies_migration_before_report_loader() -> None:
    from ai_test_asset_center.private_pilot_report_loading import ReportLoadingMixin
    from ai_test_asset_center.private_pilot_service import PrivatePilotHandler

    mro = list(PrivatePilotHandler.__mro__)
    assert mro.index(HistoricalAuthorizationReportMigrationMixin) + 1 == mro.index(
        ReportLoadingMixin
    )
