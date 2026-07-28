from __future__ import annotations

import io

import pytest

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)


def test_docx_container_signature_routes_through_registered_adapter() -> None:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("订单管理", level=1)
    document.add_paragraph("订单支付成功后才能发货。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "状态"
    table.cell(0, 1).text = "动作"
    table.cell(1, 0).text = "待支付"
    table.cell(1, 1).text = "付款"
    buffer = io.BytesIO()
    document.save(buffer)

    ir = build_document_structure_ir(
        buffer.getvalue(),
        filename="enterprise-material.bin",
        source_id="docx-signature",
    )

    assert ir["parsing_plan"]["detected_family"] == "docx"
    assert ir["parsing_plan"]["selected_adapters"][0]["adapter_name"] == "docx-native-structure"
    assert ir["adapter_receipts"][0]["adapter_name"] == "docx-native-structure"
    assert ir["structure_receipt"]["status"] in {"COMPLETE", "PARTIAL"}
    assert any(row.get("type") == "HEADING" and row.get("text") == "订单管理" for row in ir["blocks"])
    assert any(row.get("type") == "TABLE" for row in ir["blocks"])
    assert ir["ingestion_pipeline_receipt"]["business_semantics_added"] is False
