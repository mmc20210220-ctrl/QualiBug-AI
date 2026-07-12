from __future__ import annotations

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

os.environ.setdefault("QUALIBUG_JWT_SECRET", "test-private-pilot-secret")

from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.enterprise_delivery_package import create_delivery_package
from ai_test_asset_center.enterprise_source_registry import list_source_assets
from ai_test_asset_center.enterprise_test_data_receipts import issue_test_data_receipt
from ai_test_asset_center.evidence_artifact_store import verify_evidence_bundle
from ai_test_asset_center.execution_approvals import issue_execution_approval
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


API_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "paths": {"/api/cases": {"get": {"operationId": "listCases", "responses": {"200": {"description": "ok"}}}}},
        "components": {
            "schemas": {
                "Case": {
                    "type": "object",
                    "properties": {"state": {"type": "string", "enum": ["DRAFT", "APPROVED"]}},
                }
            }
        },
    }
)
PRD = "# Case lifecycle\nCase state must be observable before approval."
SCHEMA = "CREATE TABLE cases (id TEXT PRIMARY KEY, state TEXT CHECK (state IN ('DRAFT','APPROVED')));"


class _Target(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"items":[{"id":"case-1","state":"DRAFT"}]}')

    def log_message(self, *_args: object) -> None:
        return


def _ingest(project: str, root, filename: str, source_type: str, content: str) -> dict:
    calls: list[dict] = []

    def capture(payload, status: int = 200, extra_headers=None):
        calls.append({"payload": payload, "status": status, "extra_headers": extra_headers or {}})
        return None

    fake = SimpleNamespace(
        _require_known_project=lambda _project, _root: True,
        _json=capture,
    )
    PrivatePilotHandler._handle_ingest(
        fake,
        project,
        {
            "type": source_type,
            "filename": filename,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        },
        root,
        {"name": "qa", "role": "knowledge_admin"},
    )
    assert calls and calls[-1]["status"] == 200
    return calls[-1]["payload"]


def test_ingest_registers_source_and_local_readonly_execution_persists_evidence(tmp_path):
    project = "e2e-project"
    _ingest(project, tmp_path, "PRD.md", "prd", PRD)
    api_upload = _ingest(project, tmp_path, "openapi.json", "openapi", API_SPEC)
    _ingest(project, tmp_path, "schema.sql", "db_design", SCHEMA)

    manifest = api_upload["source_manifest"]
    assets = list_source_assets(project, root=tmp_path)
    assert manifest["source_id"]
    assert any(asset["latest_source_hash"] == manifest["source_hash"] for asset in assets)

    plan = scan(
        project=project,
        root=tmp_path,
        prd_text=PRD,
        api_doc_text=API_SPEC,
        campaign_context={
            "scope_id": "case-scope",
            "environment_ref": "local-runtime",
            "source_manifest": manifest,
            "test_data_contract": {"strategy": "blocked_with_testability_gap"},
        },
        save_report=True,
    )
    assert plan["incremental_discovery"]["selected_slices"]

    receipt = issue_test_data_receipt(
        project,
        root=tmp_path,
        kind="provenance",
        campaign_id=plan["campaign"]["campaign_id"],
        scope_id="case-scope",
        environment_ref="local-runtime",
        actor={"name": "qa", "role": "qa_lead"},
        provenance_ref="readonly-fixture",
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        approval = issue_execution_approval(
            project,
            root=tmp_path,
            campaign_id=plan["campaign"]["campaign_id"],
            scope_id="case-scope",
            environment_ref="local-runtime",
            source_hash=manifest["source_hash"],
            target_base_url=base_url,
            execution_mode="safe_read_only",
            expires_at_utc="2099-01-01T00:00:00Z",
            actor={"name": "qa", "role": "qa_lead"},
        )
        executed = scan(
            project=project,
            root=tmp_path,
            prd_text=PRD,
            api_doc_text=API_SPEC,
            base_url=base_url,
            campaign_context={
                "scope_id": "case-scope",
                "environment_ref": "local-runtime",
                "source_manifest": manifest,
                "execution_approval_id": approval["approval_id"],
                "execution_mode": "safe_read_only",
                "test_data_contract": {"strategy": "reuse_verified_existing", "provenance_ref": receipt["receipt_id"]},
            },
            save_report=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert executed["execution_status"] == "completed"
    assert executed["auto_har"]["status"] == "receipt_backed"
    assert executed["auto_har"]["entry_count"] > 0
    assert executed["auto_har"]["entries"][0]["request"]["method"] == "GET"
    assert executed["total_findings"] == 0
    assert all(item.get("confirmation_status") != "confirmed" for item in executed["candidate_findings"])
    assert verify_evidence_bundle(project, executed["evidence_bundle"]["bundle_id"], root=tmp_path)["valid"] is True
    assert executed["scan_preflight_guide"]["healthy_claim_allowed"] is False
    assert executed["pipeline_health"]["status"] != "OK"

    package = create_delivery_package(project, root=tmp_path, scan_result=executed)
    assert package["status"] == "created"
    assert package["release_verdict"] == "not_ready"
