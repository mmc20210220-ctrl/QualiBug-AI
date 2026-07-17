from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from ai_test_asset_center.enterprise_pilot_runtime import _validate_ref
from ai_test_asset_center import private_pilot_service


class _JsonCaptureHandler:
    _require_known_project = private_pilot_service.PrivatePilotHandler._require_known_project

    def __init__(self) -> None:
        self.status_code: int | None = None
        self.payload: dict | None = None

    def _json(self, body, status: int = 200) -> None:
        self.status_code = status
        self.payload = body


def _ensure_known_project(root: Path, project: str) -> None:
    input_dir = root / "platform_inputs" / project
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "real_project_config.json").write_text('{"project_id":"%s"}' % project, encoding="utf-8")


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
        return {"ok": True, "created": [{"source_id": "src_created"}], "duplicates": [], "errors": []}

    _ensure_known_project(tmp_path, project)
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
    assert handler.payload["source_id"] == "src_created"
    assert handler.payload["storage_mode"] == "verbatim_bytes"
    assert ".docx" in handler.payload["supported_extensions"]
    assert "postman" in handler.payload["supported_source_types"]
    assert captured["project_id"] == project
    assert captured["documents"] == [{"file_path": str(stored_path), "filename": "spec.pdf", "source_type": "prd"}]


def test_handle_ingest_rejects_invalid_base64_payload(tmp_path: Path) -> None:
    handler = _JsonCaptureHandler()
    _ensure_known_project(tmp_path, "demo")

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


def test_handle_ingest_rejects_unknown_project_before_writing(tmp_path: Path) -> None:
    handler = _JsonCaptureHandler()

    private_pilot_service.PrivatePilotHandler._handle_ingest(
        handler,
        "missing_project",
        {"filename": "spec.md", "content": base64.b64encode(b"# PRD").decode("ascii")},
        tmp_path,
        {"name": "tester", "role": "project_owner"},
    )

    assert handler.status_code == 404
    assert handler.payload == {
        "ok": False,
        "error": "PROJECT_NOT_FOUND",
        "message": "项目 'missing_project' 不存在，请先选择有效项目。",
    }
    assert not (tmp_path / "platform_workspace" / "missing_project").exists()


def test_handle_ingest_returns_failure_when_knowledge_center_rejects_upload(monkeypatch, tmp_path: Path) -> None:
    project = "demo"
    _ensure_known_project(tmp_path, project)

    def fake_ingest_document(path: str) -> dict[str, object]:
        return {"ok": True, "path": path}

    def fake_knowledge_ingest(project_id: str, documents, root: Path, actor):
        return {"ok": False, "created": [], "duplicates": [], "errors": [{"error": "parser exploded"}]}

    monkeypatch.setattr("ai_test_asset_center.document_change_watcher.ingest_document", fake_ingest_document)
    monkeypatch.setattr("ai_test_asset_center.enterprise_knowledge_center.ingest_enterprise_knowledge_documents", fake_knowledge_ingest)

    handler = _JsonCaptureHandler()
    private_pilot_service.PrivatePilotHandler._handle_ingest(
        handler,
        project,
        {"filename": "spec.md", "content": base64.b64encode(b"# PRD").decode("ascii")},
        tmp_path,
        {"name": "tester", "role": "project_owner"},
    )

    assert handler.status_code == 500
    assert handler.payload == {
        "ok": False,
        "error": "INGEST_FAILED",
        "message": "资料导入失败：parser exploded",
    }
    assert list((tmp_path / "platform_workspace" / project / "input").glob("*")) == []


