from __future__ import annotations

import io
import zipfile

import pytest

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.compatible_office_adapter import (
    CompatibleOfficeDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    CAP_COMMENT_EXTRACTION,
    CAP_FONT_EVIDENCE,
    CAP_FORMULA_EXTRACTION,
    CAP_HEADER_FOOTER,
    CAP_HEADING_HIERARCHY,
    CAP_IMAGE_PRESENCE,
    CAP_LIST_HIERARCHY,
    CAP_STYLE_SEMANTICS,
    CAP_TABLE_STRUCTURE,
    CAP_TEXT_EXTRACTION,
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.native_office_policy_adapters import (
    apply_native_office_container_policy,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.page_render_registry import (
    build_default_page_renderer_registry,
)


class AvailableNormalizer:
    name = "available-test-normalizer"
    version = "1"

    def available(self) -> bool:
        return True

    def normalize(self, source: DocumentSource, target_suffix: str):  # pragma: no cover
        raise AssertionError("capability tests must not execute normalization")


@pytest.mark.parametrize(
    ("filename", "expected", "forbidden"),
    [
        (
            "需求.wps",
            {
                CAP_TEXT_EXTRACTION,
                CAP_HEADING_HIERARCHY,
                CAP_LIST_HIERARCHY,
                CAP_TABLE_STRUCTURE,
                CAP_HEADER_FOOTER,
                CAP_FONT_EVIDENCE,
            },
            {CAP_FORMULA_EXTRACTION, CAP_COMMENT_EXTRACTION, CAP_STYLE_SEMANTICS},
        ),
        (
            "规则.et",
            {
                CAP_TEXT_EXTRACTION,
                CAP_TABLE_STRUCTURE,
                CAP_FORMULA_EXTRACTION,
                CAP_COMMENT_EXTRACTION,
                CAP_STYLE_SEMANTICS,
            },
            {CAP_HEADER_FOOTER, CAP_FONT_EVIDENCE, CAP_IMAGE_PRESENCE},
        ),
        (
            "原型.dps",
            {
                CAP_TEXT_EXTRACTION,
                CAP_HEADING_HIERARCHY,
                CAP_TABLE_STRUCTURE,
                CAP_IMAGE_PRESENCE,
                CAP_COMMENT_EXTRACTION,
                CAP_STYLE_SEMANTICS,
            },
            {CAP_FORMULA_EXTRACTION, CAP_HEADER_FOOTER, CAP_FONT_EVIDENCE},
        ),
    ],
)
def test_compatible_office_match_declares_only_family_capabilities(
    filename: str,
    expected: set[str],
    forbidden: set[str],
) -> None:
    adapter = CompatibleOfficeDocumentAdapter(AvailableNormalizer())
    source = DocumentSource("src", filename, b"legacy")

    match = adapter.probe(source)

    assert match is not None
    assert set(match.capabilities) == expected
    assert not (set(match.capabilities) & forbidden)
    receipt = adapter.receipt(source, match)
    assert set(receipt["capabilities"]) == expected
    assert receipt["capability_scope"] == "source_family_specific"


def _zip_with_member(member: str | None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        if member:
            archive.writestr(member, b"opaque-vba")
    return buffer.getvalue()


def _empty_ir() -> dict:
    return {
        "schema": "qualibug.document-ir.v1",
        "format": "docx",
        "filename": "source.docx",
        "plain_text": "",
        "blocks": [],
        "sections": [],
        "tables": [],
        "pages": [],
        "unsupported_content": [],
        "structure_receipt": {"status": "COMPLETE", "unsupported_content": []},
    }


@pytest.mark.parametrize(
    ("filename", "member", "macro_suffixes", "reason"),
    [
        ("需求.docx", "word/vbaProject.bin", {".docm", ".dotm"}, "WORD_MACRO_CODE_NOT_PARSED"),
        ("规则.xlsx", "xl/vbaProject.bin", {".xlsm", ".xltm"}, "SPREADSHEET_MACRO_CODE_NOT_PARSED"),
        ("原型.pptx", "ppt/vbaProject.bin", {".pptm", ".potm", ".ppsm"}, "PRESENTATION_MACRO_CODE_NOT_PARSED"),
    ],
)
def test_embedded_vba_is_detected_even_when_extension_is_non_macro(
    filename: str,
    member: str,
    macro_suffixes: set[str],
    reason: str,
) -> None:
    source = DocumentSource("src", filename, _zip_with_member(member))

    result = apply_native_office_container_policy(
        _empty_ir(),
        source,
        macro_member=member,
        macro_suffixes=macro_suffixes,
        macro_reason_code=reason,
    )

    assert result["structure_receipt"]["status"] == "BLOCKED"
    gap = next(row for row in result["unsupported_content"] if row["reason_code"] == reason)
    assert gap["macro_member_present"] is True
    assert gap["macro_suffix_declared"] is False
    assert gap["blocks_formal_understanding"] is True


@pytest.mark.parametrize(
    ("filename", "member", "macro_suffixes", "reason"),
    [
        ("需求.docm", "word/vbaProject.bin", {".docm", ".dotm"}, "WORD_MACRO_CODE_NOT_PARSED"),
        ("规则.xlsm", "xl/vbaProject.bin", {".xlsm", ".xltm"}, "SPREADSHEET_MACRO_CODE_NOT_PARSED"),
        ("原型.pptm", "ppt/vbaProject.bin", {".pptm", ".potm", ".ppsm"}, "PRESENTATION_MACRO_CODE_NOT_PARSED"),
    ],
)
def test_macro_extension_blocks_even_when_vba_member_is_missing(
    filename: str,
    member: str,
    macro_suffixes: set[str],
    reason: str,
) -> None:
    source = DocumentSource("src", filename, _zip_with_member(None))

    result = apply_native_office_container_policy(
        _empty_ir(),
        source,
        macro_member=member,
        macro_suffixes=macro_suffixes,
        macro_reason_code=reason,
    )

    gap = next(row for row in result["unsupported_content"] if row["reason_code"] == reason)
    assert result["format"] == filename.rsplit(".", 1)[1]
    assert result["structure_receipt"]["status"] == "BLOCKED"
    assert gap["macro_member_present"] is False
    assert gap["macro_suffix_declared"] is True


def test_non_macro_template_subtype_is_preserved_without_false_macro_gap() -> None:
    source = DocumentSource("src", "模板.dotx", _zip_with_member(None))

    result = apply_native_office_container_policy(
        _empty_ir(),
        source,
        macro_member="word/vbaProject.bin",
        macro_suffixes={".docm", ".dotm"},
        macro_reason_code="WORD_MACRO_CODE_NOT_PARSED",
    )

    assert result["format"] == "dotx"
    assert result["structure_receipt"]["ooxml_container_subtype"] == "dotx"
    assert result["structure_receipt"]["status"] == "COMPLETE"
    assert result["unsupported_content"] == []


def test_default_visual_registry_contains_compatible_office_renderer() -> None:
    names = [renderer.name for renderer in build_default_page_renderer_registry().all()]

    assert "libreoffice-compatible-office-page-renderer" in names
