from __future__ import annotations

from typing import Any

import pytest

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    AdapterMatch,
    DocumentAdapter,
    DocumentAdapterRegistry,
    DocumentSource,
    SupplementalContext,
    build_default_registry,
    build_document_structure_ir,
    plan_document_parsing,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    CAP_OCR,
    CAP_PAGE_RENDERING,
    CAP_TEXT_EXTRACTION,
    MODE_PRIMARY,
    MODE_SUPPLEMENTAL,
    text,
)


def _ir(text: str, *, adapter_name: str, block_type: str = "PARAGRAPH") -> dict[str, Any]:
    return {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "fake",
        "filename": "sample.fake",
        "plain_text": text,
        "blocks": [
            {
                "block_id": f"block:{adapter_name}",
                "type": block_type,
                "parent_id": "",
                "order": 1,
                "region": "body",
                "text": text,
                "source_locator": "sample.fake#line=1",
            }
        ],
        "sections": [],
        "tables": [],
        "unsupported_content": [],
        "structure_receipt": {
            "schema": "qualibug.document-structure-receipt.v1",
            "status": "COMPLETE",
            "format": "fake",
            "block_count": 1,
            "source_traceability_rate": 1.0,
            "block_type_distribution": {block_type: 1},
            "section_count": 0,
            "unsupported_content_count": 0,
            "unsupported_content": [],
            "document_order_is_business_flow": False,
            "filename_is_business_context": False,
        },
    }


class _PrimaryAdapter(DocumentAdapter):
    name = "fake-primary"
    parser_version = "1"
    priority = 100
    mode = MODE_PRIMARY
    capabilities = frozenset({CAP_TEXT_EXTRACTION})

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(self.name, 100, "fake-primary-match", tuple(self.capabilities), self.mode)

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        return _ir("订单不得删除", adapter_name=self.name)


class _GapExposingPrimaryAdapter(_PrimaryAdapter):
    """Primary whose parsed IR exposes a concrete scanned-page gap."""

    name = "fake-gap-primary"

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        ir = _ir("订单不得删除", adapter_name=self.name)
        ir["unsupported_content"] = [
            {
                "kind": "SCANNED_PAGE_REQUIRES_OCR",
                "reason_code": "SCANNED_PAGE_REQUIRES_OCR",
                "count": 1,
                "pages": [1],
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
            }
        ]
        return ir


class _DeferredSupplementalAdapter(DocumentAdapter):
    """Deferred supplemental selected only after a primary exposes a structural gap."""

    name = "fake-deferred-supplemental"
    parser_version = "1"
    priority = 80
    mode = MODE_SUPPLEMENTAL
    capabilities = frozenset({CAP_OCR, CAP_PAGE_RENDERING})

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return None

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        raise AssertionError("deferred adapter must not run as primary")

    def probe_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> AdapterMatch | None:
        if any(
            text(row.get("reason_code") or row.get("kind")) == "SCANNED_PAGE_REQUIRES_OCR"
            for row in context.trigger_gaps
        ):
            return AdapterMatch(
                self.name,
                90,
                "fake-supplemental-match",
                tuple(sorted(self.capabilities)),
                self.mode,
            )
        return None

    def extract_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> dict[str, Any]:
        return _ir("合同不得删除", adapter_name=self.name, block_type="HEADING")


def test_content_signature_selects_pdf_adapter_even_with_unknown_suffix() -> None:
    source = DocumentSource(
        source_id="pdf-signature",
        filename="enterprise-material.bin",
        data=b"%PDF-1.7\nnot-a-complete-pdf",
    )
    plan = plan_document_parsing(source, build_default_registry())
    assert plan["detected_family"] == "pdf"
    assert plan["detection_method"] == "pdf_file_signature"
    assert plan["selected_adapters"][0]["adapter_name"] == "pdf-native-layout"


def test_unknown_binary_is_fail_visible_and_blocked() -> None:
    ir = build_document_structure_ir(
        b"\x00\xff\x00\xfe\x00\x01\x02\x03" * 20,
        filename="proprietary.abc",
        source_id="unknown-1",
    )
    assert ir["structure_receipt"]["status"] == "BLOCKED"
    assert ir["parsing_plan"]["status"] == "BLOCKED_UNSUPPORTED_SOURCE"
    assert ir["blocks"][0]["type"] == "UNKNOWN_BLOCK"
    assert any(
        row.get("kind") == "UNSUPPORTED_SOURCE_FORMAT"
        and row.get("blocks_formal_understanding") is True
        for row in ir["unsupported_content"]
    )