def test_handle_ingest_does_not_report_success_when_knowledge_center_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = "demo"
    _ensure_known_project(tmp_path, project)
    monkeypatch.setattr(
        "ai_test_asset_center.document_change_watcher.ingest_document",
        lambda path: {"ok": True, "path": path},
    )

    def fail_knowledge_ingest(*args, **kwargs):
        raise RuntimeError("knowledge index unavailable")

    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.ingest_enterprise_knowledge_documents",
        fail_knowledge_ingest,
    )
    handler = _JsonCaptureHandler()

    with pytest.raises(RuntimeError, match="knowledge index unavailable"):
        private_pilot_service.PrivatePilotHandler._handle_ingest(
            handler,
            project,
            {"filename": "spec.md", "content": base64.b64encode(b"# PRD").decode("ascii")},
            tmp_path,
            {"name": "tester", "role": "project_owner"},
        )

    assert handler.payload is None
    receipt = json.loads(
        (tmp_path / "platform_outputs" / project / "knowledge_ingest_last_error.json").read_text(encoding="utf-8")
    )
    assert receipt["phase"] == "knowledge_center"
    assert receipt["knowledge_updated"] is False
    assert receipt["error"] == "knowledge index unavailable"


def test_handle_ingest_stops_when_document_intelligence_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = "demo"
    _ensure_known_project(tmp_path, project)
    monkeypatch.setattr(
        "ai_test_asset_center.document_change_watcher.ingest_document",
        lambda path: {"ok": False, "error": "document parser failed", "path": path},
    )

    def reject_knowledge_ingest(*args, **kwargs):
        raise AssertionError("knowledge center must not receive a failed document")

    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.ingest_enterprise_knowledge_documents",
        reject_knowledge_ingest,
    )
    handler = _JsonCaptureHandler()

    private_pilot_service.PrivatePilotHandler._handle_ingest(
        handler,
        project,
        {"filename": "spec.md", "content": base64.b64encode(b"# PRD").decode("ascii")},
        tmp_path,
        {"name": "tester", "role": "project_owner"},
    )

    assert handler.status_code == 500
    assert handler.payload == {
        "ok": False,
        "error": "DOCUMENT_INGEST_FAILED",
        "message": "document parser failed",
    }
    assert list((tmp_path / "platform_workspace" / project / "input").glob("*")) == []


def test_handle_ingest_rejects_malformed_knowledge_center_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = "demo"
    _ensure_known_project(tmp_path, project)
    monkeypatch.setattr(
        "ai_test_asset_center.document_change_watcher.ingest_document",
        lambda path: {"ok": True, "path": path},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.ingest_enterprise_knowledge_documents",
        lambda *args, **kwargs: {"created": []},
    )
    handler = _JsonCaptureHandler()

    with pytest.raises(ValueError, match="missing ok=true"):
        private_pilot_service.PrivatePilotHandler._handle_ingest(
            handler,
            project,
            {"filename": "spec.md", "content": base64.b64encode(b"# PRD").decode("ascii")},
            tmp_path,
            {"name": "tester", "role": "project_owner"},
        )

    receipt = json.loads(
        (tmp_path / "platform_outputs" / project / "knowledge_ingest_last_error.json").read_text(encoding="utf-8")
    )
    assert receipt["phase"] == "knowledge_center"
    assert receipt["error_type"] == "ValueError"


def test_handle_ingest_does_not_hide_source_registry_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = "demo"
    _ensure_known_project(tmp_path, project)
    monkeypatch.setattr(
        "ai_test_asset_center.document_change_watcher.ingest_document",
        lambda path: {"ok": True, "path": path},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.ingest_enterprise_knowledge_documents",
        lambda *args, **kwargs: {
            "ok": True,
            "created": [{"source_id": "source-1"}],
            "duplicates": [],
        },
    )

    def fail_manifest(*args, **kwargs):
        raise RuntimeError("source registry unavailable")

    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_source_registry.resolve_source_manifest",
        fail_manifest,
    )
    handler = _JsonCaptureHandler()

    with pytest.raises(RuntimeError, match="source registry unavailable"):
        private_pilot_service.PrivatePilotHandler._handle_ingest(
            handler,
            project,
            {"filename": "spec.md", "content": base64.b64encode(b"# PRD").decode("ascii")},
            tmp_path,
            {"name": "tester", "role": "project_owner"},
        )

    assert handler.payload is None
    receipt = json.loads(
        (tmp_path / "platform_outputs" / project / "knowledge_ingest_last_error.json").read_text(encoding="utf-8")
    )
    assert receipt["phase"] == "source_registry"
    assert receipt["knowledge_updated"] is True
    assert receipt["source_id"] == "source-1"


