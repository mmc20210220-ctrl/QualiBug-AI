"""A present but invalid migration receipt is a contradiction, not compatibility."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from ai_test_asset_center import historical_authorization_inventory as inventory
from ai_test_asset_center.historical_authorization_artifact_migration import (
    HistoricalAuthorizationArtifactMigrationError,
)


def test_tampered_migration_receipt_is_reported_as_contradiction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "platform_outputs" / "alpha" / "scan_result.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "mainline_run": {
                    "run_id": "run:1",
                    "campaign_id": "campaign:1",
                    "contract_fingerprint": "a" * 64,
                },
                "obligation_attempt_ledger": {
                    "run_id": "run:1",
                    "campaign_id": "campaign:1",
                    "ledger_fingerprint": "b" * 64,
                    "attempts": [],
                },
                "canonical_defect_registry": {
                    "registry_fingerprint": "c" * 64,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        inventory,
        "validate_mainline_run_contract",
        lambda value: deepcopy(value),
    )
    monkeypatch.setattr(
        inventory,
        "validate_obligation_attempt_ledger",
        lambda value: deepcopy(value),
    )
    quarantine = {
        "quarantine_count": 1,
        "quarantined_finding_ids": ["finding:auth"],
        "rerun_required_count": 1,
        "manual_recompile_required_count": 0,
        "rerun_queue": [
            {
                "finding_id": "finding:auth",
                "obligation_id": "obl:auth",
                "experiment_id": "exp:auth",
                "action": "RERUN_REQUIRED",
                "requirements": ["customer_delivery_gate_v2"],
                "quarantine_receipt_id": "auth_quarantine:1",
            }
        ],
    }
    monkeypatch.setattr(
        inventory,
        "build_historical_authorization_quarantine_projection",
        lambda *args, **kwargs: deepcopy(quarantine),
    )
    monkeypatch.setattr(
        inventory,
        "validate_historical_authorization_quarantine_projection",
        lambda value: deepcopy(value),
    )
    monkeypatch.setattr(
        inventory,
        "migrate_historical_authorization_scan_result",
        lambda payload: {
            **deepcopy(payload),
            "historical_authorization_artifact_migration": {
                "status": "MIGRATED",
                "receipt_fingerprint": "tampered",
            },
        },
    )
    monkeypatch.setattr(
        inventory,
        "validate_historical_authorization_artifact_migration_receipt",
        lambda value: (_ for _ in ()).throw(
            HistoricalAuthorizationArtifactMigrationError(
                "historical_authorization_migration_receipt_fingerprint_invalid"
            )
        ),
    )

    report = inventory.build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    project = report["projects"][0]
    audited = project["artifacts"][0]
    assert report["status"] == "CONTRADICTION"
    assert project["status"] == "CONTRADICTION"
    assert audited["status"] == "CONTRADICTION"
    assert audited["migration_status"] == "FAILED"
    assert audited["reason"] == (
        "historical_authorization_migration_receipt_fingerprint_invalid"
    )
    assert audited["source_evidence_rewritten"] is False
