from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


_HTML = b"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>QualiBug upload fixture E2E</title></head>
<body>
  <label for="fixture">Upload fixture</label>
  <input id="fixture" type="file">
  <p id="selected">empty</p>
  <script>
    const input = document.getElementById('fixture');
    const selected = document.getElementById('selected');
    const render = () => {
      selected.textContent = input.files && input.files.length
        ? `selected:${input.files[0].name}`
        : 'empty';
    };
    input.addEventListener('input', render);
    input.addEventListener('change', render);
  </script>
</body>
</html>
"""


class _UploadFixturePage(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/", "/index.html"}:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_HTML)))
        self.end_headers()
        self.wfile.write(_HTML)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _start_page() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UploadFixturePage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, thread, f"http://{host}:{port}"


def test_approved_upload_fixture_executes_and_cleanup_restores_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.sync_api")

    from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
    from ai_test_asset_center import ui_upload_fixture_registry as registry
    from ai_test_asset_center.professional_ui_complex_interactions import (
        install_professional_ui_complex_interactions,
    )
    from ai_test_asset_center.professional_ui_interaction_cleanup import (
        install_controlled_ui_interaction,
    )
    from ai_test_asset_center.professional_ui_readonly import (
        install_professional_ui_readonly,
    )
    from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
        install_upload_fixture_registry_integrity,
    )

    install_upload_fixture_registry_integrity()
    install_professional_ui_readonly()
    install_controlled_ui_interaction()
    install_professional_ui_complex_interactions()

    project = "upload_fixture_e2e"
    actor = {"name": "e2e", "role": "qa_lead"}
    source = tmp_path / "platform_inputs" / project / "inbox" / "customer_payload.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("qualibug-upload-fixture-e2e", encoding="utf-8")

    registered = registry.register_upload_fixture(
        project,
        file_path=source,
        fixture_name="customer_payload",
        root=tmp_path,
        actor=actor,
    )
    approved = registry.approve_upload_fixture(
        project,
        fixture_id=registered["fixture"]["fixture_id"],
        root=tmp_path,
        actor=actor,
    )
    binding_ref = str(approved["fixture"]["binding_ref"])
    binding = registry.approved_upload_fixture_binding(
        project,
        binding_ref,
        root=tmp_path,
    )
    approved_filename = Path(str(binding["file_path"])).name

    server, thread, base_url = _start_page()
    try:
        monkeypatch.setattr(
            interaction,
            "sandbox_write_allowed",
            lambda **_kwargs: (True, ""),
        )
        monkeypatch.setattr(
            interaction,
            "target_policy_decision",
            lambda **_kwargs: {
                "decision_id": "upload-fixture-e2e-policy",
                "write_allowed": True,
                "read_allowed": True,
                "blocking_codes": [],
            },
        )

        runtime_contract = {
            "status": "approved",
            "requested_base_url": base_url,
            "approved_base_url": base_url,
            "environment_ref": "local-playwright-e2e",
            "environment_kind": "test",
            "execution_mode": "approved_sandbox_write",
            "ui_file_bindings": {binding_ref: binding},
        }
        plan = {
            "execution_mode": "approved_sandbox_write",
            "write_approved": True,
            "actor_ref": "e2e:qa_lead",
            "interaction_contract": {
                "cleanup_strategy": "browser_compensation",
                "equivalence": "source_declared_state_probes",
                "target_scope": "approved_nonproduction_target",
            },
            "state_probes": [
                {
                    "probe_id": "selected_file_state",
                    "property": "text",
                    "selector": "#selected",
                }
            ],
            "steps": [
                {
                    "phase": "setup",
                    "action": "goto",
                    "url": "/",
                    "wait_until": "load",
                },
                {
                    "phase": "treatment",
                    "action": "set_input_files",
                    "selector": "#fixture",
                    "file_refs": [binding_ref],
                },
                {
                    "phase": "assertion",
                    "action": "expect_text",
                    "selector": "#selected",
                    "expected": f"selected:{approved_filename}",
                    "match": "equals",
                },
                {
                    "phase": "cleanup",
                    "action": "set_input_files",
                    "selector": "#fixture",
                    "file_refs": [],
                },
            ],
        }

        result = interaction.execute_controlled_browser_plan(
            project,
            plan,
            runtime_contract,
            root=tmp_path,
            run_id="upload-fixture-playwright-e2e",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result["status"] == "executed", result
    assert result["execution_status"] == "executed"
    assert result["cleanup_receipt"]["status"] == "ACCEPTED"
    treatment = next(
        row
        for row in result["steps"]
        if row.get("action") == "set_input_files" and row.get("phase") == "treatment"
    )
    assert treatment["uploaded_file_count"] == 1
    assert treatment["uploaded_files"][0]["sha256"] == binding["sha256"]
    assert treatment["upload_binding_authority"] == "runtime_contract.ui_file_bindings"
    cleanup = next(
        row
        for row in result["cleanup_steps"]
        if row.get("action") == "set_input_files" and row.get("phase") == "cleanup"
    )
    assert cleanup["uploaded_file_count"] == 0

    serialized = json.dumps(result, ensure_ascii=False)
    assert str((tmp_path / binding["file_path"]).resolve()) not in serialized
    assert "qualibug-upload-fixture-e2e" not in serialized
    assert treatment["uploaded_files"][0]["raw_file_path_included"] is False
    assert treatment["uploaded_files"][0]["raw_file_content_included"] is False
