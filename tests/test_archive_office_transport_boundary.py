from __future__ import annotations

import io
import zipfile

from ai_test_asset_center.enterprise_knowledge_center.archive_ingestion import (
    expand_archive_documents,
)


def _minimal_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>订单审批需求</w:t></w:r></w:p></w:body>
</w:document>""",
        )
    return buffer.getvalue()


def _outer_zip(member_name: str, member_bytes: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, member_bytes)
    return buffer.getvalue()


def test_top_level_docx_is_document_not_archive_transport() -> None:
    docx = _minimal_docx_bytes()

    result = expand_archive_documents(
        [{"content_bytes": docx, "filename": "订单需求.docx"}]
    )

    assert result.errors == []
    assert result.receipts == []
    assert result.transport_artifacts == []
    assert len(result.documents) == 1
    assert result.documents[0]["filename"] == "订单需求.docx"
    assert result.documents[0]["content_bytes"] == docx


def test_docx_inside_archive_remains_one_document_member_at_nested_boundary() -> None:
    docx = _minimal_docx_bytes()
    package = _outer_zip("需求/订单需求.docx", docx)

    result = expand_archive_documents(
        [{"content_bytes": package, "filename": "项目资料.zip"}]
    )

    assert result.errors == []
    assert len(result.receipts) == 1
    assert result.receipts[0]["nested_archive_count"] == 0
    assert len(result.documents) == 1
    member = result.documents[0]
    assert member["filename"] == "项目资料.zip!/需求/订单需求.docx"
    assert member["content_bytes"] == docx
    assert "[Content_Types].xml" not in member["filename"]
    assert "word/document.xml" not in member["filename"]
    assert len(member["archive_provenance"]["chain"]) == 1


def test_renamed_ooxml_member_is_detected_structurally_not_recursively_expanded() -> None:
    docx = _minimal_docx_bytes()
    package = _outer_zip("附件/requirements.bin", docx)

    result = expand_archive_documents(
        [{"content_bytes": package, "filename": "mixed.zip"}]
    )

    assert result.errors == []
    assert len(result.documents) == 1
    assert result.documents[0]["filename"] == "mixed.zip!/附件/requirements.bin"
    assert result.documents[0]["content_bytes"] == docx
    assert len(result.receipts) == 1
