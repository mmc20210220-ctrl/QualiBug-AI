from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center._utils import _load_registry
from ai_test_asset_center.enterprise_knowledge_center.source_occurrence_core import (
    ingest_enterprise_knowledge_documents,
)
from ai_test_asset_center.enterprise_knowledge_center.source_occurrence_lifecycle import (
    delete_enterprise_knowledge_source,
    list_enterprise_knowledge_sources,
    update_enterprise_knowledge_source,
)


ACTOR = {"name": "occurrence-lifecycle-test", "role": "project_owner"}
CONTENT = b"# Shared Rules\nOnly OPEN orders may be refunded.\n"


def _doc(ref: str) -> dict:
    return {
        "content_bytes": CONTENT,
        "filename": "rules.md",
        "source_type": "business_rules",
        "external_ref": ref,
    }


def _ingest_shared(tmp_path, project: str = "occurrence-lifecycle") -> dict:
    return ingest_enterprise_knowledge_documents(
        project,
        [_doc("support/rules.md"), _doc("finance/rules.md")],
        root=tmp_path,
        actor=ACTOR,
    )


def test_public_inventory_lists_occurrences_not_canonical_interpretations(tmp_path) -> None:
    result = _ingest_shared(tmp_path)
    assert result["ok"] is True

    inventory = list_enterprise_knowledge_sources(
        "occurrence-lifecycle",
        root=tmp_path,
    )

    assert inventory["summary"]["active_source_count"] == 2
    assert inventory["summary"]["canonical_source_count"] == 1
    assert {row["external_ref"] for row in inventory["sources"]} == {
        "support/rules.md",
        "finance/rules.md",
    }
    assert len({row["canonical_source_id"] for row in inventory["sources"]}) == 1
    assert all(row["inventory_role"] == "SOURCE_OCCURRENCE" for row in inventory["sources"])


def test_deleting_one_occurrence_retains_shared_bytes_chunks_and_runtime_source(tmp_path) -> None:
    _ingest_shared(tmp_path)
    inventory = list_enterprise_knowledge_sources(
        "occurrence-lifecycle",
        root=tmp_path,
    )
    support = next(row for row in inventory["sources"] if row["external_ref"] == "support/rules.md")
    canonical_source_id = support["canonical_source_id"]
    registry_before = _load_registry("occurrence-lifecycle", tmp_path)
    canonical_before = next(
        row for row in registry_before["sources"] if row["source_id"] == canonical_source_id
    )
    stored = tmp_path / canonical_before["stored_path"]
    assert stored.exists()

    deleted = delete_enterprise_knowledge_source(
        "occurrence-lifecycle",
        support["source_id"],
        root=tmp_path,
        actor=ACTOR,
    )

    assert deleted["remaining_active_occurrence_count"] == 1
    assert deleted["canonical_source_deleted"] is False
    assert deleted["shared_bytes_retained"] is True
    assert deleted["shared_chunks_retained"] is True
    assert deleted["shared_runtime_source_retained"] is True
    assert stored.exists()
    registry_after = _load_registry("occurrence-lifecycle", tmp_path)
    canonical_after = next(
        row for row in registry_after["sources"] if row["source_id"] == canonical_source_id
    )
    assert canonical_after["status"] == "active"


def test_deleting_last_occurrence_retires_canonical_content(tmp_path) -> None:
    _ingest_shared(tmp_path, project="occurrence-last-delete")
    inventory = list_enterprise_knowledge_sources(
        "occurrence-last-delete",
        root=tmp_path,
    )
    first, second = inventory["sources"]
    canonical_source_id = first["canonical_source_id"]
    registry = _load_registry("occurrence-last-delete", tmp_path)
    canonical = next(row for row in registry["sources"] if row["source_id"] == canonical_source_id)
    stored = tmp_path / canonical["stored_path"]
    assert stored.exists()

    delete_enterprise_knowledge_source(
        "occurrence-last-delete",
        first["source_id"],
        root=tmp_path,
        actor=ACTOR,
    )
    deleted = delete_enterprise_knowledge_source(
        "occurrence-last-delete",
        second["source_id"],
        root=tmp_path,
        actor=ACTOR,
    )

    assert deleted["remaining_active_occurrence_count"] == 0
    assert deleted["canonical_source_deleted"] is True
    assert deleted["shared_bytes_retained"] is False
    assert not stored.exists()
    final_inventory = list_enterprise_knowledge_sources(
        "occurrence-last-delete",
        root=tmp_path,
    )
    assert final_inventory["summary"]["active_source_count"] == 0


def test_canonical_id_cannot_select_a_winner_when_multiple_occurrences_exist(tmp_path) -> None:
    _ingest_shared(tmp_path, project="occurrence-ambiguous-delete")
    inventory = list_enterprise_knowledge_sources(
        "occurrence-ambiguous-delete",
        root=tmp_path,
    )
    canonical_source_id = inventory["sources"][0]["canonical_source_id"]

    with pytest.raises(ValueError, match="multiple active occurrences"):
        delete_enterprise_knowledge_source(
            "occurrence-ambiguous-delete",
            canonical_source_id,
            root=tmp_path,
            actor=ACTOR,
        )


def test_interpretation_identity_cannot_be_edited_in_place(tmp_path) -> None:
    _ingest_shared(tmp_path, project="occurrence-update")
    inventory = list_enterprise_knowledge_sources(
        "occurrence-update",
        root=tmp_path,
    )
    occurrence_id = inventory["sources"][0]["source_id"]

    with pytest.raises(ValueError, match="immutable"):
        update_enterprise_knowledge_source(
            "occurrence-update",
            occurrence_id,
            {"source_type": "openapi"},
            root=tmp_path,
            actor=ACTOR,
        )

    updated = update_enterprise_knowledge_source(
        "occurrence-update",
        occurrence_id,
        {"tags": ["approved", "finance"]},
        root=tmp_path,
        actor=ACTOR,
    )
    assert updated["source"]["tags"] == ["approved", "finance"]
