from __future__ import annotations

import hashlib
import io
import zipfile

from ai_test_asset_center import enterprise_source_registry
from ai_test_asset_center.enterprise_knowledge_center import (
    ingest_enterprise_knowledge_documents,
    list_enterprise_knowledge_sources,
)
from ai_test_asset_center.enterprise_knowledge_center import _crud


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return buffer.getvalue()


def _success(blob: bytes, filename: str, source_type: str, source_id: str) -> dict:
    value = blob.decode("utf-8")
    projection = f"# {filename}\n\n{value}"
    document_ir = {
        "format": filename.rsplit(".", 1)[-1],
        "blocks": [
            {
                "block_id": f"block:{source_id}",
                "type": "PARAGRAPH",
                "order": 1,
                "text": value,
                "source_locator": f"{filename}#line=1;chars=0-{max(0, len(value) - 1)}",
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
        "text_hash": hashlib.sha256(projection.encode()).hexdigest(),
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


def _failed(blob: bytes, filename: str, source_type: str, source_id: str) -> dict:
    error = {
        "stage": "parse",
        "code": "SYNTHETIC_FORMAL_PARSE_FAILURE",
        "detail": "member formal parse failed",
        "severity": "P0",
        "blocks_formal_understanding": True,
    }
    result = _success(blob, filename, source_type, source_id)
    result.update(
        {
            "text": "",
            "parse_status": "failed",
            "parser": "none",
            "text_hash": "",
            "text_length": 0,
            "parse_errors": [error],
            "document_ir_status": "BLOCKED",
            "document_ir": {},
            "document_structure": {},
        }
    )
    result["parser_receipt"] = {
        "receipt_id": f"receipt:{source_id}",
        "parser": "none",
        "parser_status": "failed",
        "fidelity": "blocked",
        "errors": [error],
        "evidence_closure_receipt": {"exact_address_rate": 0.0},
    }
    return result


def _chunk_receipt(**kwargs) -> tuple[dict, None]:
    manifest = kwargs["runtime_manifest"]
    return (
        {
            "schema": "qualibug.document-ir-chunk-index-receipt.v1",
            "source_id": kwargs["source_id"],
            "source_hash": kwargs["content_hash"],
            "chunk_count": 1,
            "exact_address_rate": 1.0,
            "status": "REGISTERED",
            "runtime_source_id": manifest["source_id"],
            "runtime_source_hash": manifest["source_hash"],
            "raw_binary_utf8_decode_used": False,
            "silent_failure_allowed": False,
        },
        None,
    )


def test_one_failed_member_rolls_back_all_new_archive_members(monkeypatch, tmp_path) -> None:
    def parse(blob, filename, source_type, source_id):
        return _failed(blob, filename, source_type, source_id) if "bad.md" in filename else _success(
            blob, filename, source_type, source_id
        )

    monkeypatch.setattr(_crud, "parse_enterprise_source", parse)
    monkeypatch.setattr(_crud, "_register_chunks", _chunk_receipt)
    package = _zip_bytes(
        [
            ("requirements/good.md", b"valid requirement"),
            ("requirements/bad.md", b"invalid requirement"),
        ]
    )

    result = ingest_enterprise_knowledge_documents(
        "archive_member_atomic",
        [{"content_bytes": package, "filename": "requirements.zip"}],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert result["ok"] is False
    assert result["created"] == []
    assert result["rolled_back_archives"][0]["status"] == "COMPLETE"
    assert result["rolled_back_archives"][0]["archive_members_active_after_rollback"] is False
    assert list_enterprise_knowledge_sources(
        "archive_member_atomic", root=tmp_path
    )["sources"] == []
    assert enterprise_source_registry.list_source_assets(
        "archive_member_atomic", root=tmp_path
    ) == []


def test_failed_archive_update_restores_every_previous_member(monkeypatch, tmp_path) -> None:
    mode = {"fail_bad_v2": False}

    def parse(blob, filename, source_type, source_id):
        if mode["fail_bad_v2"] and "bad.md" in filename and b"version two" in blob:
            return _failed(blob, filename, source_type, source_id)
        return _success(blob, filename, source_type, source_id)

    monkeypatch.setattr(_crud, "parse_enterprise_source", parse)
    monkeypatch.setattr(_crud, "_register_chunks", _chunk_receipt)

    first = ingest_enterprise_knowledge_documents(
        "archive_update_atomic",
        [
            {
                "content_bytes": _zip_bytes(
                    [
                        ("requirements/good.md", b"good version one"),
                        ("requirements/bad.md", b"bad version one"),
                    ]
                ),
                "filename": "requirements.zip",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )
    assert first["ok"] is True
    first_active = {
        row["logical_key"]: row
        for row in list_enterprise_knowledge_sources(
            "archive_update_atomic", root=tmp_path
        )["sources"]
    }
    first_assets = {
        row["source_id"]: row["latest_source_hash"]
        for row in enterprise_source_registry.list_source_assets(
            "archive_update_atomic", root=tmp_path
        )
    }

    mode["fail_bad_v2"] = True
    second = ingest_enterprise_knowledge_documents(
        "archive_update_atomic",
        [
            {
                "content_bytes": _zip_bytes(
                    [
                        ("requirements/good.md", b"good version two"),
                        ("requirements/bad.md", b"bad version two"),
                    ]
                ),
                "filename": "requirements.zip",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert second["ok"] is False
    restored = {
        row["logical_key"]: row
        for row in list_enterprise_knowledge_sources(
            "archive_update_atomic", root=tmp_path
        )["sources"]
    }
    assert set(restored) == set(first_active)
    assert {
        key: row["source_id"] for key, row in restored.items()
    } == {
        key: row["source_id"] for key, row in first_active.items()
    }
    restored_assets = {
        row["source_id"]: row["latest_source_hash"]
        for row in enterprise_source_registry.list_source_assets(
            "archive_update_atomic", root=tmp_path
        )
    }
    assert restored_assets == first_assets
