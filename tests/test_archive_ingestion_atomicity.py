from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center.archive_ingestion import (
    ArchiveLimits,
    ArchiveMember,
    ArchiveProviderRegistry,
    expand_archive_documents,
    ingest_enterprise_knowledge_archives,
)


def _zip_bytes(files: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files:
            archive.writestr(name, value)
    return buffer.getvalue()


def test_late_member_failure_rolls_back_previously_expanded_sibling() -> None:
    class OrderedProvider:
        name = "ordered-atomicity-provider"
        version = "1"

        def available(self) -> bool:
            return True

        def supports(self, filename: str, data: bytes) -> bool:
            return filename.endswith(".zip")

        def members(self, filename: str, data: bytes, limits: ArchiveLimits):
            return [
                ArchiveMember(path="safe.md", data=b"# Safe requirement"),
                ArchiveMember(path="../escape.txt", data=b"must block the package"),
            ]

    result = expand_archive_documents(
        [{"content_bytes": b"synthetic", "filename": "materials.zip"}],
        registry=ArchiveProviderRegistry([OrderedProvider()]),
    )

    assert result.documents == []
    assert result.errors
    assert result.errors[0]["code"] == "ARCHIVE_MEMBER_PATH_TRAVERSAL"
    assert result.receipts[0]["status"] == "BLOCKED"
    assert result.receipts[0]["partial_member_activation_rolled_back"] is True
    assert result.receipts[0]["expanded_document_count"] == 0


def test_nested_failure_rolls_back_outer_safe_siblings() -> None:
    malicious_inner = _zip_bytes([("../escape.txt", b"blocked")])
    outer = _zip_bytes(
        [
            ("requirements/safe.md", b"# Requirement\nsubmit then approve"),
            ("attachments/unsafe.zip", malicious_inner),
        ]
    )

    result = expand_archive_documents(
        [{"content_bytes": outer, "filename": "enterprise-materials.zip"}]
    )

    assert result.documents == []
    assert any(row["code"] == "ARCHIVE_MEMBER_PATH_TRAVERSAL" for row in result.errors)
    assert result.receipts[0]["status"] == "BLOCKED"
    assert result.receipts[0]["partial_member_activation_rolled_back"] is True
    assert result.receipts[1]["status"] == "BLOCKED"


def test_nested_archives_share_one_top_level_member_budget() -> None:
    first = _zip_bytes([("first.md", b"first")])
    second = _zip_bytes([("second.md", b"second")])
    outer = _zip_bytes([("first.zip", first), ("second.zip", second)])

    result = expand_archive_documents(
        [{"content_bytes": outer, "filename": "budget.zip"}],
        limits=ArchiveLimits(max_members=3),
    )

    assert result.documents == []
    assert any(row["code"] == "ARCHIVE_MEMBER_COUNT_EXCEEDED" for row in result.errors)
    assert result.receipts[0]["status"] == "BLOCKED"
    assert all(row["expanded_document_count"] == 0 for row in result.receipts)


def test_junk_only_package_is_blocked_not_reported_complete() -> None:
    package = _zip_bytes([("__MACOSX/._requirements", b"metadata")])

    result = expand_archive_documents(
        [{"content_bytes": package, "filename": "junk.zip"}]
    )

    assert result.documents == []
    assert any(row["code"] == "ARCHIVE_NO_IMPORTABLE_MEMBERS" for row in result.errors)
    assert result.receipts[0]["status"] == "BLOCKED"
    assert result.ignored_members[0]["code"] == "ARCHIVE_SYSTEM_JUNK_SKIPPED"


def test_archive_provenance_survives_canonical_ingestion_boundary(tmp_path: Path) -> None:
    archive_path = tmp_path / "materials.zip"
    archive_path.write_bytes(_zip_bytes([("requirements/order.md", b"# Order")]))

    def fake_ingest(project, documents, *, root, actor):
        assert len(documents) == 1
        external_ref = documents[0]["external_ref"]
        return {
            "ok": True,
            "created": [{"source_id": "src_member", "external_ref": external_ref}],
            "duplicates": [],
            "errors": [],
            "warnings": [],
            "source_count": 1,
        }

    result = ingest_enterprise_knowledge_archives(
        "archive_atomic_project",
        [archive_path],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
        ingest_documents=fake_ingest,
    )

    assert result["ok"] is True
    created = result["created"][0]
    assert created["archive_provenance"]["root_archive_filename"] == "materials.zip"
    assert created["archive_provenance"]["root_archive_hash"]
    assert result["failed_archives_activate_no_members"] is True
