from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest


class _State:
    count = 0
    result = "empty"
    lock = threading.Lock()


class _Page(BaseHTTPRequestHandler):
    def _html(self) -> bytes:
        with _State.lock:
            result = _State.result
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>upload scenario</title></head>
<body>
<form method="post" action="/upload" enctype="multipart/form-data">
<label for="fixture">Upload fixture</label><input id="fixture" name="fixture" type="file">
<button id="upload-submit" type="submit">上传</button></form>
<form method="post" action="/remove"><button id="remove-upload" type="submit">删除测试上传</button></form>
<p id="upload-result">{result}</p>
</body></html>""".encode("utf-8")

    def _send_html(self) -> None:
        body = self._html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/state":
            with _State.lock:
                body = json.dumps({"count": _State.count}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in {"/", "/customers/upload"}:
            self._send_html()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        with _State.lock:
            if self.path == "/upload":
                _State.count = 1
                _State.result = "上传成功"
            elif self.path == "/remove":
                _State.count = 0
                _State.result = "empty"
            else:
                self.send_response(404)
                self.end_headers()
                return
        self._send_html()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _server() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    with _State.lock:
        _State.count = 0
        _State.result = "empty"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, thread, f"http://{host}:{port}"


def _governed_scenario(tmp_path: Path) -> tuple[str, dict[str, Any]]:
    from ai_test_asset_center.enterprise_knowledge_center import ingest_enterprise_knowledge_documents
    from ai_test_asset_center.ui_upload_fixture_registry import (
        approve_upload_fixture,
        materialize_upload_fixture_bindings,
        register_upload_fixture,
    )
    from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
        install_upload_fixture_registry_integrity,
    )
    from ai_test_asset_center.ui_upload_scenario_registry import (
        approve_upload_scenario,
        approved_upload_scenario,
        register_upload_scenario,
    )
    from ai_test_asset_center.ui_upload_scenario_semantic_authority import (
        install_ui_upload_scenario_semantic_authority,
    )

    project = "upload_scenario_e2e"
    actor = {"name": "e2e", "role": "qa_lead"}
    install_upload_fixture_registry_integrity()
    install_ui_upload_scenario_semantic_authority()
    openapi = {
        "openapi": "3.0.0",
        "info": {"title": "Upload API", "version": "1.0.0"},
        "paths": {
            "/customers/upload": {
                "get": {
                    "operationId": "getCustomerUploadPage",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    permissions = {
        "permissions": [{
            "role": "admin",
            "resource": "/customers/upload",
            "actions": ["read"],
            "decision": "allow",
        }]
    }
    ingested = ingest_enterprise_knowledge_documents(
        project,
        [
            {
                "text": "# Bulk upload\nAdmin selects a CSV, clicks upload, sees 上传成功, then deletes the test upload.",
                "filename": "upload-ui.md",
                "source_type": "uiux_spec",
            },
            {"text": json.dumps(openapi), "filename": "upload-openapi.json", "source_type": "openapi"},
            {"text": json.dumps(permissions), "filename": "upload-permissions.json", "source_type": "permission_matrix"},
        ],
        root=tmp_path,
        actor=actor,
    )
    ui_source = next(row for row in ingested["created"] if row.get("original_name") == "upload-ui.md")
    source = tmp_path / "platform_inputs" / project / "inbox" / "customers.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("id,name\n1,Alice\n", encoding="utf-8")
    fixture = register_upload_fixture(
        project,
        file_path=source,
        fixture_name="customers-csv",
        root=tmp_path,
        actor=actor,
    )["fixture"]
    approved_fixture = approve_upload_fixture(
        project,
        fixture_id=fixture["fixture_id"],
        root=tmp_path,
        actor=actor,
    )["fixture"]
    binding_ref = str(approved_fixture["binding_ref"])
    candidate = register_upload_scenario(
        project,
        {
            "title": "客户批量上传并清理",
            "source_id": str(ui_source["source_id"]),
            "source_locator": "heading:Bulk upload",
            "operation_ref": "api:GET:/customers/upload",
            "actor_role": "admin",
            "start_url": "/customers/upload",
            "fixture_binding_refs": [binding_ref],
            "upload_selector": "#fixture",
            "submission_mode": "click_submit",
            "submit_selector": "#upload-submit",
            "cleanup_selector": "#remove-upload",
            "assertion_selector": "#upload-result",
            "assertion_text": "上传成功",
            "rendered_probe_selector": "#upload-result",
            "persistent_probe_url": "/state",
            "persistent_json_pointer": "/count",
        },
        root=tmp_path,
        actor=actor,
    )["scenario"]
    approved = approve_upload_scenario(
        project,
        scenario_id=str(candidate["scenario_id"]),
        root=tmp_path,
        actor=actor,
    )["scenario"]
    materialized = approved_upload_scenario(
        project,
        str(approved["scenario_ref"]),
        root=tmp_path,
    )
    return project, {
        "request": materialized["ui_execution_request"],
        "bindings": materialize_upload_fixture_bindings(
            project,
            list(materialized["fixture_binding_refs"]),
            root=tmp_path,
        ),
    }


def test_approved_upload_scenario_submits_asserts_and_restores_persistent_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
    from ai_test_asset_center import professional_ui_interaction_cleanup as interaction

    project, scenario = _governed_scenario(tmp_path)
    server, thread, base_url = _server()
    try:
        monkeypatch.setattr(interaction, "sandbox_write_allowed", lambda **_kwargs: (True, ""))
        monkeypatch.setattr(
            interaction,
            "target_policy_decision",
            lambda **_kwargs: {
                "decision_id": "upload-scenario-e2e-policy",
                "write_allowed": True,
                "read_allowed": True,
                "blocking_codes": [],
            },
        )
        result = interaction.execute_controlled_browser_plan(
            project,
            scenario["request"]["browser_plan"],
            {
                "status": "approved",
                "requested_base_url": base_url,
                "approved_base_url": base_url,
                "environment_ref": "local-playwright-scenario-e2e",
                "environment_kind": "test",
                "execution_mode": "approved_sandbox_write",
                "ui_file_bindings": scenario["bindings"],
            },
            root=tmp_path,
            run_id="upload-scenario-playwright-e2e",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result["status"] == "executed", result
    assert result["cleanup_receipt"]["status"] == "ACCEPTED"
    assert all(row["equivalent"] for row in result["cleanup_receipt"]["probe_comparisons"])
    with _State.lock:
        assert (_State.count, _State.result) == (0, "empty")
    assert [(row.get("phase"), row.get("action")) for row in result["steps"]] == [
        ("setup", "goto"),
        ("treatment", "set_input_files"),
        ("treatment", "click"),
        ("assertion", "expect_text"),
    ]
    assert [(row.get("phase"), row.get("action")) for row in result["cleanup_steps"]] == [
        ("cleanup", "click"),
        ("cleanup", "set_input_files"),
    ]
    assert result["evidence_privacy"]["har_persisted"] is False
    assert result["evidence_privacy"]["trace_persisted"] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert "id,name" not in serialized
    assert "Alice" not in serialized
    assert str(tmp_path.resolve()) not in serialized
