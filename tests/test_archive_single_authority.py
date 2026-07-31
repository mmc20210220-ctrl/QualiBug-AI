from __future__ import annotations

import inspect

from ai_test_asset_center.enterprise_knowledge_center import archive_expansion
from ai_test_asset_center.enterprise_knowledge_center.archive_ingestion_core import (
    ArchiveExpansion,
)


def test_archive_expansion_facade_delegates_to_one_canonical_core(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_expand(documents, *, limits):
        calls.append({"documents": list(documents), "limits": limits})
        return ArchiveExpansion(
            documents=[
                {
                    "content_bytes": b"# requirement",
                    "filename": "bundle.zip!/需求.md",
                    "tags": ["archive_member"],
                    "external_ref": "archive://root!/需求.md",
                    "archive_provenance": {
                        "root_archive_filename": "bundle.zip",
                        "root_archive_hash": "a" * 64,
                        "member_hash": "b" * 64,
                        "chain": [
                            {
                                "archive_filename": "bundle.zip",
                                "archive_hash": "a" * 64,
                                "member_path": "需求.md",
                                "member_hash": "b" * 64,
                                "depth": 1,
                            }
                        ],
                    },
                }
            ],
            receipts=[
                {
                    "status": "COMPLETE",
                    "archive_filename": "bundle.zip",
                    "archive_hash": "a" * 64,
                    "archive_byte_count": 120,
                    "depth": 1,
                    "provider_name": "fake-provider",
                    "member_count": 1,
                    "expanded_document_count": 1,
                    "ignored_member_count": 0,
                    "errors": [],
                }
            ],
        )

    monkeypatch.setattr(archive_expansion, "expand_archive_documents", fake_expand)

    batch = archive_expansion.expand_document_envelopes(
        [{"content_bytes": b"PK fixture", "filename": "bundle.zip"}]
    )

    assert len(calls) == 1
    assert batch.documents[0]["filename"] == "需求.md"
    provenance = batch.documents[0]["archive_provenance"]
    assert provenance["top_level_archive_name"] == "bundle.zip"
    assert provenance["virtual_member_path"] == "需求.md"
    receipt = batch.to_dict()
    assert receipt["canonical_archive_authority"] == "archive_ingestion_core"
    assert receipt["duplicate_archive_parser_present"] is False


def test_archive_expansion_facade_contains_no_archive_parser_implementation() -> None:
    source = inspect.getsource(archive_expansion)

    assert "import zipfile" not in source
    assert "import tarfile" not in source
    assert "import gzip" not in source
    assert "ZipFile(" not in source
    assert "tarfile.open(" not in source
    assert "GzipFile(" not in source
