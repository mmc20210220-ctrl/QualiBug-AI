"""T03 Canonical Artifact contract tests.

The canonical artifact projection is read-only: it joins the runtime source
registry (asset -> versions -> chunks) with the enterprise knowledge-center
registry (content/interpretation/source-occurrence). These tests verify the
contract, the query interface, and block-level version diff without inventing
any parallel storage model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.enterprise_knowledge_center import (
    CanonicalArtifactError,
    diff_artifact_versions,
    ingest_enterprise_knowledge_documents,
    list_artifact_versions,
    project_canonical_artifacts,
    query_canonical_artifacts,
)

HASH_V1 = "a" * 64
HASH_V2 = "b" * 64
PROJ_HASH_V1 = "c" * 64
PROJ_HASH_V2 = "d" * 64


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _knowledge_registry_payload() -> dict:
    return {
        "project_id": "artifact_test",
        "sources": [
            {
                "source_id": "src_v1",
                "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "version": 1,
                "status": "active",
                "source_type": "other_document",
                "format_identity": "md",
                "original_name": "guideline.md",
                "content_asset_id": "content:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "interpretation_asset_id": "interpretation:v1",
                "parse": {"parse_status": "parsed", "parse_reused": False},
                "runtime_source_manifest": {
                    "source_id": "knowledge_artifact_lineage",
                    "source_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                    "source_version_id": "srcv_v1",
                    "source_type": "other_document",
                },
            },
            {
                "source_id": "src_v2",
                "content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "version": 2,
                "status": "active",
                "source_type": "other_document",
                "format_identity": "md",
                "original_name": "guideline.md",
                "content_asset_id": "content:sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "interpretation_asset_id": "interpretation:v2",
                "parse": {"parse_status": "parsed", "parse_reused": False},
                "runtime_source_manifest": {
                    "source_id": "knowledge_artifact_lineage",
                    "source_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                    "source_version_id": "srcv_v2",
                    "source_type": "other_document",
                },
            },
        ],
        "content_assets": [
            {
                "content_asset_id": "content:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "status": "ACTIVE",
            },
            {
                "content_asset_id": "content:sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "status": "ACTIVE",
            },
        ],
        "interpretation_assets": [
            {"interpretation_asset_id": "interpretation:v1", "status": "ACTIVE"},
            {"interpretation_asset_id": "interpretation:v2", "status": "ACTIVE"},
        ],
        "source_occurrences": [
            {
                "source_occurrence_id": "occ-file",
                "canonical_source_id": "src_v1",
                "source_ref": "docs/guideline.md",
                "external_ref": "docs/guideline.md",
                "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "version": 1,
                "status": "active",
                "tags": [],
            },
            {
                "source_occurrence_id": "occ-online",
                "canonical_source_id": "src_v1",
                "source_ref": "https://example.com/docs/guideline.md",
                "external_ref": "https://example.com/docs/guideline.md",
                "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "version": 1,
                "status": "active",
                "tags": [],
            },
            {
                "source_occurrence_id": "occ-superseded",
                "canonical_source_id": "src_v2",
                "source_ref": "docs/guideline.md",
                "external_ref": "docs/guideline.md",
                "content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "version": 2,
                "status": "superseded",
                "tags": [],
            },
        ],
    }


def _runtime_registry_payload() -> dict:
    return {
        "schema_version": "enterprise-source-registry-v1",
        "project_id": "artifact_test",
        "assets": {
            "knowledge_artifact_lineage": {
                "source_id": "knowledge_artifact_lineage",
                "source_type": "other_document",
                "latest_source_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                "latest_version_id": "srcv_v2",
                "versions": [
                    {
                        "version_id": "srcv_v1",
                        "source_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                        "byte_count": 100,
                        "source_type": "other_document",
                        "source_origin": "registered_source_registry",
                        "filename": "guideline.md",
                        "external_ref": "docs/guideline.md",
                        "registered_at_utc": "2026-08-01T00:00:00Z",
                        "metadata": {
                            "knowledge_source_id": "src_v1",
                            "knowledge_source_version": 1,
                            "original_content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        },
                        "blob_ref": "platform_workspace/artifact_test/source_registry/blobs/cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.txt",
                    },
                    {
                        "version_id": "srcv_v2",
                        "source_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                        "byte_count": 120,
                        "source_type": "other_document",
                        "source_origin": "registered_source_registry",
                        "filename": "guideline.md",
                        "external_ref": "docs/guideline.md",
                        "registered_at_utc": "2026-08-01T01:00:00Z",
                        "metadata": {
                            "knowledge_source_id": "src_v2",
                            "knowledge_source_version": 2,
                            "original_content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        },
                        "blob_ref": "platform_workspace/artifact_test/source_registry/blobs/dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd.txt",
                    },
                ],
            }
        },
        "updated_at_utc": "2026-08-01T01:00:00Z",
    }


def _seed_fixture(root: Path, *, chunks: dict[str, list[dict]] | None = None) -> Path:
    project = "artifact_test"
    knowledge_path = (
        root
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "source_registry.json"
    )
    runtime_path = root / "platform_workspace" / project / "source_registry" / "registry.json"
    _write_json(knowledge_path, _knowledge_registry_payload())
    _write_json(runtime_path, _runtime_registry_payload())
    for digest, rows in (chunks or {}).items():
        _write_json(
            root
            / "platform_workspace"
            / project
            / "source_registry"
            / "chunks"
            / digest
            / "chunks.json",
            {"source_hash": digest, "chunk_count": len(rows), "chunks": rows},
        )
    return root


def _chunk(block_id: str, digest: str, text: str = "") -> dict:
    return {
        "chunk_id": f"chunk:src:{block_id}",
        "block_id": block_id,
        "chunk_type": "paragraph",
        "content": text,
        "content_hash": digest,
        "confidence": 1.0,
        "extraction_method": "document_ir",
    }


def test_empty_projection_is_honest(tmp_path: Path) -> None:
    artifacts = project_canonical_artifacts("artifact_empty", tmp_path)
    assert artifacts == []
    query = query_canonical_artifacts("artifact_empty", tmp_path)
    assert query["status"] == "NO_MATCH"
    assert query["match_count"] == 0
    with pytest.raises(CanonicalArtifactError):
        diff_artifact_versions("artifact_empty", "missing_artifact", tmp_path)


def test_projection_links_runtime_and_knowledge_registries(tmp_path: Path) -> None:
    _seed_fixture(tmp_path)
    artifacts = project_canonical_artifacts("artifact_test", tmp_path)
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["artifact_id"] == "knowledge_artifact_lineage"
    assert artifact["version_count"] == 2
    assert artifact["content_block_count"] == 0
    assert artifact["knowledge_instance_count"] == 2
    assert artifact["source_relation_count"] == 2
    refs = {row["source_ref"] for row in artifact["source_relations"]}
    assert refs == {
        "docs/guideline.md",
        "https://example.com/docs/guideline.md",
    }
    assert artifact["access_scope"]["scope"] == "PROJECT_SCOPED"
    assert artifact["entity_mentions"]["status"] == "NOT_PROJECTED"
    assert artifact["sync_cursor"]["persisted"] is False
    versions = artifact["versions"]
    assert {row["version_id"] for row in versions} == {"srcv_v1", "srcv_v2"}
    v1 = next(row for row in versions if row["version_id"] == "srcv_v1")
    assert v1["knowledge_source_id"] == "src_v1"
    assert v1["knowledge_source_version"] == 1


def test_query_filters_by_ref_hash_and_artifact_id(tmp_path: Path) -> None:
    _seed_fixture(tmp_path)
    by_ref = query_canonical_artifacts(
        "artifact_test",
        tmp_path,
        source_ref="https://example.com/docs/guideline.md",
    )
    assert by_ref["status"] == "MATCHED"
    assert by_ref["match_count"] == 1
    by_hash = query_canonical_artifacts(
        "artifact_test", tmp_path, content_hash="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    assert by_hash["match_count"] == 1
    by_id = query_canonical_artifacts(
        "artifact_test", tmp_path, artifact_id="knowledge_artifact_lineage"
    )
    assert by_id["match_count"] == 1
    missing = query_canonical_artifacts("artifact_test", tmp_path, source_ref="nope")
    assert missing["status"] == "NO_MATCH"
    versions = list_artifact_versions(
        "artifact_test", "knowledge_artifact_lineage", tmp_path
    )
    assert versions["version_count"] == 2
    with pytest.raises(CanonicalArtifactError):
        list_artifact_versions("artifact_test", "unknown_artifact", tmp_path)


def test_diff_unchanged_when_content_hash_identical(tmp_path: Path) -> None:
    _seed_fixture(tmp_path)
    result = diff_artifact_versions(
        "artifact_test",
        "knowledge_artifact_lineage",
        tmp_path,
        base_hash="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        head_hash="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    )
    assert result["verdict"] == "UNCHANGED"
    assert result["content_hash_changed"] is False
    assert result["summary"]["review_required"] is False


def test_diff_block_level_changes(tmp_path: Path) -> None:
    _seed_fixture(
        tmp_path,
        chunks={
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": [_chunk("b1", "fp-old", "old text")],
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": [
                _chunk("b1", "fp-new", "new text"),
                _chunk("b2", "fp-added", "added text"),
            ],
        },
    )
    result = diff_artifact_versions(
        "artifact_test",
        "knowledge_artifact_lineage",
        tmp_path,
    )
    assert result["verdict"] == "CHANGED"
    assert result["content_hash_changed"] is True
    assert result["block_identity_mode"] == "BLOCK_ID"
    assert result["summary"]["changed_block_count"] == 1
    assert result["summary"]["added_block_count"] == 1
    assert result["summary"]["removed_block_count"] == 0
    assert result["changed_blocks"][0]["block_key"] == "block:b1"
    assert result["added_blocks"][0]["block_key"] == "block:b2"
    assert result["summary"]["review_required"] is True


def test_diff_reports_review_gap_when_chunk_index_missing(tmp_path: Path) -> None:
    _seed_fixture(tmp_path)
    result = diff_artifact_versions(
        "artifact_test",
        "knowledge_artifact_lineage",
        tmp_path,
    )
    assert result["verdict"] == "CHANGED"
    assert result["content_hash_changed"] is True
    gaps = result["change_review_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["code"] == "ARTIFACT_CHUNK_INDEX_UNAVAILABLE"
    assert result["summary"]["review_required"] is True


def test_diff_requires_two_versions(tmp_path: Path) -> None:
    payload = _runtime_registry_payload()
    payload["assets"]["knowledge_artifact_lineage"]["versions"] = payload["assets"][
        "knowledge_artifact_lineage"
    ]["versions"][:1]
    project = "artifact_test"
    _write_json(
        tmp_path
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "source_registry.json",
        _knowledge_registry_payload(),
    )
    _write_json(
        tmp_path / "platform_workspace" / project / "source_registry" / "registry.json",
        payload,
    )
    with pytest.raises(CanonicalArtifactError):
        diff_artifact_versions(
            project,
            "knowledge_artifact_lineage",
            tmp_path,
        )


def test_file_and_online_sources_converge_to_one_artifact(tmp_path: Path) -> None:
    project = "artifact_model_it"
    actor = {"name": "t03-test", "role": "knowledge_admin"}
    content = (
        b"# Payment Guideline\n\n"
        b"Users can create a payment after login. "
        b"Only admins can void a payment."
    )
    file_result = ingest_enterprise_knowledge_documents(
        project,
        [
            {
                "external_ref": "docs/payment_guideline.md",
                "filename": "payment_guideline.md",
                "content_bytes": content,
            }
        ],
        root=tmp_path,
        actor=actor,
    )
    assert not file_result.get("errors"), file_result.get("errors")

    online_result = ingest_enterprise_knowledge_documents(
        project,
        [
            {
                "external_ref": "https://example.com/docs/payment_guideline.md",
                "filename": "payment_guideline.md",
                "content_bytes": content,
            }
        ],
        root=tmp_path,
        actor=actor,
    )
    assert not online_result.get("errors"), online_result.get("errors")

    artifacts = project_canonical_artifacts(project, tmp_path)
    assert len(artifacts) == 1, artifacts
    artifact = artifacts[0]
    refs = {row["source_ref"] for row in artifact["source_relations"]}
    assert refs == {
        "docs/payment_guideline.md",
        "https://example.com/docs/payment_guideline.md",
    }
    assert artifact["version_count"] == 1
    assert artifact["content_block_count"] >= 1

    changed = ingest_enterprise_knowledge_documents(
        project,
        [
            {
                "external_ref": "docs/payment_guideline.md",
                "filename": "payment_guideline.md",
                "content_bytes": content + b"\n\nAdmins can also refund a payment.",
            }
        ],
        root=tmp_path,
        actor=actor,
    )
    assert not changed.get("errors"), changed.get("errors")

    artifacts_v2 = project_canonical_artifacts(project, tmp_path)
    assert len(artifacts_v2) == 1
    artifact_v2 = artifacts_v2[0]
    assert artifact_v2["version_count"] == 2
    diff = diff_artifact_versions(
        project, artifact_v2["artifact_id"], tmp_path
    )
    assert diff["verdict"] == "CHANGED"
    assert diff["content_hash_changed"] is True
