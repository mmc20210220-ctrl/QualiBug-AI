from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center import source_ingestion
from ai_test_asset_center.enterprise_knowledge_center.source_ingestion import (
    build_document_ir_retrieval_chunks,
    parse_enterprise_source,
    project_document_ir_for_semantic_extraction,
)


def _spreadsheet_ir() -> dict:
    blocks = [
        {
            "block_id": "sheet-heading",
            "type": "HEADING",
            "order": 1,
            "level": 1,
            "text": "字段定义",
            "source_locator": "schema.xlsx#sheet=字段定义",
        },
        {
            "block_id": "cell-a1",
            "type": "TABLE_CELL",
            "order": 2,
            "text": "字段",
            "source_locator": "schema.xlsx#sheet=字段定义;cell=A1",
            "sheet": "字段定义",
            "cell_ref": "A1",
            "row_index": 1,
            "column_index": 1,
            "evidence_address": {"address_kind": "SPREADSHEET_CELL"},
        },
        {
            "block_id": "cell-b1",
            "type": "TABLE_CELL",
            "order": 3,
            "text": "类型",
            "source_locator": "schema.xlsx#sheet=字段定义;cell=B1",
            "sheet": "字段定义",
            "cell_ref": "B1",
            "row_index": 1,
            "column_index": 2,
            "evidence_address": {"address_kind": "SPREADSHEET_CELL"},
        },
        {
            "block_id": "cell-a2",
            "type": "TABLE_CELL",
            "order": 4,
            "text": "order_id",
            "source_locator": "schema.xlsx#sheet=字段定义;cell=A2",
            "sheet": "字段定义",
            "cell_ref": "A2",
            "row_index": 2,
            "column_index": 1,
            "evidence_address": {"address_kind": "SPREADSHEET_CELL"},
        },
        {
            "block_id": "cell-b2",
            "type": "TABLE_CELL",
            "order": 5,
            "text": "bigint",
            "source_locator": "schema.xlsx#sheet=字段定义;cell=B2",
            "sheet": "字段定义",
            "cell_ref": "B2",
            "row_index": 2,
            "column_index": 2,
            "evidence_address": {"address_kind": "SPREADSHEET_CELL"},
        },
    ]
    return {
        "schema": "qualibug.document-ir.v1",
        "format": "xlsx",
        "blocks": blocks,
        "tables": [
            {
                "block_id": "table-fields",
                "cell_block_ids": [
                    "cell-a1",
                    "cell-b1",
                    "cell-a2",
                    "cell-b2",
                ],
            }
        ],
        "structure_receipt": {"status": "COMPLETE"},
        "evidence_closure_receipt": {
            "status": "COMPLETE",
            "exact_address_rate": 1.0,
        },
        "ingestion_pipeline_receipt": {"final_status": "COMPLETE"},
        "unsupported_content": [],
    }


def test_spreadsheet_projection_reuses_exact_document_ir_cells() -> None:
    projection, receipt = project_document_ir_for_semantic_extraction(
        _spreadsheet_ir(),
        filename="schema.xlsx",
    )

    assert "# 字段定义" in projection
    assert "| 字段 | 类型 |" in projection
    assert "| order_id | bigint |" in projection
    assert receipt["projected_table_count"] == 1
    assert receipt["tables"][0]["header_row_candidate"] == 1
    assert receipt["tables"][0]["header_semantics_confirmed"] is False
    assert receipt["business_semantics_added"] is False


def test_retrieval_chunks_preserve_exact_cell_evidence() -> None:
    parsed = {
        "document_ir": _spreadsheet_ir(),
        "text": "字段 order_id bigint",
        "tables": [{"name": "orders"}],
        "field_dictionary": [{"table": "orders", "field": "order_id"}],
        "operations": [],
        "roles": [],
        "state_machines": [],
    }
    source_hash = "a" * 64

    chunks, receipt = build_document_ir_retrieval_chunks(
        parsed,
        source_id="src_orders",
        source_hash=source_hash,
        source_version=3,
    )

    order_id = next(chunk for chunk in chunks if chunk["content"] == "order_id")
    assert order_id["source_hash"] == source_hash
    assert order_id["source_version"] == "3"
    assert order_id["block_id"] == "cell-a2"
    assert order_id["source_locator"].endswith("cell=A2")
    assert order_id["evidence_address"]["address_kind"] == "SPREADSHEET_CELL"
    assert "order_id" in order_id["entities"]
    assert order_id["confidence"] == 1.0
    assert receipt["raw_binary_utf8_decode_used"] is False
    assert receipt["silent_failure_allowed"] is False


def test_binary_source_is_projected_before_legacy_semantic_extraction(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        source_ingestion,
        "build_document_structure_ir",
        lambda *args, **kwargs: _spreadsheet_ir(),
    )

    def fake_parse(blob: bytes, filename: str, source_type: str, source_id: str) -> dict:
        captured["blob"] = blob
        captured["filename"] = filename
        return {
            "text": blob.decode("utf-8"),
            "payload": None,
            "openapi": {},
            "operations": [],
            "tables": [],
            "field_dictionary": [],
            "ui_specs": [],
            "permissions": [],
            "tickets": [],
            "har_errors": [],
            "log_errors": [],
            "rules": [],
            "roles": [],
            "state_machines": [],
            "parse_status": "parsed",
            "parser": "md",
            "text_hash": "",
            "text_length": len(blob),
            "parse_errors": [],
            "parser_receipt": {"parser": "md", "parser_status": "parsed"},
        }

    monkeypatch.setattr(source_ingestion, "_parse_source", fake_parse)

    result = parse_enterprise_source(
        b"\x00\xffPK-not-utf8-workbook",
        "requirements.xlsx",
        "prd",
        "src_prd",
    )

    projected = bytes(captured["blob"]).decode("utf-8")
    assert captured["filename"] == "requirements.md"
    assert "order_id" in projected
    assert "\ufffd" not in projected
    assert result["document_ir_status"] == "COMPLETE"
    assert result["parser_receipt"]["parser"].startswith("document_ir+")


