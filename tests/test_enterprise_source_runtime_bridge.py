from __future__ import annotations

import hashlib

from ai_test_asset_center import enterprise_source_registry
from ai_test_asset_center.enterprise_knowledge_center import _crud


def _parsed_projection(blob: bytes, filename: str, source_type: str, source_id: str) -> dict:
    source_text = blob.decode("utf-8")
    projection = f"# {filename}\n\n{source_text}"
    document_ir = {
        "format": "docx",
        "blocks": [
            {
                "block_id": f"block:{source_id}",
                "type": "PARAGRAPH",
                "order": 1,
                "text": source_text,
                "source_locator": f"{filename}#paragraph=1",
                "evidence_address": {"address_kind": "EXACT_SOURCE_LOCATOR"},
            }
        ],
        "tables": [],
    }
    return {
        "text": projection,
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
        "parser": "document_ir+md",
        "text_hash": hashlib.sha256(projection.encode("utf-8")).hexdigest(),
        "text_length": len(projection),
        "parse_errors": [],
        "document_ir_status": "COMPLETE",
        "document_ir": document_ir,
        "document_structure": document_ir,
        "semantic_projection_receipt": {
            "schema": "qualibug.semantic-source-projection.v1",
            "projected_table_count": 0,
        },
        "parser_receipt": {
            "receipt_id": f"receipt:{source_id}",
            "parser": "document_ir+md",
            "parser_status": "parsed",
            "fidelity": "full",
            "errors": [],
            "evidence_closure_receipt": {"exact_address_rate": 1.0},
        },
    }


def _registered_chunk_receipt(**kwargs) -> tuple[dict, None]:
    runtime_manifest = kwargs["runtime_manifest"]
    assert runtime_manifest["status"] == "REGISTERED"
    return (
        {
            "schema": "qualibug.document-ir-chunk-index-receipt.v1",
            "source_id": kwargs["source_id"],
            "source_hash": kwargs["content_hash"],
            "chunk_count": 1,
            "exact_address_rate": 1.0,
            "status": "REGISTERED",
            "runtime_source_id": runtime_manifest["source_id"],
            "runtime_source_hash": runtime_manifest["source_hash"],
            "raw_binary_utf8_decode_used": False,
            "silent_failure_allowed": False,
        },
        None,
    )


def test_upload_registers_document_ir_projection_in_runtime_corpus(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(_crud, "parse_enterprise_source", _parsed_projection)
    monkeypatch.setattr(_crud, "_register_chunks", _registered_chunk_receipt)

    result = _crud.ingest_enterprise_knowledge_documents(
        "runtime_bridge",
        [
            {
                "text": "订单金额超过五万元需要财务审批",
                "filename": "订单审批需求.docx",
                "source_type": "prd",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert result["ok"] is True
    record = result["created"][0]
    manifest = record["runtime_source_manifest"]
    assert record["status"] == "active"
    assert manifest["status"] == "REGISTERED"
    assert manifest["source_id"] == record["runtime_asset_id"]
    assert manifest["source_id"].startswith("knowledge_")
    runtime_text = enterprise_source_registry.load_source_content(
        "runtime_bridge",
        manifest["source_hash"],
        root=tmp_path,
    )
    assert runtime_text == "# 订单审批需求.docx\n\n订单金额超过五万元需要财务审批"
    assert runtime_text != "订单金额超过五万元需要财务审批"
    assets = enterprise_source_registry.list_source_assets(
        "runtime_bridge",
        root=tmp_path,
    )
    assert [row["source_id"] for row in assets] == [manifest["source_id"]]


def test_failed_outer_transaction_restores_previous_runtime_version(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(_crud, "parse_enterprise_source", _parsed_projection)
    monkeypatch.setattr(_crud, "_register_chunks", _registered_chunk_receipt)
    first = _crud.ingest_enterprise_knowledge_documents(
        "runtime_rollback",
        [
            {
                "text": "第一版审批规则",
                "filename": "审批规则.docx",
                "source_type": "prd",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )
    first_record = first["created"][0]
    first_manifest = first_record["runtime_source_manifest"]

    def fail_after_runtime_registration(**kwargs):
        raise RuntimeError("synthetic chunk transaction failure")

    monkeypatch.setattr(_crud, "_register_chunks", fail_after_runtime_registration)
    second = _crud.ingest_enterprise_knowledge_documents(
        "runtime_rollback",
        [
            {
                "text": "第二版错误规则",
                "filename": "审批规则.docx",
                "source_type": "prd",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert second["ok"] is False
    inventory = _crud.list_enterprise_knowledge_sources(
        "runtime_rollback",
        root=tmp_path,
        include_deleted=True,
    )
    active = [row for row in inventory["sources"] if row.get("status") == "active"]
    assert [row["source_id"] for row in active] == [first_record["source_id"]]
    assets = enterprise_source_registry.list_source_assets(
        "runtime_rollback",
        root=tmp_path,
    )
    assert len(assets) == 1
    assert assets[0]["source_id"] == first_manifest["source_id"]
    assert assets[0]["latest_source_hash"] == first_manifest["source_hash"]
    restored_text = enterprise_source_registry.load_source_content(
        "runtime_rollback",
        first_manifest["source_hash"],
        root=tmp_path,
    )
    assert "第一版审批规则" in restored_text


def test_delete_deactivates_runtime_asset_but_retains_immutable_blob(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(_crud, "parse_enterprise_source", _parsed_projection)
    monkeypatch.setattr(_crud, "_register_chunks", _registered_chunk_receipt)
    created = _crud.ingest_enterprise_knowledge_documents(
        "runtime_delete",
        [
            {
                "text": "库存锁定规则",
                "filename": "库存规则.docx",
                "source_type": "prd",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )["created"][0]
    manifest = created["runtime_source_manifest"]

    deleted = _crud.delete_enterprise_knowledge_source(
        "runtime_delete",
        created["source_id"],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert deleted["ok"] is True
    assert deleted["runtime_source_deactivation"]["deactivated"] is True
    assert enterprise_source_registry.list_source_assets(
        "runtime_delete",
        root=tmp_path,
    ) == []
    retained = enterprise_source_registry.load_source_content(
        "runtime_delete",
        manifest["source_hash"],
        root=tmp_path,
    )
    assert "库存锁定规则" in retained
