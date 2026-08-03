from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_test_asset_center.connector_lifecycle_commit_authority as authority
from ai_test_asset_center.connector_lifecycle_commit_authority import (
    ConnectorLifecycleCommitError,
    commit_connector_lifecycle_transaction,
    recover_connector_lifecycle_transactions,
)
from ai_test_asset_center.connector_sync_authority import (
    _registry_path as connector_registry_path,
    _run_path as connector_run_path,
)
from ai_test_asset_center.enterprise_knowledge_center._utils import (
    _paths as knowledge_paths,
    _write_json,
)
from ai_test_asset_center.enterprise_source_registry import (
    _paths as runtime_source_paths,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
EPOCH = "sync-atomic-one"
ACTOR = {"name": "tester", "role": "knowledge_admin"}


def _targets(root: Path) -> dict[str, Path]:
    runtime = runtime_source_paths(root, PROJECT)
    return {
        "knowledge_registry": knowledge_paths(PROJECT, root)["registry"],
        "runtime_source_registry": runtime["registry"],
        "runtime_source_audit": runtime["audit"],
        "sync_run_receipt": connector_run_path(PROJECT, CONNECTOR, EPOCH, root),
        "connector_registry": connector_registry_path(PROJECT, root),
    }


def _seed(root: Path) -> dict[str, bytes]:
    targets = _targets(root)
    _write_json(
        targets["knowledge_registry"],
        {
            "phase": "phase58_enterprise_knowledge_unified_ingestion",
            "project_id": PROJECT,
            "sources": [],
            "source_occurrences": [],
            "audit_events": [],
            "governance": {},
            "marker": "knowledge-before",
        },
    )
    _write_json(
        targets["runtime_source_registry"],
        {
            "schema_version": "enterprise-source-registry-v1",
            "project_id": PROJECT,
            "assets": {},
            "marker": "runtime-before",
        },
    )
    targets["runtime_source_audit"].parent.mkdir(parents=True, exist_ok=True)
    targets["runtime_source_audit"].write_text("audit-before\n", encoding="utf-8")
    _write_json(
        targets["sync_run_receipt"],
        {
            "schema": "qualibug.connector-sync-run.v1",
            "sync_epoch_id": EPOCH,
            "project_id": PROJECT,
            "connector_instance_id": CONNECTOR,
            "connector_type": "feishu",
            "sync_mode": "FULL",
            "status": "COMPLETE",
            "started_at_utc": "2026-08-01T10:00:00Z",
            "completed_at_utc": "2026-08-01T10:01:00Z",
            "item_count": 1,
            "success_count": 1,
            "failure_count": 0,
        },
    )
    _write_json(
        targets["connector_registry"],
        {
            "schema": "qualibug.connector-sync-registry.v1",
            "project_id": PROJECT,
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "feishu",
                    "status": "ACTIVE",
                }
            ],
            "sync_runs": [
                {
                    "sync_epoch_id": EPOCH,
                    "connector_instance_id": CONNECTOR,
                    "status": "COMPLETE",
                }
            ],
            "audit_events": [],
            "governance": {},
        },
    )
    return {name: path.read_bytes() for name, path in targets.items()}


def _mutate_all(root: Path, marker: str) -> None:
    targets = _targets(root)
    for name in (
        "knowledge_registry",
        "runtime_source_registry",
        "sync_run_receipt",
        "connector_registry",
    ):
        _write_json(targets[name], {"marker": marker, "authority": name})
    targets["runtime_source_audit"].write_text(
        f"audit-{marker}\n",
        encoding="utf-8",
    )


def _lifecycle(*, persisted: bool) -> dict:
    return {
        "schema": "qualibug.connector-remote-lifecycle.v1",
        "status": "COMPLETE" if persisted else "PARTIAL_RECEIPT_NOT_PERSISTED",
        "sync_receipt_persisted": persisted,
        "evidence_persistence_status": "COMPLETE" if persisted else "FAILED",
        "remote_deletion_inferred": False,
        "permission_loss_inferred": False,
        "historical_source_bytes_retained": True,
        "customer_material_mutation_executed": False,
    }


def _transaction_receipts(root: Path) -> list[Path]:
    directory = (
        knowledge_paths(PROJECT, root)["workspace"]
        / "connector_lifecycle_transactions"
        / CONNECTOR
        / "receipts"
    )
    return sorted(directory.glob("*.json")) if directory.exists() else []


def _pending_transactions(root: Path) -> list[Path]:
    directory = (
        knowledge_paths(PROJECT, root)["workspace"]
        / "connector_lifecycle_transactions"
        / CONNECTOR
        / "pending"
    )
    return sorted(path for path in directory.glob("*") if path.is_dir()) if directory.exists() else []