def test_crud_does_not_decode_raw_binary_for_chunking() -> None:
    crud = (
        Path(__file__).parents[1]
        / "ai_test_asset_center"
        / "enterprise_knowledge_center"
        / "_crud.py"
    ).read_text(encoding="utf-8")
    source_ingestion = (
        Path(__file__).parents[1]
        / "ai_test_asset_center"
        / "enterprise_knowledge_center"
        / "source_ingestion.py"
    ).read_text(encoding="utf-8")

    assert 'blob.decode("utf-8"' not in crud
    assert "from .document_intelligence" not in crud
    assert "from .enterprise_source_registry" not in crud
    assert "build_document_ir_retrieval_chunks" in crud
    # The chunking receipt now lives with the Document IR bridge in source_ingestion,
    # not in the CRUD transaction module.
    assert '"raw_binary_utf8_decode_used": False' in source_ingestion


def _crud_parsed_result(*, failed: bool = False) -> dict:
    status = "BLOCKED" if failed else "COMPLETE"
    parse_status = "failed" if failed else "parsed"
    errors = (
        [
            {
                "stage": "document_structure",
                "code": "DOCUMENT_STRUCTURE_BLOCKED",
                "detail": "synthetic blocked source",
            }
        ]
        if failed
        else []
    )
    document_ir = {
        "format": "docx",
        "blocks": [
            {
                "block_id": "paragraph-1",
                "type": "PARAGRAPH",
                "order": 1,
                "text": "订单审批规则",
                "source_locator": "requirements.docx#paragraph=1",
                "evidence_address": {"address_kind": "EXACT_SOURCE_LOCATOR"},
            }
        ],
        "tables": [],
    }
    return {
        "text": "订单审批规则",
        "payload": None,
        "openapi": {},
        "operations": [],
        "tables": [],
        "field_dictionary": [],
        "ui_specs": [],
        "permissions": [],
        "tickets": [],
        "har_errors": [],
        "log_errors": [],
        "rules": [],
        "roles": [],
        "state_machines": [],
        "parse_status": parse_status,
        "parser": "document_ir+md",
        "text_hash": "b" * 64,
        "text_length": 6,
        "parse_errors": errors,
        "document_ir_status": status,
        "document_ir": document_ir,
        "document_structure": document_ir,
        "semantic_projection_receipt": {"projected_table_count": 0},
        "parser_receipt": {
            "parser": "document_ir+md",
            "parser_status": parse_status,
            "fidelity": "blocked" if failed else "full",
            "evidence_closure_receipt": {"exact_address_rate": 1.0},
            "errors": errors,
        },
    }


def test_failed_new_version_does_not_supersede_active_source(
    monkeypatch,
    tmp_path,
) -> None:
    from ai_test_asset_center.enterprise_knowledge_center import _crud

    monkeypatch.setattr(
        _crud,
        "_register_chunks",
        lambda **kwargs: (
            {
                "schema": "qualibug.document-ir-chunk-index-receipt.v1",
                "source_id": kwargs["source_id"],
                "source_hash": kwargs["content_hash"],
                "chunk_count": 1,
                "exact_address_rate": 1.0,
                "status": "REGISTERED",
                "raw_binary_utf8_decode_used": False,
                "silent_failure_allowed": False,
            },
            None,
        ),
    )
    monkeypatch.setattr(
        _crud,
        "parse_enterprise_source",
        lambda *args, **kwargs: _crud_parsed_result(failed=False),
    )
    first = _crud.ingest_enterprise_knowledge_documents(
        "transaction_project",
        [
            {
                "text": "first valid requirements",
                "filename": "requirements.docx",
                "source_type": "prd",
            }
        ],
        root=tmp_path,
        actor={"name": "tester", "role": "admin"},
    )
    first_id = first["created"][0]["source_id"]
    assert first["ok"] is True
    assert first["created"][0]["status"] == "active"

    monkeypatch.setattr(
        _crud,
        "parse_enterprise_source",
        lambda *args, **kwargs: _crud_parsed_result(failed=True),
    )
    second = _crud.ingest_enterprise_knowledge_documents(
        "transaction_project",
        [
            {
                "text": "second broken requirements",
                "filename": "requirements.docx",
                "source_type": "prd",
            }
        ],
        root=tmp_path,
        actor={"name": "tester", "role": "admin"},
    )

    inventory = _crud.list_enterprise_knowledge_sources(
        "transaction_project",
        root=tmp_path,
        include_deleted=True,
    )
    active = [row for row in inventory["sources"] if row.get("status") == "active"]
    failed = [row for row in inventory["sources"] if row.get("status") == "failed"]

    assert second["ok"] is False
    assert second["errors"][0]["code"] == "SOURCE_FORMAL_PARSE_BLOCKED"
    assert [row["source_id"] for row in active] == [first_id]
    # Atomic batch transaction: a failed version blocks the batch before any
    # record is registered, so no "failed" source record is created.
    assert len(failed) == 0
    assert inventory["summary"]["failed_source_count"] == 0
