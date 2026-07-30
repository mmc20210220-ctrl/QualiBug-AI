from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from ai_test_asset_center import enterprise_source_registry
from ai_test_asset_center.enterprise_knowledge_center import _crud


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _parsed_projection(blob: bytes, filename: str, source_type: str, source_id: str) -> dict:
    source_text = blob.decode("utf-8")
    projection = f"# {filename}\n\n{source_text}"
    document_ir = {
        "format": Path(filename).suffix.lstrip(".") or "text",
        "blocks": [
            {
                "block_id": f"block:{source_id}",
                "type": "PARAGRAPH",
                "order": 1,
                "text": source_text,
                "source_locator": f"{filename}#line=1;chars=0-{max(0, len(source_text) - 1)}",
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
        "parser": "document_ir+text",
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
            "parser": "document_ir+text",
            "parser_status": "parsed",
            "fidelity": "full",
            "errors": [],
            "evidence_closure_receipt": {"exact_address_rate": 1.0},
        },
    }


def _registered_chunk_receipt(**kwargs) -> tuple[dict, None]:
    runtime_manifest = kwargs["runtime_manifest"]
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


def _install_success_path(monkeypatch) -> None:
    monkeypatch.setattr(_crud, "parse_enterprise_source", _parsed_projection)
    monkeypatch.setattr(_crud, "_register_chunks", _registered_chunk_receipt)


def test_archive_members_are_registered_as_independent_runtime_sources(monkeypatch, tmp_path) -> None:
    _install_success_path(monkeypatch)
    package = _zip_bytes(
        [
            ("requirements/order.md", "# 产品需求\n订单超过五万元需要财务审批".encode()),
            ("bugs/history.csv", "id,title\nBUG-1,重复支付".encode()),
        ]
    )

    result = _crud.ingest_enterprise_knowledge_documents(
        "archive_runtime",
        [{"content_bytes": package, "filename": "ERP资料包.zip", "tags": ["pilot"]}],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert result["ok"] is True
    assert result["archive_expansion"]["status"] == "COMPLETE"
    assert result["archive_expansion"]["document_count"] == 2
    assert len(result["created"]) == 2
    assert all(row["status"] == "active" for row in result["created"])
    assert all(row["archive_provenance"] for row in result["created"])
    assert all(row["runtime_source_manifest"]["status"] == "REGISTERED" for row in result["created"])
    assert {row["original_name"] for row in result["created"]} == {
        "requirements/order.md",
        "bugs/history.csv",
    }
    package_path = tmp_path / result["archive_expansion"]["packages"][0]["stored_path"]
    assert package_path.is_file()
    assets = enterprise_source_registry.list_source_assets(
        "archive_runtime",
        root=tmp_path,
    )
    assert len(assets) == 2
    assert {row["source_id"] for row in assets} == {
        row["runtime_asset_id"] for row in result["created"]
    }


def test_archive_security_failure_activates_no_partial_members(monkeypatch, tmp_path) -> None:
    _install_success_path(monkeypatch)
    package = _zip_bytes(
        [
            ("safe.md", b"safe"),
            ("../escape.md", b"unsafe"),
        ]
    )

    result = _crud.ingest_enterprise_knowledge_documents(
        "archive_blocked",
        [{"content_bytes": package, "filename": "malicious.zip"}],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert result["ok"] is False
    assert result["created"] == []
    assert result["source_count"] == 0
    assert result["archive_expansion"]["status"] == "BLOCKED"
    assert any(row["code"] == "ARCHIVE_MEMBER_PATH_TRAVERSAL" for row in result["errors"])
    assert enterprise_source_registry.list_source_assets(
        "archive_blocked",
        root=tmp_path,
    ) == []


def test_same_package_and_member_path_form_one_version_line(monkeypatch, tmp_path) -> None:
    _install_success_path(monkeypatch)

    first = _crud.ingest_enterprise_knowledge_documents(
        "archive_versions",
        [
            {
                "content_bytes": _zip_bytes([("rules/approval.md", b"version one")]),
                "filename": "requirements.zip",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )
    second = _crud.ingest_enterprise_knowledge_documents(
        "archive_versions",
        [
            {
                "content_bytes": _zip_bytes([("rules/approval.md", b"version two")]),
                "filename": "requirements.zip",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    first_record = first["created"][0]
    second_record = second["created"][0]
    assert first_record["logical_key"] == second_record["logical_key"]
    assert first_record["runtime_asset_id"] == second_record["runtime_asset_id"]
    assert first_record["version"] == 1
    assert second_record["version"] == 2
    inventory = _crud.list_enterprise_knowledge_sources(
        "archive_versions",
        root=tmp_path,
        include_deleted=True,
    )
    rows = [row for row in inventory["sources"] if row["logical_key"] == first_record["logical_key"]]
    assert {row["status"] for row in rows} == {"active", "superseded"}
    active = next(row for row in rows if row["status"] == "active")
    assert active["source_id"] == second_record["source_id"]
