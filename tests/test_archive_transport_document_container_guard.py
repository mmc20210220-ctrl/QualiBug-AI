from __future__ import annotations

import io
import zipfile

import pytest

from ai_test_asset_center.enterprise_knowledge_center.archive_expansion import (
    expand_document_envelopes,
)
from ai_test_asset_center.enterprise_knowledge_center.archive_ingestion import (
    expand_archive_documents,
)


def _zip_members(*members: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for member in members:
            archive.writestr(member, b"fixture")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("requirements.docx", _zip_members("[Content_Types].xml", "word/document.xml")),
        ("rules.xlsx", _zip_members("[Content_Types].xml", "xl/workbook.xml")),
        ("rules.xlsb", _zip_members("[Content_Types].xml", "xl/workbook.bin")),
        ("prototype.pptx", _zip_members("[Content_Types].xml", "ppt/presentation.xml")),
        ("requirements.odt", _zip_members("content.xml", "META-INF/manifest.xml")),
        ("requirements.wps", _zip_members("document.xml", "manifest.xml")),
        ("rules.et", _zip_members("workbook.xml", "manifest.xml")),
        ("prototype.dps", _zip_members("presentation.xml", "manifest.xml")),
    ],
)
def test_declared_zip_based_documents_are_not_expanded_as_archives(
    filename: str,
    data: bytes,
) -> None:
    envelope = {"content_bytes": data, "filename": filename}

    canonical = expand_archive_documents([envelope])
    compatibility = expand_document_envelopes([envelope])

    assert canonical.errors == []
    assert canonical.receipts == []
    assert canonical.transport_artifacts == []
    assert canonical.documents == [envelope]
    assert compatibility.errors == []
    assert compatibility.packages == []
    assert compatibility.documents == [envelope]


@pytest.mark.parametrize(
    ("data", "expected_family"),
    [
        (_zip_members("[Content_Types].xml", "word/document.xml"), "word"),
        (_zip_members("[Content_Types].xml", "xl/workbook.xml"), "spreadsheet"),
        (_zip_members("[Content_Types].xml", "ppt/presentation.xml"), "presentation"),
        (_zip_members("content.xml", "META-INF/manifest.xml"), "odf"),
    ],
)
def test_extensionless_structural_document_containers_are_not_expanded(
    data: bytes,
    expected_family: str,
) -> None:
    envelope = {"content_bytes": data, "filename": "uploaded-material"}

    result = expand_archive_documents([envelope])

    assert result.documents == [envelope], expected_family
    assert result.receipts == []
    assert result.errors == []


def test_real_zip_transport_still_expands_members() -> None:
    package = _zip_members("requirements/order.md", "bugs/history.csv")

    result = expand_archive_documents(
        [{"content_bytes": package, "filename": "enterprise-materials.zip"}]
    )

    assert result.errors == []
    assert result.receipts
    assert result.transport_artifacts
    assert {row["filename"] for row in result.documents} == {
        "enterprise-materials.zip!/requirements/order.md",
        "enterprise-materials.zip!/bugs/history.csv",
    }
