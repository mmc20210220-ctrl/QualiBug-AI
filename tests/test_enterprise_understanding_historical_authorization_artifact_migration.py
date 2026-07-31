"""Stored authorization artifacts migrate through a derived, non-destructive view."""
from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center import canonical_defect_registry as canonical_registry
from ai_test_asset_center import discovery_mainline_contract as mainline_contract
from ai_test_asset_center import historical_authorization_artifact_migration as migration
from ai_test_asset_center.historical_authorization_artifact_migration import (
    HistoricalAuthorizationArtifactMigrationError,
    migrate_historical_authorization_scan_result,
    validate_historical_authorization_artifact_migration_receipt,
)


def _quarantine_projection() -> dict:
    return {
        "schema_version": "qualibug.historical-authorization-quarantine-projection.v1",
        "status": "QUARANTINED",
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "attempt_ledger_fingerprint": "a" * 64,
        "superseded_registry_fingerprint": "b" * 64,
        "quarantine_count": 1,
        "quarantined_finding_ids": ["finding:auth"],
        "rerun_required_count": 1,
        "manual_recompile_required_count": 0,
        "quarantine_receipts": [],
        "rerun_queue": [],
        "projection_fingerprint": "c" * 64,
    }


def _scan_result() -> dict:
    ledger = {
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "ledger_fingerprint": "a" * 64,
        "attempts": [],
    }
    mainline = {
        "run_id": "run:1",
        "campaign_id": "campaign:1",
        "target_id": "target:1",
        "environment_id": "env:1",
        "contract_fingerprint": "d" * 64,
    }
    old_registry = {
        "registry_fingerprint": "b" * 64,
        "delivery_occurrence_finding_ids": ["finding:auth", "finding:valid"],
    }
    return {
        "mainline_run": mainline,
        "obligation_attempt_ledger": ledger,
        "delivery_occurrences": [
            {"finding_id": "finding:auth"},
            {"finding_id": "finding:valid"},
        ],
        "canonical_defect_registry": old_registry,
        "v12": {
            "mainline_run": deepcopy(mainline),
            "obligation_attempt_ledger": deepcopy(ledger),
            "delivery_occurrences": [
                {"finding_id": "finding:auth"},
                {"finding_id": "finding:valid"},
            ],
            "canonical_defect_registry": deepcopy(old_registry),
        },
    }


def test_migration_rebuilds_only_derived_registry_and_preserves_source(
    monkeypatch,
) -> None:
    source = _scan_result()
    snapshot = deepcopy(source)
    quarantine = _quarantine_projection()
    rebuilt = {
        "registry_fingerprint": "e" * 64,
        "delivery_occurrence_count": 1,
        "delivery_occurrence_finding_ids": ["finding:valid"],
    }

    monkeypatch.setattr(
        migration,
        "build_historical_authorization_quarantine_projection",
        lambda ledger, superseded_registry_fingerprint="": deepcopy(quarantine),
    )
    monkeypatch.setattr(
        migration,
        "validate_historical_authorization_quarantine_projection",
        lambda value: deepcopy(value),
    )
    monkeypatch.setattr(
        mainline_contract,
        "validate_mainline_run_contract",
        lambda value: deepcopy(value),
    )
    monkeypatch.setattr(
        canonical_registry,
        "build_canonical_defect_registry",
        lambda **kwargs: deepcopy(rebuilt),
    )

    output = migrate_historical_authorization_scan_result(source)

    assert source == snapshot
    assert output["obligation_attempt_ledger"] == snapshot[
        "obligation_attempt_ledger"
    ]
    assert output["canonical_defect_registry"] == rebuilt
    assert output["v12"]["canonical_defect_registry"] == rebuilt
    assert output["historical_authorization_quarantine"] == quarantine
    receipt = output["historical_authorization_artifact_migration"]
    assert receipt["status"] == "MIGRATED"
    assert receipt["superseded_registry_fingerprint"] == "b" * 64
    assert receipt["rebuilt_registry_fingerprint"] == "e" * 64
    assert receipt["quarantined_finding_ids"] == ["finding:auth"]
    assert receipt["source_evidence_rewritten"] is False
    assert validate_historical_authorization_artifact_migration_receipt(
        receipt
    ) == receipt


def test_migration_requires_mainline_and_ledger() -> None:
    with pytest.raises(
        HistoricalAuthorizationArtifactMigrationError,
        match="historical_authorization_attempt_ledger_missing",
    ):
        migrate_historical_authorization_scan_result({"mainline_run": {"id": "run"}})

    with pytest.raises(
        HistoricalAuthorizationArtifactMigrationError,
        match="historical_authorization_mainline_run_missing",
    ):
        migrate_historical_authorization_scan_result(
            {"obligation_attempt_ledger": {"run_id": "run:1"}}
        )


def test_migration_receipt_tamper_is_rejected(monkeypatch) -> None:
    source = _scan_result()
    quarantine = _quarantine_projection()
    rebuilt = {
        "registry_fingerprint": "e" * 64,
        "delivery_occurrence_count": 1,
    }
    monkeypatch.setattr(
        migration,
        "build_historical_authorization_quarantine_projection",
        lambda ledger, superseded_registry_fingerprint="": deepcopy(quarantine),
    )
    monkeypatch.setattr(
        migration,
        "validate_historical_authorization_quarantine_projection",
        lambda value: deepcopy(value),
    )
    monkeypatch.setattr(
        mainline_contract,
        "validate_mainline_run_contract",
        lambda value: deepcopy(value),
    )
    monkeypatch.setattr(
        canonical_registry,
        "build_canonical_defect_registry",
        lambda **kwargs: deepcopy(rebuilt),
    )

    receipt = migrate_historical_authorization_scan_result(source)[
        "historical_authorization_artifact_migration"
    ]
    receipt["rebuilt_delivery_occurrence_count"] = 99

    with pytest.raises(
        HistoricalAuthorizationArtifactMigrationError,
        match="historical_authorization_migration_receipt_fingerprint_invalid",
    ):
        validate_historical_authorization_artifact_migration_receipt(receipt)
