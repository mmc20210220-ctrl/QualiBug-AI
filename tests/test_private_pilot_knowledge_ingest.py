from __future__ import annotations

import base64
from pathlib import Path

from ai_test_asset_center import private_pilot_service


class _JsonCaptureHandler:
    def __init__(self) -> None:
        self.status_code: int | None = None
        self.payload: dict | None = None

    def _json(self, body, status: int = 200) -> None:
        self.status_code = status
        self.payload = body


def test_handle_ingest_persists_binary_upload_bytes_verbatim(monkeypatch, tmp_path: Path) -> None:
    project = "demo"
    actor = {"name": "tester", "role": "project_owner"}
    raw = b"%PDF-1.7\n\x00\xffbinary-payload\n%%EOF"
    body = {
        "type": "prd",
        "filename": r"..\nested\spec.pdf",
        "content": base64.b64encode(raw).decode("ascii"),
    }
    captured: dict[str, object] = {}

    def fake_ingest_document(path: str) -> dict[str, object]:
        captured["ingest_path"] = path
        return {"ok": True, "format": ".pdf"}

    def fake_knowledge_ingest(project_id: str, documents, root: Path, actor):
        captured["project_id"] = project_id
        captured["documents"] = documents

    monkeypatch.setattr("ai_test_asset_center.document_change_watcher.ingest_document", fake_ingest_document)
    monkeypatch.setattr("ai_test_asset_center.enterprise_knowledge_center.ingest_enterprise_knowledge_documents", fake_knowledge_ingest)

    handler = _JsonCaptureHandler()
    private_pilot_service.PrivatePilotHandler._handle_ingest(handler, project, body, tmp_path, actor)

    assert handler.status_code == 200
    assert handler.payload is not None
    stored_path = Path(str(handler.payload["path"]))
    assert stored_path.name == "spec.pdf"
    assert stored_path.read_bytes() == raw
    assert Path(str(captured["ingest_path"])) == stored_path
    assert handler.payload["storage_mode"] == "verbatim_bytes"
    assert ".docx" in handler.payload["supported_extensions"]
    assert "postman" in handler.payload["supported_source_types"]
    assert captured["project_id"] == project
    assert captured["documents"] == [{"file_path": str(stored_path), "filename": "spec.pdf", "source_type": "prd"}]


def test_handle_ingest_rejects_invalid_base64_payload(tmp_path: Path) -> None:
    handler = _JsonCaptureHandler()

    private_pilot_service.PrivatePilotHandler._handle_ingest(
        handler,
        "demo",
        {"filename": "broken.pdf", "content": "not-base64%%%"},
        tmp_path,
        {"name": "tester", "role": "project_owner"},
    )

    assert handler.status_code == 400
    assert handler.payload == {
        "ok": False,
        "error": "DECODE_FAILED",
        "message": "Base64 解码失败，请检查文件内容。",
    }


def test_ingest_contract_and_ui_support_claims_match_real_capabilities() -> None:
    spec = (Path(__file__).resolve().parents[1] / "openapi_qualibug.yaml").read_text(encoding="utf-8")

    assert ".docx" in private_pilot_service.ONBOARD_DOCUMENT_EXTENSIONS
    assert ".csv" in private_pilot_service.KNOWLEDGE_INGEST_EXTENSIONS
    assert "permission_matrix" in private_pilot_service.KNOWLEDGE_INGEST_SOURCE_TYPES
    assert "verbatim" in spec
    assert "database_schema" in spec
    assert ".docx" in spec
