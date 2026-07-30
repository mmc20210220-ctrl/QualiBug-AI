from __future__ import annotations

import gzip
import io
import stat
import zipfile
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center.archive_ingestion import (
    ARCHIVE_INGESTION_RECEIPT_SCHEMA,
    ArchiveLimits,
    ArchiveProviderRegistry,
    expand_archive_documents,
    ingest_enterprise_knowledge_archives,
)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def test_zip_members_become_canonical_document_envelopes_with_provenance() -> None:
    data = _zip_bytes(
        {
            "需求/订单PRD.md": b"# Order\namount > 50000 requires approval",
            "历史缺陷.csv": b"id,title\nBUG-1,duplicate payment",
        }
    )

    result = expand_archive_documents(
        [{"content_bytes": data, "filename": "ERP资料.zip", "tags": ["customer"]}]
    )

    assert result.errors == []
    assert len(result.documents) == 2
    names = sorted(row["filename"] for row in result.documents)
    assert names == ["ERP资料.zip!/历史缺陷.csv", "ERP资料.zip!/需求/订单PRD.md"]
    for row in result.documents:
        assert row["content_bytes"]
        assert "archive_member" in row["tags"]
        assert row["external_ref"].startswith("archive://")
        provenance = row["archive_provenance"]
        assert provenance["root_archive_filename"] == "ERP资料.zip"
        assert provenance["root_archive_hash"]
        assert len(provenance["chain"]) == 1
    assert result.receipts[0]["status"] == "COMPLETE"
    assert result.receipts[0]["archive_is_transport_not_business_authority"] is True


def test_nested_archive_chain_is_preserved_without_parallel_parser() -> None:
    inner = _zip_bytes({"rules/审批规则.md": b"# Rule\nfinance approval required"})
    outer = _zip_bytes({"附件/规则包.zip": inner})

    result = expand_archive_documents(
        [{"content_bytes": outer, "filename": "项目资料.zip"}]
    )

    assert result.errors == []
    assert len(result.documents) == 1
    member = result.documents[0]
    assert member["filename"] == "项目资料.zip!/附件/规则包.zip!/rules/审批规则.md"
    assert len(member["archive_provenance"]["chain"]) == 2
    assert len(result.receipts) == 2
    assert result.receipts[0]["nested_archive_count"] == 1


def test_path_traversal_member_blocks_archive_expansion() -> None:
    data = _zip_bytes({"../secrets.txt": b"should never escape"})

    result = expand_archive_documents(
        [{"content_bytes": data, "filename": "unsafe.zip"}]
    )

    assert result.documents == []
    assert result.errors
    assert result.errors[0]["code"] == "ARCHIVE_MEMBER_PATH_TRAVERSAL"
    assert result.receipts[0]["status"] == "BLOCKED"


def test_zip_symlink_is_rejected_fail_visibly() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("linked.txt")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target.txt")

    result = expand_archive_documents(
        [{"content_bytes": buffer.getvalue(), "filename": "links.zip"}]
    )

    assert result.documents == []
    assert result.errors[0]["code"] == "ARCHIVE_LINK_MEMBER_FORBIDDEN"


def test_compression_bomb_ratio_is_blocked_before_member_read() -> None:
    data = _zip_bytes({"huge.txt": b"A" * 200_000})

    result = expand_archive_documents(
        [{"content_bytes": data, "filename": "bomb.zip"}],
        limits=ArchiveLimits(max_compression_ratio=5.0),
    )

    assert result.documents == []
    assert result.errors[0]["code"] == "ARCHIVE_MEMBER_COMPRESSION_RATIO_EXCEEDED"


def test_plain_gzip_is_single_member_transport() -> None:
    data = gzip.compress(b"id,title\nBUG-1,timeout")

    result = expand_archive_documents(
        [{"content_bytes": data, "filename": "bugs.csv.gz"}]
    )

    assert result.errors == []
    assert len(result.documents) == 1
    assert result.documents[0]["filename"] == "bugs.csv.gz!/bugs.csv"
    assert result.documents[0]["content_bytes"].startswith(b"id,title")


def test_missing_archive_provider_runtime_is_explicit() -> None:
    class MissingProvider:
        name = "missing-rar-provider"
        version = "1"

        def available(self):
            return False

        def supports(self, filename, data):
            return filename.endswith(".rar")

        def members(self, filename, data, limits):  # pragma: no cover
            raise AssertionError

    result = expand_archive_documents(
        [{"content_bytes": b"Rar!\x1a\x07opaque", "filename": "legacy.rar"}],
        registry=ArchiveProviderRegistry([MissingProvider()]),
    )

    assert result.documents == []
    assert result.errors[0]["code"] == "ARCHIVE_RUNTIME_DEPENDENCY_UNAVAILABLE"
    assert result.receipts[0]["status"] == "BLOCKED"


def test_ingestion_retains_archive_and_delegates_members_to_canonical_ingest(tmp_path) -> None:
    archive_path = tmp_path / "资料.zip"
    archive_path.write_bytes(_zip_bytes({"需求.md": b"# Requirement\nsubmit then approve"}))
    captured: list[dict] = []

    def fake_ingest(project, documents, *, root, actor):
        for row in documents:
            captured.append(dict(row))
            assert Path(row["file_path"]).is_file()
            assert row["filename"] == "资料.zip!/需求.md"
            assert row["external_ref"].startswith("archive://")
        return {
            "ok": True,
            "created": [{"source_id": "src_member"}],
            "duplicates": [],
            "errors": [],
            "warnings": [],
            "source_count": 1,
        }

    result = ingest_enterprise_knowledge_archives(
        "archive_project",
        [archive_path],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
        ingest_documents=fake_ingest,
    )

    assert result["schema"] == ARCHIVE_INGESTION_RECEIPT_SCHEMA
    assert result["ok"] is True
    assert result["expanded_document_count"] == 1
    assert result["created"] == [{"source_id": "src_member"}]
    assert len(captured) == 1
    artifact = result["archive_transport_artifacts"][0]
    assert (tmp_path / artifact["stored_path"]).read_bytes() == archive_path.read_bytes()
    assert result["archive_is_transport_not_business_authority"] is True
    assert result["members_use_canonical_document_ingestion"] is True