def test_generic_text_fallback_builds_structure_without_format_specific_branch() -> None:
    ir = build_document_structure_ir(
        "# 订单\n\n其不得发货。\n\n1）创建订单".encode("utf-8"),
        filename="enterprise-material.customtext",
        source_id="text-1",
    )
    assert ir["parsing_plan"]["selected_adapters"][0]["adapter_name"] == "generic-text-structure"
    assert ir["structure_receipt"]["status"] in {"COMPLETE", "PARTIAL"}
    blocks = ir["blocks"]
    heading = next(row for row in blocks if row.get("type") == "HEADING")
    paragraph = next(row for row in blocks if row.get("type") == "PARAGRAPH")
    list_item = next(row for row in blocks if row.get("type") == "LIST_ITEM")
    assert heading["text"] == "订单"
    assert paragraph["parent_id"] == heading["block_id"]
    assert list_item["parent_id"] == heading["block_id"]
    assert ir["ingestion_pipeline_receipt"]["business_semantics_added"] is False


def test_supplemental_is_not_run_eagerly_beside_primary_without_gap() -> None:
    registry = DocumentAdapterRegistry([_PrimaryAdapter(), _DeferredSupplementalAdapter()])
    ir = build_document_structure_ir(
        b"fake",
        filename="sample.fake",
        source_id="no-gap-1",
        registry=registry,
    )
    assert ir["adapter_merge_receipt"]["adapter_count"] == 1
    assert ir["adapter_merge_receipt"]["block_conflict_count"] == 0
    assert ir["ingestion_pipeline_receipt"]["deferred_plan_status"] == "NOT_REQUIRED"


def test_multi_adapter_conflict_is_preserved_and_blocks_formal_understanding() -> None:
    registry = DocumentAdapterRegistry(
        [_GapExposingPrimaryAdapter(), _DeferredSupplementalAdapter()]
    )
    ir = build_document_structure_ir(
        b"fake",
        filename="sample.fake",
        source_id="conflict-1",
        registry=registry,
    )
    assert ir["adapter_merge_receipt"]["adapter_count"] == 2
    assert ir["adapter_merge_receipt"]["block_conflict_count"] == 1
    assert ir["structure_receipt"]["status"] == "BLOCKED"
    assert any(
        row.get("kind") == "DOCUMENT_ADAPTER_BLOCK_CONFLICT"
        and row.get("blocks_formal_understanding") is True
        for row in ir["unsupported_content"]
    )
    conflicting = [
        row for row in ir["blocks"] if row.get("source_locator") == "sample.fake#line=1"
    ]
    assert len(conflicting) == 2


def test_registry_rejects_duplicate_adapter_names() -> None:
    registry = DocumentAdapterRegistry([_PrimaryAdapter()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_PrimaryAdapter())


def test_enterprise_understanding_mainline_uses_adapter_pipeline(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center.enterprise_knowledge_center import _crud
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.integration import (
        _parsed_sources_for_context,
    )

    relative = "platform_workspace/project/enterprise_knowledge_center/sources/source.custom"
    stored = tmp_path / relative
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_text("# 订单\n其不得发货。", encoding="utf-8")

    monkeypatch.setattr(
        _crud,
        "_record_parse",
        lambda _record, _root: {
            "text": "# 订单\n其不得发货。",
            "document_structure": {},
            "parser_receipt": {"source_locator": "source.custom"},
        },
    )
    asset = {
        "source_inventory": [
            {
                "source_id": "source-custom",
                "status": "active",
                "stored_path": relative,
                "original_name": "source.custom",
                "source_type": "other_document",
            }
        ]
    }
    parsed = _parsed_sources_for_context(asset, tmp_path)
    assert len(parsed) == 1
    structure = parsed[0]["document_structure"]
    assert structure["parsing_plan"]["selected_adapters"][0]["adapter_name"] == "generic-text-structure"
    assert structure["ingestion_pipeline_receipt"]["executed_adapter_count"] == 1
    assert not parsed[0]["document_structure_error"]
