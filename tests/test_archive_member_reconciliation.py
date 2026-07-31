from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

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


def _parsed(blob: bytes, filename: str, source_type: str, source_id: str) -> dict:
    value = blob.decode("utf-8")
    projection = f"# {filename}\n\n{value}"
    document_ir = {
        "format": Path(filename).suffix.lstrip(".") or "text",
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


def _chunks(**kwargs) -> tuple[dict, None]:
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


def test_removed_member_retires_from_active_corpus_but_keeps_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(_crud, "parse_enterprise_source", _parsed)
    monkeypatch.setattr(_crud, "_register_chunks", _chunks)

    first = ingest_enterprise_knowledge_documents(
        "archive_reconcile",
        [
            {
                "content_bytes": _zip_bytes(
                    [
                        ("requirements/current.md", b"current rule v1"),
                        ("requirements/obsolete.md", b"obsolete rule"),
                    ]
                ),
                "filename": "requirements.zip",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )
    assert first["ok"] is True
    obsolete = next(
        row for row in first["created"] if row["original_name"].endswith("obsolete.md")
    )
    obsolete_stored_path = tmp_path / obsolete["stored_path"]
    assert obsolete_stored_path.is_file()

    second = ingest_enterprise_knowledge_documents(
        "archive_reconcile",
        [
            {
                "content_bytes": _zip_bytes(
                    [("requirements/current.md", b"current rule v2")]
                ),
                "filename": "requirements.zip",
            }
        ],
        root=tmp_path,
        actor={"name": "qa", "role": "admin"},
    )

    assert second["ok"] is True
    reconciliation = second["source_occurrence_reconciliations"][0]
    assert reconciliation["status"] == "PASS"
    assert len(reconciliation["retired_source_occurrence_ids"]) == 1
    active = list_enterprise_knowledge_sources(
        "archive_reconcile", root=tmp_path
    )["sources"]
    assert len(active) == 1
    assert active[0]["original_name"].endswith("current.md")
    history = list_enterprise_knowledge_sources(
        "archive_reconcile", root=tmp_path, include_deleted=True
    )["sources"]
    retired = next(row for row in history if row["original_name"].endswith("obsolete.md"))
    assert retired["status"] == "retired_archive_member"
    assert retired["superseded_reason"] == "archive_source_occurrence_removed"
    assert obsolete_stored_path.is_file()
