from __future__ import annotations

from ai_test_asset_center import enterprise_knowledge_center, enterprise_source_registry
from ai_test_asset_center.private_pilot_ingest_authority import (
    ingest_uploaded_enterprise_material,
)


def test_explicit_archive_type_sets_member_inheritance_contract(monkeypatch, tmp_path) -> None:
    source = tmp_path / "需求包.zip"
    source.write_bytes(b"PK synthetic fixture")
    captured: list[dict] = []

    def fake_ingest(project, documents, *, root, actor):
        captured.extend(documents)
        return {
            "ok": True,
            "created": [{"source_id": "src_member"}],
            "duplicates": [],
            "errors": [],
            "warnings": [],
            "source_count": 1,
            "archive_expansion": {
                "status": "COMPLETE",
                "document_count": 1,
                "package_count": 1,
                "error_count": 0,
                "warning_count": 0,
                "packages": [{"status": "COMPLETE"}],
                "canonical_archive_authority": "archive_ingestion_core",
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
        lambda *args, **kwargs: {
            "source_id": "src_composed",
            "source_hash": "a" * 64,
            "part_count": 1,
            "composed_from": [],
        },
    )

    result = ingest_uploaded_enterprise_material(
        project="archive_type_override",
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
        out_path=source,
        filename=source.name,
        raw=source.read_bytes(),
        explicit_type="prd",
    )

    assert result["ok"] is True
    assert result["type_resolution"] == "explicit_member_override"
    assert result["doc_info"]["archive_member_type_mode"] == "explicit_member_override"
    assert captured == [
        {
            "file_path": str(source),
            "filename": source.name,
            "source_type": "prd",
            "inherit_source_type_to_members": True,
        }
    ]
