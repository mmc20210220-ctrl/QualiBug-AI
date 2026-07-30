from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import document_change_watcher
from ai_test_asset_center import enterprise_source_registry
from ai_test_asset_center.enterprise_knowledge_center import archive_ingestion
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
    assert captured[0]["file_path"] == str(source)


def test_archive_upload_delegates_members_and_never_parses_package_as_document(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "资料.zip"
    source.write_bytes(b"PK synthetic fixture")
    called = {"archive": 0, "document": 0}

    def fake_archive(project, paths, *, root, actor, source_type_hints):
        called["archive"] += 1
        assert list(paths) == [source]
        return {
            "schema": "qualibug.archive-ingestion-receipt.v1",
            "ok": True,
            "created": [{"source_id": "src_member_1"}, {"source_id": "src_member_2"}],
            "duplicates": [],
            "errors": [],
            "warnings": [],
            "source_count": 2,
            "expanded_document_count": 2,
            "archive_receipts": [{"status": "COMPLETE"}],
            "archive_transport_artifacts": [{"archive_hash": "c" * 64}],
        }

    monkeypatch.setattr(
        archive_ingestion,
        "ingest_enterprise_knowledge_archives",
        fake_archive,
    )
    monkeypatch.setattr(
        document_change_watcher,
        "ingest_document",
        lambda path: called.__setitem__("document", called["document"] + 1),
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
    assert called == {"archive": 1, "document": 0}
    assert result["canonical_runtime_corpus_used"] is True


def test_archive_member_failure_is_returned_without_composing_runtime_manifest(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "unsafe.zip"
    source.write_bytes(b"PK unsafe")
    composed = {"called": False}

    monkeypatch.setattr(
        archive_ingestion,
        "ingest_enterprise_knowledge_archives",
        lambda *args, **kwargs: {
            "ok": False,
            "created": [],
            "duplicates": [],
            "errors": [{"code": "ARCHIVE_MEMBER_PATH_TRAVERSAL"}],
            "archive_receipts": [{"status": "BLOCKED"}],
            "expanded_document_count": 0,
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
    assert composed["called"] is False
