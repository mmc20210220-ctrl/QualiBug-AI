from __future__ import annotations

from pathlib import Path

import pytest

import ai_test_asset_center.enterprise_knowledge_center.source_occurrence_core as core
import ai_test_asset_center.enterprise_knowledge_center.source_occurrence_lifecycle as lifecycle

PROJECT = "enterprise-project"
ACTOR = {"name": "tester", "role": "knowledge_admin"}
SOURCE_REF = "connector://feishu-prod/feishu-wiki-docx/wiki%3Aspace%3Anode"


def _registry(status: str = "active") -> tuple[dict, dict, dict]:
    canonical = {
        "source_id": "canonical-source",
        "status": "active",
        "content_hash": "a" * 64,
        "source_type": "other_document",
        "original_name": "document.docx",
        "source_occurrence_ids": ["occurrence-source"],
        "source_refs": [SOURCE_REF],
    }
    occurrence = {
        "source_occurrence_id": "occurrence-source",
        "source_ref": SOURCE_REF,
        "canonical_source_id": canonical["source_id"],
        "content_asset_id": "content:sha256:" + canonical["content_hash"],
        "interpretation_asset_id": "interpretation-source",
        "content_hash": canonical["content_hash"],
        "source_type": "other_document",
        "format_identity": "docx",
        "filename": "document.docx",
        "version": 1,
        "status": status,
        "source_metadata": {
            "connector_instance_id": "feishu-prod",
            "remote_resource_id": "wiki:space:node",
            "remote_lifecycle_state": "PRESENT",
        },
    }
    registry = {
        "sources": [canonical],
        "source_occurrences": [occurrence],
        "content_assets": [
            {
                "content_asset_id": occurrence["content_asset_id"],
                "content_hash": canonical["content_hash"],
                "canonical_source_ids": [canonical["source_id"]],
                "source_occurrence_ids": [occurrence["source_occurrence_id"]],
                "status": "ACTIVE",
            }
        ],
        "interpretation_assets": [
            {
                "interpretation_asset_id": occurrence["interpretation_asset_id"],
                "canonical_source_id": canonical["source_id"],
                "content_asset_id": occurrence["content_asset_id"],
                "content_hash": canonical["content_hash"],
                "source_type": "other_document",
                "format_identity": "docx",
                "source_occurrence_ids": [occurrence["source_occurrence_id"]],
                "source_refs": [SOURCE_REF],
                "status": "ACTIVE",
            }
        ],
        "audit_events": [],
        "governance": {},
    }
    return registry, canonical, occurrence


def test_remote_scope_retirement_is_not_user_delete_or_byte_purge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry, canonical, occurrence = _registry()
    saves = []
    monkeypatch.setattr(lifecycle, "_load_registry", lambda *args, **kwargs: registry)
    monkeypatch.setattr(
        lifecycle,
        "_save_registry",
        lambda *args, **kwargs: saves.append(args[-1] if args else registry),
    )
    monkeypatch.setattr(
        lifecycle._occurrence_core,
        "deactivate_unreferenced_canonical_sources",
        lambda *args, **kwargs: {
            "status": "PASS",
            "deactivated_canonical_source_ids": [canonical["source_id"]],
            "historical_source_bytes_retained": True,
            "errors": [],
        },
    )

    result = lifecycle.delete_enterprise_knowledge_source(
        PROJECT,
        SOURCE_REF,
        root=tmp_path,
        actor=ACTOR,
        purge_bytes=False,
        retirement_reason=(
            "absent_from_configured_scope_after_consecutive_complete_snapshots"
        ),
        retirement_evidence={
            "sync_epoch_id": "sync-two",
            "complete_snapshot_count": 2,
            "absence_is_remote_deletion_proof": False,
            "customer_source_modified": False,
        },
    )

    assert result["retired_remote_scope"] is True
    assert result["lifecycle_status"] == "retired_remote_scope"
    assert result["purge_bytes_executed"] is False
    assert result["historical_source_bytes_retained"] is True
    assert result["customer_source_modified"] is False
    assert occurrence["status"] == "retired_remote_scope"
    assert occurrence["retirement_evidence"][
        "absence_is_remote_deletion_proof"
    ] is False
    assert not occurrence.get("deleted_at_utc")
    event = registry["audit_events"][-1]
    assert event["event"] == "retire_remote_scope_source_occurrence"
    assert event["customer_source_modified"] is False
    assert saves


def test_remote_scope_retirement_rejects_physical_purge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry, _, _ = _registry()
    monkeypatch.setattr(lifecycle, "_load_registry", lambda *args, **kwargs: registry)

    with pytest.raises(ValueError, match="cannot purge"):
        lifecycle.delete_enterprise_knowledge_source(
            PROJECT,
            SOURCE_REF,
            root=tmp_path,
            actor=ACTOR,
            purge_bytes=True,
            retirement_reason="remote_scope_absent",
        )


def test_reactivation_preserves_occurrence_identity_and_history() -> None:
    registry, canonical, occurrence = _registry(status="retired_remote_scope")
    occurrence.update(
        {
            "retired_at_utc": "2026-08-01T10:00:00Z",
            "retired_reason": "scope_absent",
            "retirement_evidence": {"complete_snapshot_count": 2},
        }
    )
    registry["content_assets"][0]["source_occurrence_ids"] = []
    registry["interpretation_assets"][0]["source_occurrence_ids"] = []
    registry["interpretation_assets"][0]["source_refs"] = []
    canonical["source_occurrence_ids"] = []
    canonical["source_refs"] = []
    occurrence_id = occurrence["source_occurrence_id"]

    core._reactivate_existing_occurrence(
        registry,
        canonical=canonical,
        occurrence=occurrence,
        actor=ACTOR,
    )

    assert occurrence["source_occurrence_id"] == occurrence_id
    assert occurrence["status"] == "active"
    assert occurrence["reactivated_from_status"] == "retired_remote_scope"
    assert occurrence["lifecycle_history"][-1]["status"] == (
        "retired_remote_scope"
    )
    assert not occurrence.get("retired_reason")
    assert occurrence_id in canonical["source_occurrence_ids"]
    assert SOURCE_REF in canonical["source_refs"]
    assert occurrence_id in registry["content_assets"][0][
        "source_occurrence_ids"
    ]
    assert occurrence_id in registry["interpretation_assets"][0][
        "source_occurrence_ids"
    ]


def test_inventory_counts_remote_retirement_separately_from_deletion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry, _, occurrence = _registry(status="retired_remote_scope")
    monkeypatch.setattr(lifecycle, "_load_registry", lambda *args, **kwargs: registry)

    inventory = lifecycle.list_enterprise_knowledge_sources(
        PROJECT,
        root=tmp_path,
        include_deleted=True,
    )

    assert inventory["summary"]["retired_remote_scope_count"] == 1
    assert inventory["summary"]["deleted_source_count"] == 0
    assert inventory["sources"][0]["status"] == "retired_remote_scope"
    assert inventory["governance"][
        "remote_scope_retirement_is_not_customer_source_deletion"
    ] is True
    assert inventory["sources"][0]["source_occurrence_id"] == occurrence[
        "source_occurrence_id"
    ]
