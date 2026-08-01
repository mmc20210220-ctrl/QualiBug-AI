"""Project-wide historical authorization inventory is read-only and deduplicated."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from ai_test_asset_center import historical_authorization_inventory as inventory
from ai_test_asset_center.historical_authorization_inventory import (
    HistoricalAuthorizationInventoryError,
    audit_historical_authorization_artifact,
    build_historical_authorization_inventory,
    discover_historical_authorization_artifacts,
    main,
    validate_historical_authorization_inventory,
)
from ai_test_asset_center.historical_authorization_quarantine import (
    HistoricalAuthorizationQuarantineError,
)


def _payload(
    *,
    ledger_fingerprint: str = "a" * 64,
    run_id: str = "run:1",
    campaign_id: str = "campaign:1",
) -> dict:
    return {
        "mainline_run": {
            "run_id": run_id,
            "campaign_id": campaign_id,
            "target_id": "target:1",
            "environment_id": "env:1",
            "policy_version": "v2",
            "evaluation_mode": "operational",
            "contract_fingerprint": "b" * 64,
        },
        "obligation_attempt_ledger": {
            "run_id": run_id,
            "campaign_id": campaign_id,
            "ledger_fingerprint": ledger_fingerprint,
            "attempts": [],
        },
        "canonical_defect_registry": {
            "registry_fingerprint": "c" * 64,
        },
        "delivery_occurrences": [],
    }


def _write_artifact(
    root: Path,
    *,
    source_root: str,
    project_id: str,
    filename: str,
    payload: dict,
) -> Path:
    path = root / source_root / project_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _valid_authority_contracts(monkeypatch) -> None:
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


def _quarantine_projection() -> dict:
    return {
        "status": "QUARANTINED",
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


def _install_quarantine_migration(monkeypatch, *, status: str = "MIGRATED") -> None:
    projection = _quarantine_projection()
    monkeypatch.setattr(
        inventory,
        "build_historical_authorization_quarantine_projection",
        lambda ledger, superseded_registry_fingerprint="": deepcopy(projection),
    )
    monkeypatch.setattr(
        inventory,
        "validate_historical_authorization_quarantine_projection",
        lambda value: deepcopy(value),
    )
    migration = {
        "status": status,
        "rebuild_reason": (
            "FORMAL_DELIVERY_OCCURRENCES_MISSING:finding:valid"
            if status == "REBUILD_BLOCKED"
            else ""
        ),
        "rebuilt_registry_fingerprint": "" if status == "REBUILD_BLOCKED" else "d" * 64,
        "rebuilt_delivery_occurrence_count": 0,
        "source_evidence_rewritten": False,
    }
    monkeypatch.setattr(
        inventory,
        "migrate_historical_authorization_scan_result",
        lambda payload: {
            **deepcopy(payload),
            "historical_authorization_artifact_migration": deepcopy(migration),
        },
    )
    monkeypatch.setattr(
        inventory,
        "validate_historical_authorization_artifact_migration_receipt",
        lambda value: deepcopy(value),
    )


def _install_clear_projection(monkeypatch) -> None:
    monkeypatch.setattr(
        inventory,
        "build_historical_authorization_quarantine_projection",
        lambda ledger, superseded_registry_fingerprint="": {
            "quarantine_count": 0
        },
    )
    monkeypatch.setattr(
        inventory,
        "validate_historical_authorization_quarantine_projection",
        lambda value: deepcopy(value),
    )


def test_discovery_covers_outputs_and_workspace_and_project_filter(
    tmp_path: Path,
) -> None:
    first = _write_artifact(
        tmp_path,
        source_root="platform_outputs",
        project_id="alpha",
        filename="scan_result.json",
        payload=_payload(),
    )
    second = _write_artifact(
        tmp_path,
        source_root="platform_workspace",
        project_id="beta",
        filename="v12_report.json",
        payload=_payload(ledger_fingerprint="e" * 64),
    )
    (tmp_path / "platform_outputs" / "ignored.txt").write_text(
        "ignored",
        encoding="utf-8",
    )

    discovered = discover_historical_authorization_artifacts(tmp_path)

    assert set(discovered) == {"alpha", "beta"}
    assert discovered["alpha"] == [first.resolve()]
    assert discovered["beta"] == [second.resolve()]
    assert discover_historical_authorization_artifacts(
        tmp_path,
        project_ids=["beta"],
    ) == {"beta": [second.resolve()]}


def test_duplicate_artifacts_do_not_double_count_one_ledger_occurrence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_quarantine_migration(monkeypatch)
    payload = _payload()
    first = _write_artifact(
        tmp_path,
        source_root="platform_outputs",
        project_id="alpha",
        filename="scan_result.json",
        payload=payload,
    )
    second = _write_artifact(
        tmp_path,
        source_root="platform_workspace",
        project_id="alpha",
        filename="v12_report.json",
        payload=payload,
    )
    first_before = first.read_bytes()
    second_before = second.read_bytes()

    report = build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    assert first.read_bytes() == first_before
    assert second.read_bytes() == second_before
    assert report["status"] == "ACTION_REQUIRED"
    assert report["project_count"] == 1
    assert report["artifact_count"] == 2
    assert report["authority_scope_count"] == 1
    assert report["quarantine_occurrence_count"] == 1
    assert report["rerun_required_count"] == 1
    project = report["projects"][0]
    assert project["status"] == "QUARANTINED"
    assert project["artifact_count"] == 2
    assert project["authority_scope_count"] == 1
    assert project["quarantine_occurrence_count"] == 1
    assert project["quarantined_finding_ids"] == ["finding:auth"]
    assert len(project["rerun_queue"]) == 1
    assert project["registry_rebuilt_scope_count"] == 1
    assert all(
        artifact["source_evidence_rewritten"] is False
        for artifact in project["artifacts"]
    )
    assert validate_historical_authorization_inventory(report) == report


def test_rebuild_blocked_is_reported_without_rewriting_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_quarantine_migration(monkeypatch, status="REBUILD_BLOCKED")
    path = _write_artifact(
        tmp_path,
        source_root="platform_outputs",
        project_id="alpha",
        filename="scan_result.json",
        payload=_payload(),
    )
    before = path.read_bytes()

    report = build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    assert path.read_bytes() == before
    project = report["projects"][0]
    artifact = project["artifacts"][0]
    assert project["status"] == "REBUILD_BLOCKED"
    assert project["registry_rebuild_blocked_scope_count"] == 1
    assert artifact["migration_status"] == "REBUILD_BLOCKED"
    assert artifact["registry_rebuild_reason"].startswith(
        "FORMAL_DELIVERY_OCCURRENCES_MISSING"
    )
    assert artifact["rebuilt_registry_fingerprint"] == ""


def test_invalid_json_is_isolated_and_does_not_abort_other_projects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_clear_projection(monkeypatch)
    _write_artifact(
        tmp_path,
        source_root="platform_outputs",
        project_id="clear",
        filename="scan_result.json",
        payload=_payload(),
    )
    invalid = tmp_path / "platform_outputs" / "broken" / "scan_result.json"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("{not-json", encoding="utf-8")

    report = build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    assert report["status"] == "CONTRADICTION"
    projects = {value["project_id"]: value for value in report["projects"]}
    assert projects["clear"]["status"] == "CLEAR"
    assert projects["broken"]["status"] == "CONTRADICTION"
    assert projects["broken"]["invalid_artifact_count"] == 1
    assert report["artifact_status_counts"]["INVALID_ARTIFACT"] == 1


def test_artifact_change_during_snapshot_is_not_audited_as_stable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = _write_artifact(
        tmp_path,
        source_root="platform_outputs",
        project_id="moving",
        filename="scan_result.json",
        payload=_payload(),
    )
    original_read_bytes = Path.read_bytes

    def _changing_read_bytes(candidate: Path) -> bytes:
        raw = original_read_bytes(candidate)
        if candidate.resolve() == path.resolve():
            candidate.write_bytes(raw + b" ")
        return raw

    monkeypatch.setattr(Path, "read_bytes", _changing_read_bytes)

    artifact = audit_historical_authorization_artifact(path, root=tmp_path)

    assert artifact["status"] == "INVALID_ARTIFACT"
    assert artifact["reason"] == "ARTIFACT_CHANGED_DURING_READ"
    assert artifact["source_evidence_rewritten"] is False


def test_missing_authority_is_unverifiable_not_a_crash(tmp_path: Path) -> None:
    _write_artifact(
        tmp_path,
        source_root="platform_workspace",
        project_id="legacy",
        filename="intelligence_report.json",
        payload={"findings": [{"finding_id": "finding:old"}]},
    )

    report = build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    assert report["status"] == "ACTION_REQUIRED"
    project = report["projects"][0]
    assert project["status"] == "UNVERIFIABLE"
    assert project["unverifiable_artifact_count"] == 1
    assert project["artifacts"][0]["reason"] == "MAINLINE_RUN_MISSING"
    assert project["quarantine_occurrence_count"] == 0


def test_mainline_and_ledger_identity_mismatch_is_a_contradiction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_clear_projection(monkeypatch)
    payload = _payload()
    payload["obligation_attempt_ledger"]["campaign_id"] = "campaign:foreign"
    _write_artifact(
        tmp_path,
        source_root="platform_outputs",
        project_id="foreign-ledger",
        filename="scan_result.json",
        payload=payload,
    )

    report = build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    artifact = report["projects"][0]["artifacts"][0]
    assert report["status"] == "CONTRADICTION"
    assert artifact["status"] == "CONTRADICTION"
    assert artifact["reason"].startswith(
        "AUTHORITY_IDENTITY_MISMATCH:campaign_id"
    )


def test_authorization_contradiction_is_reported_per_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write_artifact(
        tmp_path,
        source_root="platform_outputs",
        project_id="alpha",
        filename="scan_result.json",
        payload=_payload(),
    )
    monkeypatch.setattr(
        inventory,
        "build_historical_authorization_quarantine_projection",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HistoricalAuthorizationQuarantineError(
                "historical_authorization_contradiction:fingerprint"
            )
        ),
    )

    report = build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    artifact = report["projects"][0]["artifacts"][0]
    assert report["status"] == "CONTRADICTION"
    assert artifact["status"] == "CONTRADICTION"
    assert "fingerprint" in artifact["reason"]
    assert artifact["source_evidence_rewritten"] is False


def test_requested_missing_project_is_explicit(tmp_path: Path) -> None:
    report = build_historical_authorization_inventory(
        tmp_path,
        project_ids=["missing-project"],
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    assert report["requested_projects"] == ["missing-project"]
    assert report["missing_projects"] == ["missing-project"]
    assert report["project_count"] == 0
    assert report["status"] == "CLEAR"


def test_resigned_project_summary_tamper_is_rejected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_quarantine_migration(monkeypatch)
    _write_artifact(
        tmp_path,
        source_root="platform_outputs",
        project_id="alpha",
        filename="scan_result.json",
        payload=_payload(),
    )
    report = build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )
    report["projects"][0]["quarantine_occurrence_count"] = 99
    report["inventory_fingerprint"] = inventory._fingerprint(
        {
            key: value
            for key, value in report.items()
            if key != "inventory_fingerprint"
        }
    )

    with pytest.raises(
        HistoricalAuthorizationInventoryError,
        match="historical_authorization_inventory_project_summary_invalid:alpha",
    ):
        validate_historical_authorization_inventory(report)


def test_inventory_fingerprint_detects_tampering(tmp_path: Path) -> None:
    report = build_historical_authorization_inventory(
        tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )
    report["artifact_count"] = 9

    with pytest.raises(
        HistoricalAuthorizationInventoryError,
        match="historical_authorization_inventory_summary_invalid:artifact_count",
    ):
        validate_historical_authorization_inventory(report)


def test_cli_writes_only_inventory_report(tmp_path: Path, capsys) -> None:
    source = _write_artifact(
        tmp_path,
        source_root="platform_workspace",
        project_id="legacy",
        filename="scan_result.json",
        payload={"findings": []},
    )
    before = source.read_bytes()
    output = tmp_path / "reports" / "authorization-inventory.json"

    exit_code = main([
        "--root",
        str(tmp_path),
        "--output",
        str(output),
        "--compact",
    ])

    assert exit_code == 0
    assert source.read_bytes() == before
    assert output.is_file()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["source_artifacts_modified"] is False
    assert json.loads(capsys.readouterr().out)["inventory_fingerprint"] == written[
        "inventory_fingerprint"
    ]