def test_invalid_api_document_skips_auto_scan_instead_of_launching_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = "demo"
    _ensure_known_project(tmp_path, project)
    monkeypatch.setattr(
        "ai_test_asset_center.document_change_watcher.ingest_document",
        lambda path: {"ok": True, "path": path},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_knowledge_center.ingest_enterprise_knowledge_documents",
        lambda *args, **kwargs: {
            "ok": True,
            "created": [{"source_id": "source-1"}],
            "duplicates": [],
        },
    )
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_source_registry.resolve_source_manifest",
        lambda *args, **kwargs: {"source_id": "source-1", "source_hash": "a" * 64},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.universal_api_parser.parse_to_openapi",
        lambda path: (_ for _ in ()).throw(ValueError("invalid OpenAPI document")),
    )

    def reject_thread(*args, **kwargs):
        raise AssertionError("auto scan thread must not start for an invalid API document")

    monkeypatch.setattr("threading.Thread", reject_thread)
    handler = _JsonCaptureHandler()

    private_pilot_service.PrivatePilotHandler._handle_ingest(
        handler,
        project,
        {
            "type": "openapi",
            "filename": "openapi.yaml",
            "content": base64.b64encode(b"not: valid: openapi").decode("ascii"),
        },
        tmp_path,
        {"name": "tester", "role": "project_owner"},
    )

    assert handler.status_code == 200
    assert handler.payload is not None
    assert handler.payload["auto_scan"] == "skipped"
    assert handler.payload["ingest_status"] == "created_scan_skipped"
    assert "invalid OpenAPI document" in handler.payload["auto_scan_reason"]


def test_ingest_auto_scan_failure_emits_receipt_and_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ai_test_asset_center import __main__ as main_module

    monkeypatch.setattr(
        main_module,
        "scan",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scan provider unavailable")),
    )

    with pytest.raises(RuntimeError, match="scan provider unavailable"):
        private_pilot_service._run_ingest_auto_scan(
            root=tmp_path,
            project="demo",
            body={},
            raw=b"# PRD",
            doc_type="prd",
            source_manifest={"source_id": "source-1", "source_hash": "a" * 64},
        )

    receipt_path = tmp_path / "platform_outputs" / "demo" / "auto_scan_last_error.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == "qualibug.auto-scan-failure.v1"
    assert receipt["phase"] == "scan"
    assert receipt["error_type"] == "RuntimeError"
    assert receipt["error"] == "scan provider unavailable"


def test_knowledge_asset_sources_align_with_ingested_inventory(tmp_path: Path) -> None:
    stored = tmp_path / "platform_workspace" / "demo" / "enterprise_knowledge_center" / "sources" / "src_1_v1_prd.md"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_text("# PRD\n", encoding="utf-8")

    asset = {
        "source_inventory": [
            {
                "source_id": "src_1",
                "original_name": "PRD.md",
                "source_type": "prd",
                "status": "active",
                "stored_path": "platform_workspace/demo/enterprise_knowledge_center/sources/src_1_v1_prd.md",
                "created_at_utc": "2026-07-01T10:00:00Z",
                "version": 1,
                "parse": {"parse_status": "parsed"},
            }
        ]
    }

    rows = private_pilot_service._knowledge_asset_sources(asset, tmp_path)

    assert len(rows) == 1
    assert rows[0]["source_id"] == "src_1"
    assert rows[0]["filename"] == "PRD.md"
    assert rows[0]["source_type"] == "prd"
    assert rows[0]["status"] == "active"
    assert rows[0]["size_bytes"] == stored.stat().st_size
    assert rows[0]["uploaded_at"] == "2026-07-01T10:00:00Z"
    assert rows[0]["version"] == 1
    assert rows[0]["parse_status"] == "parsed"


