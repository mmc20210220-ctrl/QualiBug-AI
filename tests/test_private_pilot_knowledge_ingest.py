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
    _upload_bytes = private_pilot_service.PrivatePilotHandler._upload_bytes

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


def _composed_manifest(*args, **kwargs) -> dict[str, object]:
    return {"source_id": "src_composed", "source_hash": "a" * 64, "composed_from": [], "part_count": 1}


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
        captured["staged_bytes"] = Path(path).read_bytes()
        return {"ok": True, "format": ".pdf"}

    def fake_knowledge_ingest(project_id: str, documents, root: Path, actor):
        captured["project_id"] = project_id
        captured["documents"] = documents
        return {
            "ok": True,
            "created": [{"source_id": "src_created", "stored_path": "platform_workspace/demo/sources/spec.pdf"}],
            "duplicates": [],
            "errors": [],
        }

    _ensure_known_project(tmp_path, project)
    monkeypatch.setattr("ai_test_asset_center.document_change_watcher.ingest_document", fake_ingest_document)
    monkeypatch.setattr("ai_test_asset_center.enterprise_knowledge_center.ingest_enterprise_knowledge_documents", fake_knowledge_ingest)
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_source_registry.compose_project_source_manifest",
        _composed_manifest,
    )

    handler = _JsonCaptureHandler()
    private_pilot_service.PrivatePilotHandler._handle_ingest(handler, project, body, tmp_path, actor)

    assert handler.status_code == 200
    assert handler.payload is not None
    assert handler.payload["source_id"] == "src_created"
    assert handler.payload["path"] == "platform_workspace/demo/sources/spec.pdf"
    assert handler.payload["storage_mode"] == "canonical_immutable_source"
    assert captured["staged_bytes"] == raw
    assert ".docx" in handler.payload["supported_extensions"]
    assert "postman" in handler.payload["supported_source_types"]
    assert captured["project_id"] == project
    assert captured["documents"] == [
        {"file_path": captured["ingest_path"], "filename": "spec.pdf", "source_type": "prd"}
    ]


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
    assert handler.payload["ok"] is False
    assert handler.payload["error"] == "DECODE_FAILED"
    assert handler.payload["message"] == "Base64 解码失败，请检查文件内容。"


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
    assert handler.payload["ok"] is False
    assert handler.payload["error"] == "INGEST_FAILED"
    assert handler.payload["message"] == "资料导入失败：parser exploded"
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
    assert receipt["phase"] == "canonical_ingest"
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

    with pytest.raises(ValueError, match="knowledge ingest result ok must be a boolean"):
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
    assert receipt["phase"] == "canonical_ingest"
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
        "ai_test_asset_center.enterprise_source_registry.compose_project_source_manifest",
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
    assert receipt["phase"] == "canonical_ingest"
    assert receipt["knowledge_updated"] is False
    assert receipt["source_id"] == ""


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
        "ai_test_asset_center.enterprise_source_registry.compose_project_source_manifest",
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
    project_id, output_dir = private_pilot_service._project_output_dir_for_import(tmp_path, "customer_a")

    assert project_id == "customer_a"
    assert (tmp_path / "platform_outputs").resolve() in output_dir.parents
    assert output_dir.name == project_id

    # Path-unsafe project ids must fail closed, never be silently rewritten into a
    # path segment that could escape the platform_outputs root.
    with pytest.raises(ValueError):
        private_pilot_service._project_output_dir_for_import(tmp_path, r"..\outside/customer")


def test_project_list_scope_filter_limits_public_project_listing() -> None:
    handler = SimpleNamespace(
        _principal=lambda: {
            "tenant_id": "tenant_a",
            "name": "viewer",
            "role": "viewer",
            "auth_type": "bearer",
            "session_version": "1",
        },
        _tenant_project_ids=lambda: {"customer_a", "customer_b"},
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
