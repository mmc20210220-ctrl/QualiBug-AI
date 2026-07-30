from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import document_change_watcher
from ai_test_asset_center import enterprise_source_registry
from ai_test_asset_center import enterprise_knowledge_center
from ai_test_asset_center.private_pilot_ingest_authority import (
    UPLOAD_INGEST_AUTHORITY_SCHEMA,
    ingest_uploaded_enterprise_material,
)


def _composed_manifest(*args, **kwargs):
    return {
        "source_id": "src_project_composed_all",
        "source_hash": "a" * 64,
        "composed_from": [{"source_id": "knowledge_1", "source_hash": "b" * 64}],
        "part_count": 1,
    }


def test_normal_upload_uses_knowledge_authority_and_composed_runtime_manifest(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "订单PRD.md"
    source.write_text("# 产品需求\n金额超过五万需要审批", encoding="utf-8")
    captured: list[dict] = []

    monkeypatch.setattr(
        document_change_watcher,
        "ingest_document",
        lambda path: {"ok": True, "text": "# 产品需求\n金额超过五万需要审批"},
    )

    def fake_ingest(project, documents, *, root, actor):
        captured.extend(documents)
        return {
            "ok": True,
            "created": [
                {
                    "source_id": "src_prd",
                    "runtime_source_manifest": {
                        "source_id": "knowledge_1",
                        "source_hash": "b" * 64,
                    },
                }
            ],
            "duplicates": [],
            "errors": [],
            "warnings": [],
            "source_count": 1,
            "archive_expansion": {
                "status": "COMPLETE",
                "document_count": 1,
                "package_count": 0,
                "error_count": 0,
                "warning_count": 0,
                "packages": [],
            },
        }

    monkeypatch.setattr(
        enterprise_knowledge_center,
        "ingest_enterprise_knowledge_documents",
        fake_ingest,
    )
    monkeypatch.setattr(
        enterprise_source_registry,
        "compose_project_source_manifest",
        _composed_manifest,
    )
    monkeypatch.setattr(
        enterprise_source_registry,
        "register_source_asset",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP upload must not register a second source asset")
        ),
    )

    result = ingest_uploaded_enterprise_material(
        project="project_1",
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
        out_path=source,
        filename=source.name,
        raw=source.read_bytes(),
    )

    assert result["schema"] == UPLOAD_INGEST_AUTHORITY_SCHEMA
    assert result["ok"] is True
    assert result["transport"] == "document"
    assert result["doc_type"] == "prd"
    assert result["source_id"] == "src_prd"
    assert result["source_manifest"]["source_id"] == "src_project_composed_all"
    assert result["second_source_registration_performed"] is False
    assert result["parallel_archive_parser_called"] is False
    assert captured[0]["file_path"] == str(source)


def test_archive_upload_uses_canonical_knowledge_transaction_and_skips_document_watcher(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "资料.zip"
    source.write_bytes(b"PK synthetic fixture")
    called = {"knowledge": 0, "document_watcher": 0}
    captured: list[dict] = []

    def fake_ingest(project, documents, *, root, actor):
        called["knowledge"] += 1
        captured.extend(documents)
        return {
            "ok": True,
            "created": [{"source_id": "src_member_1"}, {"source_id": "src_member_2"}],
            "duplicates": [],
            "errors": [],
            "warnings": [],
            "source_count": 2,
            "archive_expansion": {
                "status": "COMPLETE",
                "document_count": 2,
                "package_count": 1,
                "error_count": 0,
                "warning_count": 0,
                "packages": [{"status": "COMPLETE", "archive_hash": "c" * 64}],
                "canonical_archive_authority": "archive_ingestion_core",
                "duplicate_archive_parser_present": False,
            },
        }

    monkeypatch.setattr(
        enterprise_knowledge_center,
        "ingest_enterprise_knowledge_documents",
        fake_ingest,
    )
    monkeypatch.setattr(
        document_change_watcher,
        "ingest_document",
        lambda path: called.__setitem__(
            "document_watcher", called["document_watcher"] + 1
        ),
    )
    monkeypatch.setattr(
        enterprise_source_registry,
        "compose_project_source_manifest",
        _composed_manifest,
    )

    result = ingest_uploaded_enterprise_material(
        project="project_2",
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
        out_path=source,
        filename=source.name,
        raw=source.read_bytes(),
    )

    assert result["ok"] is True
    assert result["transport"] == "archive"
    assert result["doc_type"] == "archive_package"
    assert result["source_ids"] == ["src_member_1", "src_member_2"]
    assert result["doc_info"]["expanded_document_count"] == 2
    assert result["doc_info"]["canonical_archive_authority"] == "archive_ingestion_core"
    assert called == {"knowledge": 1, "document_watcher": 0}
    assert captured == [{"file_path": str(source), "filename": source.name}]
    assert result["parallel_archive_parser_called"] is False
    assert result["canonical_runtime_corpus_used"] is True


def test_archive_member_failure_is_returned_without_composing_runtime_manifest(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "unsafe.zip"
    source.write_bytes(b"PK unsafe")
    composed = {"called": False}

    monkeypatch.setattr(
        enterprise_knowledge_center,
        "ingest_enterprise_knowledge_documents",
        lambda *args, **kwargs: {
            "ok": False,
            "created": [],
            "duplicates": [],
            "errors": [{"code": "ARCHIVE_MEMBER_PATH_TRAVERSAL"}],
            "warnings": [],
            "archive_expansion": {
                "status": "BLOCKED",
                "document_count": 0,
                "package_count": 1,
                "error_count": 1,
                "warning_count": 0,
                "packages": [{"status": "BLOCKED"}],
                "canonical_archive_authority": "archive_ingestion_core",
            },
        },
    )

    def should_not_compose(*args, **kwargs):
        composed["called"] = True
        raise AssertionError

    monkeypatch.setattr(
        enterprise_source_registry,
        "compose_project_source_manifest",
        should_not_compose,
    )

    result = ingest_uploaded_enterprise_material(
        project="project_3",
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
        out_path=source,
        filename=source.name,
        raw=source.read_bytes(),
    )

    assert result["ok"] is False
    assert result["ingest_result"]["errors"][0]["code"] == "ARCHIVE_MEMBER_PATH_TRAVERSAL"
    assert result["doc_info"]["archive_error_count"] == 1
    assert result["parallel_archive_parser_called"] is False
    assert composed["called"] is False
