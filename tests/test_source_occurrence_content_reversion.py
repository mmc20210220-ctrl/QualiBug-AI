from __future__ import annotations

from pathlib import Path

import pytest

import ai_test_asset_center.enterprise_knowledge_center.source_occurrence_core as core

PROJECT = "enterprise-project"
SOURCE_REF = "connector://feishu-prod/feishu-wiki-docx/wiki%3Aspace%3Anode"
ACTOR = {"name": "tester", "role": "knowledge_admin"}


def _canonical(source_id: str, content_hash: str) -> dict:
    return {
        "source_id": source_id,
        "status": "active",
        "content_hash": content_hash,
        "source_type": "other_document",
        "original_name": "document.docx",
        "logical_key": f"canonical:{source_id}",
        "source_occurrence_ids": [],
        "source_refs": [],
    }


def test_content_a_to_b_to_a_reuses_a_and_supersedes_b(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical_a = _canonical("canonical-a", "a" * 64)
    canonical_b = _canonical("canonical-b", "b" * 64)
    interpretation_a = core._interpretation_asset_id(
        canonical_a["content_hash"],
        "other_document",
        "docx",
    )
    interpretation_b = core._interpretation_asset_id(
        canonical_b["content_hash"],
        "other_document",
        "docx",
    )
    occurrence_a_id = core._source_occurrence_id(
        PROJECT,
        SOURCE_REF,
        canonical_a["content_hash"],
        interpretation_a,
    )
    occurrence_b_id = core._source_occurrence_id(
        PROJECT,
        SOURCE_REF,
        canonical_b["content_hash"],
        interpretation_b,
    )
    occurrence_a = {
        "source_occurrence_id": occurrence_a_id,
        "source_ref": SOURCE_REF,
        "canonical_source_id": canonical_a["source_id"],
        "content_asset_id": core._content_asset_id(canonical_a["content_hash"]),
        "interpretation_asset_id": interpretation_a,
        "content_hash": canonical_a["content_hash"],
        "source_type": "other_document",
        "format_identity": "docx",
        "filename": "document.docx",
        "version": 1,
        "status": "superseded",
        "superseded_reason": "source_occurrence_superseded",
    }
    occurrence_b = {
        "source_occurrence_id": occurrence_b_id,
        "source_ref": SOURCE_REF,
        "canonical_source_id": canonical_b["source_id"],
        "content_asset_id": core._content_asset_id(canonical_b["content_hash"]),
        "interpretation_asset_id": interpretation_b,
        "content_hash": canonical_b["content_hash"],
        "source_type": "other_document",
        "format_identity": "docx",
        "filename": "document.docx",
        "version": 2,
        "status": "active",
    }
    canonical_b["source_occurrence_ids"] = [occurrence_b_id]
    canonical_b["source_refs"] = [SOURCE_REF]
    registry = {
        "sources": [canonical_a, canonical_b],
        "source_occurrences": [occurrence_a, occurrence_b],
        "content_assets": [
            {
                "content_asset_id": occurrence_a["content_asset_id"],
                "content_hash": canonical_a["content_hash"],
                "canonical_source_ids": [canonical_a["source_id"]],
                "source_occurrence_ids": [],
                "status": "ACTIVE",
            },
            {
                "content_asset_id": occurrence_b["content_asset_id"],
                "content_hash": canonical_b["content_hash"],
                "canonical_source_ids": [canonical_b["source_id"]],
                "source_occurrence_ids": [occurrence_b_id],
                "status": "ACTIVE",
            },
        ],
        "interpretation_assets": [
            {
                "interpretation_asset_id": interpretation_a,
                "canonical_source_id": canonical_a["source_id"],
                "content_asset_id": occurrence_a["content_asset_id"],
                "content_hash": canonical_a["content_hash"],
                "source_type": "other_document",
                "format_identity": "docx",
                "source_occurrence_ids": [],
                "source_refs": [],
                "status": "ACTIVE",
            },
            {
                "interpretation_asset_id": interpretation_b,
                "canonical_source_id": canonical_b["source_id"],
                "content_asset_id": occurrence_b["content_asset_id"],
                "content_hash": canonical_b["content_hash"],
                "source_type": "other_document",
                "format_identity": "docx",
                "source_occurrence_ids": [occurrence_b_id],
                "source_refs": [SOURCE_REF],
                "status": "ACTIVE",
            },
        ],
    }
    monkeypatch.setattr(core, "_reactivate_canonical_if_needed", lambda **kwargs: None)

    occurrence, created, orphan_candidates = core._register_occurrence(
        registry,
        project=PROJECT,
        root=tmp_path,
        actor=ACTOR,
        canonical=canonical_a,
        result_row={
            "source_id": canonical_a["source_id"],
            "source_type": "other_document",
            "original_name": "document.docx",
            "external_ref": SOURCE_REF,
            "reason": "same_content_hash",
        },
        envelope={
            "source_type": "other_document",
            "filename": "document.docx",
            "external_ref": SOURCE_REF,
        },
    )

    active = [
        row
        for row in registry["source_occurrences"]
        if row["status"] == "active" and row["source_ref"] == SOURCE_REF
    ]
    assert created is False
    assert occurrence is occurrence_a
    assert occurrence_a["source_occurrence_id"] == occurrence_a_id
    assert occurrence_a["status"] == "active"
    assert occurrence_a["content_reversion_reconciled"] is True
    assert occurrence_a["single_active_source_ref_invariant"] is True
    assert active == [occurrence_a]
    assert occurrence_b["status"] == "superseded"
    assert occurrence_b["superseded_by_occurrence_id"] == occurrence_a_id
    assert occurrence_b["superseded_reason"] == (
        "source_content_reverted_to_prior_occurrence"
    )
    assert canonical_b["source_occurrence_ids"] == []
    assert canonical_b["source_refs"] == []
    assert canonical_b["source_id"] in orphan_candidates
    assert canonical_a["source_id"] not in orphan_candidates