def test_project_import_output_dir_stays_inside_platform_outputs(tmp_path: Path) -> None:
    project_id, output_dir = private_pilot_service._project_output_dir_for_import(tmp_path, r"..\outside/customer")

    assert project_id == "..outsidecustomer"
    assert (tmp_path / "platform_outputs").resolve() in output_dir.parents
    assert output_dir.name == project_id


def test_project_list_scope_filter_limits_public_project_listing(monkeypatch) -> None:
    monkeypatch.setenv("QUALIBUG_ALLOW_PUBLIC_BIND", "1")
    handler = SimpleNamespace(
        headers={private_pilot_service.PROJECT_SCOPE_HEADER: "customer_a; customer_b"},
        server=SimpleNamespace(server_address=("0.0.0.0", 8088)),
    )

    scopes, wildcard = private_pilot_service.PrivatePilotHandler._project_list_scope_filter(handler)

    assert wildcard is False
    assert scopes == {"customer_a", "customer_b"}


def test_connector_credential_ref_rejects_embedded_dsn_secret() -> None:
    assert _validate_ref("secret_ref:SHOP_DATABASE", "credential_ref") == "secret_ref:SHOP_DATABASE"

    with pytest.raises(ValueError):
        _validate_ref("env://postgresql://qa:plain-password@db.internal:5432/app", "credential_ref")


def test_ingest_contract_and_ui_support_claims_match_real_capabilities() -> None:
    spec = (Path(__file__).resolve().parents[1] / "openapi_qualibug.yaml").read_text(encoding="utf-8")

    assert ".docx" in private_pilot_service.ONBOARD_DOCUMENT_EXTENSIONS
    assert ".csv" in private_pilot_service.KNOWLEDGE_INGEST_EXTENSIONS
    assert "permission_matrix" in private_pilot_service.KNOWLEDGE_INGEST_SOURCE_TYPES
    assert "verbatim" in spec
    assert "database_schema" in spec
    assert ".docx" in spec
    assert "ingest_status" in spec
    assert "source_id" in spec
    assert "PROJECT_NOT_FOUND" in spec


def test_frontend_materials_flow_uses_real_asset_api_and_supported_doc_types() -> None:
    root = Path(__file__).resolve().parents[1]
    client = (root / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    materials = (root / "frontend" / "src" / "pages" / "EnterpriseMaterials.tsx").read_text(encoding="utf-8")

    assert "/knowledge/asset?project=" in client
    assert "parseApiErrorMessage" in client
    assert "collaboration_document" in materials
    assert "business_doc" not in materials
    assert "normalizeUploadError" in materials


def test_private_pilot_frontend_aliases_match_current_vite_routes() -> None:
    assert private_pilot_service._normalize_frontend_page_path("/knowledge") == "/materials"
    assert private_pilot_service._normalize_frontend_page_path("/benchmark") == "/coverage"
    assert private_pilot_service._normalize_frontend_page_path("/onboard") == "/products"
    assert private_pilot_service._normalize_frontend_page_path("/dashboard") == "/dashboard"
    assert private_pilot_service._normalize_frontend_page_path("/evidence") == "/evidence"


def test_load_scan_history_marks_legacy_compatibility(tmp_path: Path) -> None:
    project = "demo"
    report_dir = tmp_path / "platform_outputs" / project / "pipeline_reports"
    report_dir.mkdir(parents=True)
    (report_dir / "latest_pipeline_report.json").write_text('{"stage2_discovery": {"findings": []}}', encoding="utf-8")

    class _Handler:
        _load_scan_history = private_pilot_service.PrivatePilotHandler._load_scan_history

    result = _Handler()._load_scan_history(project, tmp_path)
    assert result["ok"] is True
    assert result["compatibility_mode"] == "legacy_findings_report_v1"
    assert result["canonical_api_family"] == "/api/v1/projects/{projectId}/*"
