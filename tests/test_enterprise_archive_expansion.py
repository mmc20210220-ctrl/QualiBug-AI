from __future__ import annotations

import gzip
import io
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center import archive_ingestion
from ai_test_asset_center.enterprise_knowledge_center import archive_ingestion_core
from ai_test_asset_center.enterprise_knowledge_center.archive_expansion import (
    ArchiveExpansionPolicy,
    expand_document_envelopes,
)


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_zip_expands_to_standard_document_envelopes_with_package_provenance(tmp_path: Path) -> None:
    package = _zip_bytes(
        [
            ("requirements/order.md", b"# Order\nAmount over 50000 requires finance approval"),
            ("bugs/history.csv", b"id,title\nBUG-1,duplicate payment"),
        ]
    )

    batch = expand_document_envelopes(
        [{"content_bytes": package, "filename": "enterprise-materials.zip", "tags": ["pilot"]}],
        package_store_dir=tmp_path / "packages",
    )
    receipt = batch.to_dict()

    assert receipt["status"] == "COMPLETE"
    assert receipt["document_count"] == 2
    assert receipt["package_count"] == 1
    assert receipt["canonical_archive_authority"] == "archive_ingestion_core"
    assert receipt["duplicate_archive_parser_present"] is False
    assert {row["filename"] for row in batch.documents} == {
        "requirements/order.md",
        "bugs/history.csv",
    }
    for row in batch.documents:
        provenance = row["archive_provenance"]
        assert provenance["top_level_archive_name"] == "enterprise-materials.zip"
        assert provenance["member_hash"]
        assert provenance["archive_member_is_business_source"] is True
        assert provenance["archive_container_is_business_source"] is False
        assert row["tags"] == ["pilot"]
        assert not row.get("source_type")
    package_receipt = batch.packages[0]
    assert package_receipt["status"] == "COMPLETE"
    assert package_receipt["expanded_leaf_count"] == 2
    assert Path(package_receipt["stored_path"]).is_file()


def test_source_type_is_inherited_only_when_explicitly_requested() -> None:
    package = _zip_bytes([("rules.md", b"# Rule\nfinance approval required")])

    automatic = expand_document_envelopes(
        [{"content_bytes": package, "filename": "auto.zip", "source_type": "prd"}]
    )
    inherited = expand_document_envelopes(
        [
            {
                "content_bytes": package,
                "filename": "explicit.zip",
                "source_type": "prd",
                "inherit_source_type_to_members": True,
            }
        ]
    )

    assert automatic.documents[0].get("source_type") is None
    assert inherited.documents[0]["source_type"] == "prd"


def test_security_violation_rolls_back_all_members_from_that_package() -> None:
    package = _zip_bytes(
        [
            ("safe.md", b"safe material"),
            ("../escape.txt", b"must never activate"),
        ]
    )

    batch = expand_document_envelopes(
        [{"content_bytes": package, "filename": "malicious.zip"}]
    )

    assert batch.documents == []
    assert batch.packages[0]["status"] == "BLOCKED"
    assert any(row["code"] == "ARCHIVE_MEMBER_PATH_TRAVERSAL" for row in batch.errors)


def test_nested_archives_use_stable_virtual_member_paths() -> None:
    nested = _zip_bytes([("rules/approval.md", b"approval rule")])
    outer = _zip_bytes([("attachments/nested.zip", nested)])

    batch = expand_document_envelopes(
        [{"content_bytes": outer, "filename": "delivery.zip"}]
    )

    assert batch.to_dict()["status"] == "COMPLETE"
    assert len(batch.documents) == 1
    child = batch.documents[0]
    assert child["filename"] == "attachments/nested.zip!/rules/approval.md"
    assert child["archive_provenance"]["archive_depth"] == 2
    assert child["archive_provenance"]["virtual_member_path"] == child["filename"]
    assert len(child["archive_provenance"]["archive_chain"]) == 2


def test_zip_compression_ratio_limit_blocks_package_without_partial_activation() -> None:
    package = _zip_bytes([("huge.txt", b"A" * 200_000)])
    policy = ArchiveExpansionPolicy(max_compression_ratio=2.0)

    batch = expand_document_envelopes(
        [{"content_bytes": package, "filename": "bomb.zip"}],
        policy=policy,
    )

    assert batch.documents == []
    assert any(
        row["code"] == "ARCHIVE_MEMBER_COMPRESSION_RATIO_EXCEEDED"
        for row in batch.errors
    )


def test_zip_symlink_member_is_forbidden() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")

    batch = expand_document_envelopes(
        [{"content_bytes": buffer.getvalue(), "filename": "links.zip"}]
    )

    assert batch.documents == []
    assert any(row["code"] == "ARCHIVE_LINK_MEMBER_FORBIDDEN" for row in batch.errors)


def test_tar_link_member_is_forbidden() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        safe = tarfile.TarInfo("safe.txt")
        safe_data = b"safe"
        safe.size = len(safe_data)
        archive.addfile(safe, io.BytesIO(safe_data))
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "safe.txt"
        archive.addfile(link)

    batch = expand_document_envelopes(
        [{"content_bytes": buffer.getvalue(), "filename": "links.tar"}]
    )

    assert batch.documents == []
    assert any(row["code"] == "ARCHIVE_NON_REGULAR_MEMBER_FORBIDDEN" for row in batch.errors)


def test_gzip_single_member_expands_without_writing_archive_controlled_path() -> None:
    package = gzip.compress(b"service error line")

    batch = expand_document_envelopes(
        [{"content_bytes": package, "filename": "application.log.gz"}]
    )

    assert batch.to_dict()["status"] == "COMPLETE"
    assert len(batch.documents) == 1
    assert batch.documents[0]["filename"] == "application.log"
    assert batch.documents[0]["content_bytes"] == b"service error line"


def test_rar_and_7z_follow_runtime_provider_contract() -> None:
    for filename in ("materials.rar", "materials.7z"):
        batch = expand_document_envelopes(
            [{"content_bytes": b"opaque", "filename": filename}]
        )
        assert batch.documents == []
        assert batch.packages[0]["status"] == "BLOCKED"
        codes = {row["code"] for row in batch.errors}
        if shutil.which("bsdtar"):
            assert "ARCHIVE_RUNTIME_DEPENDENCY_UNAVAILABLE" not in codes
        else:
            assert "ARCHIVE_RUNTIME_DEPENDENCY_UNAVAILABLE" in codes


def test_archive_with_only_system_junk_is_not_reported_as_success() -> None:
    package = _zip_bytes([("__MACOSX/._requirements", b"metadata")])

    batch = expand_document_envelopes(
        [{"content_bytes": package, "filename": "junk.zip"}]
    )

    assert batch.documents == []
    assert batch.packages[0]["status"] == "BLOCKED"
    assert any(row["code"] == "ARCHIVE_NO_IMPORTABLE_MEMBERS" for row in batch.errors)


def test_public_archive_ingestion_module_is_only_a_core_facade() -> None:
    # The facade owns transport classification but shares the single atomic
    # parser/transaction authority with core (no duplicate archive parser).
    assert archive_ingestion._core is archive_ingestion_core
    assert (
        archive_ingestion.ingest_enterprise_knowledge_archives
        is archive_ingestion_core.ingest_enterprise_knowledge_archives
    )