def test_success_commits_one_transaction_id_to_run_and_summary(tmp_path: Path) -> None:
    _seed(tmp_path)

    def apply() -> dict:
        knowledge = json.loads(
            _targets(tmp_path)["knowledge_registry"].read_text(encoding="utf-8")
        )
        knowledge["marker"] = "knowledge-after"
        _write_json(_targets(tmp_path)["knowledge_registry"], knowledge)
        return _lifecycle(persisted=True)

    result = commit_connector_lifecycle_transaction(
        PROJECT,
        connector_instance_id=CONNECTOR,
        sync_epoch_id=EPOCH,
        apply_lifecycle=apply,
        root=tmp_path,
        actor=ACTOR,
    )

    transaction_id = result["lifecycle_commit_transaction_id"]
    assert result["lifecycle_commit_status"] == "COMMITTED"
    assert result["cross_authority_atomic_commit"] is True
    run = json.loads(_targets(tmp_path)["sync_run_receipt"].read_text(encoding="utf-8"))
    registry = json.loads(
        _targets(tmp_path)["connector_registry"].read_text(encoding="utf-8")
    )
    summary = next(row for row in registry["sync_runs"] if row["sync_epoch_id"] == EPOCH)
    assert run["remote_lifecycle_commit"] == {
        "schema": authority.CONNECTOR_LIFECYCLE_COMMIT_SCHEMA,
        "transaction_id": transaction_id,
        "status": "COMMITTED",
        "cross_authority_atomic_commit": True,
        "customer_material_mutation_executed": False,
    }
    assert summary["remote_lifecycle_commit_transaction_id"] == transaction_id
    assert summary["remote_lifecycle_commit_status"] == "COMMITTED"
    assert summary["remote_lifecycle_evidence_persisted"] is True
    receipts = _transaction_receipts(tmp_path)
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "COMMITTED"
    assert receipt["source_content_backed_up"] is False
    assert receipt["customer_material_mutation_executed"] is False
    assert _pending_transactions(tmp_path) == []


def test_precommit_receipt_failure_restores_all_existing_authority_files(
    tmp_path: Path,
) -> None:
    before = _seed(tmp_path)

    def apply() -> dict:
        _mutate_all(tmp_path, "mutated-before-failure")
        return _lifecycle(persisted=False)

    with pytest.raises(
        ConnectorLifecycleCommitError,
        match="lifecycle_commit_rolled_back",
    ):
        commit_connector_lifecycle_transaction(
            PROJECT,
            connector_instance_id=CONNECTOR,
            sync_epoch_id=EPOCH,
            apply_lifecycle=apply,
            root=tmp_path,
            actor=ACTOR,
        )

    for name, path in _targets(tmp_path).items():
        assert path.read_bytes() == before[name]
    receipts = _transaction_receipts(tmp_path)
    assert len(receipts) == 1
    receipt_text = receipts[0].read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)
    assert receipt["status"] == "ROLLED_BACK"
    assert receipt["rollback_verified"] is True
    assert "remote_resource_id" not in receipt_text
    assert "source_ref" not in receipt_text
    assert "customer document body" not in receipt_text
    assert _pending_transactions(tmp_path) == []


def test_recovery_rolls_back_interrupted_apply_without_source_bytes(tmp_path: Path) -> None:
    before = _seed(tmp_path)
    transaction_dir, journal = authority._begin_transaction(
        PROJECT,
        CONNECTOR,
        EPOCH,
        tmp_path.resolve(),
        ACTOR,
    )
    journal["phase"] = "APPLYING"
    journal["apply_started"] = True
    authority._write_journal(transaction_dir, journal)
    _mutate_all(tmp_path, "crashed-apply")

    result = recover_connector_lifecycle_transactions(
        PROJECT,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
    )

    assert result["status"] == "RECOVERED"
    assert result["recovered_transaction_count"] == 1
    for name, path in _targets(tmp_path).items():
        assert path.read_bytes() == before[name]
    receipt = json.loads(_transaction_receipts(tmp_path)[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "ROLLED_BACK"
    assert receipt["rollback_reason_code"] == "RECOVER_INCOMPLETE_APPLY"
    assert receipt["source_content_backed_up"] is False
    assert _pending_transactions(tmp_path) == []


def test_feishu_core_delegates_lifecycle_to_mainline_authority() -> None:
    """The retired facade wired an atomic lifecycle authority into Feishu sync. The
    surviving core module reconciles remote lifecycle through the mainline connector
    lifecycle authority, and the atomic authority stays exported as a formal API."""
    import ai_test_asset_center.connector_remote_lifecycle as mainline_lifecycle
    import ai_test_asset_center.feishu_connector_capability_sync_core as core

    assert core.reconcile_connector_remote_lifecycle is (
        mainline_lifecycle.reconcile_connector_remote_lifecycle
    )
    assert authority.reconcile_connector_remote_lifecycle_atomic is not None
