from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.enterprise_source_registry import register_source_asset


API_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "paths": {"/api/orders/{order_id}": {"get": {"operationId": "getOrder", "responses": {"200": {"description": "ok"}}}}},
    }
)


def test_scan_promotes_persisted_followup_ui_request_to_cross_verified_high_confidence(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_service as service
    from ai_test_asset_center.v12_pipeline import _ui_bridge_finding

    project = "enterprise-project"
    manifest = register_source_asset(project, "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    db_path = tmp_path / "orders.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO orders(id, status) VALUES (?, ?)", ("ord_123", "PAID"))
        conn.commit()
    finally:
        conn.close()

    asset_path = tmp_path / "platform_workspace" / project / "defect_discovery" / "ui_followup_execution_requests.json"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text(
        json.dumps(
            {
                "version": "ui_followup_execution_requests_v1",
                "project_id": project,
                "items": [
                    {
                        "request_template_id": "UIFOLLOW_SQLITE_1",
                        "title": "复现场景：订单详情页异常",
                        "severity": "P1",
                        "path": "/orders/ord_123",
                        "task": "Re-open order details and collect deterministic UI evidence.",
                        "page_hints": ["候选路径：/orders/ord_123"],
                        "browser_plan": {
                            "execution_mode": "safe_read_only",
                            "steps": [{"action": "goto", "url": "/orders/ord_123", "wait_until": "networkidle"}],
                        },
                        "metadata": {
                            "bridge_mode": "page_agent_browser_plan",
                            "verification": {
                                "kind": "sqlite_query",
                                "db_path": str(db_path),
                                "query": "SELECT id, status FROM orders WHERE id = '{object_id}'",
                                "min_rows": 1,
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("QUALIBUG_PAGE_AGENT_BRIDGE_URL", "http://127.0.0.1:8797/execute")

    prepared = service._prepare_v12_scan_body(
        project,
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "service-a",
            "environment_ref": "local-benchmark",
            "source_manifest": manifest,
        },
        local_dev_mode=True,
    )

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_persist_execution_evidence(*args, **kwargs):
        return {
            "status": "persisted",
            "bundle_id": "evb_ui_cross_verified",
            "manifest_ref": "platform_outputs/enterprise-project/evidence/manifest.json",
        }

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        request = campaign_context["ui_execution_requests"][0]
        assert request["metadata"]["verification"]["kind"] == "sqlite_query"
        return {
            "runtime_contract": {"status": "approved", "approved_base_url": base_url},
            "phases": {
                "execution": {"status": "completed", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ui_cross_verified",
                "scope_id": "service-a",
                "environment_ref": "local-benchmark",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [],
            "ui_findings": [
                _ui_bridge_finding(
                    {"title": "订单详情页状态异常", "confidence_score": 0.82},
                    request_result={
                        "request_id": request["request_id"],
                        "title": request["title"],
                        "provider": "page_agent",
                        "bridge_provider": "page_agent_browser_plan",
                        "status": "executed",
                        "current_url": "http://127.0.0.1:8080/orders/ord_123",
                        "artifacts": [{"artifact_type": "screenshot", "ref": "platform_workspace/enterprise-project/page_agent_runs/scan/ui_req_sqlite/final.png"}],
                        "created_data": {"entity": "order", "id": "ord_123"},
                        "metadata": request["metadata"],
                    },
                    campaign_id="camp_ui_cross_verified",
                    discovery_round=1,
                )
            ],
            "ui_execution": {
                "status": "completed",
                "requested": 1,
                "executed": 1,
                "failed": 0,
                "blocked": 0,
                "provider_distribution": {"page_agent": 1},
                "results": [],
                "artifacts": [],
                "duration_ms": 12,
            },
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist_execution_evidence)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project=project,
        root=tmp_path,
        prd_text=str(prepared.get("prd") or ""),
        api_doc_text=str(prepared.get("api_doc") or ""),
        base_url=str(prepared.get("base_url") or ""),
        campaign_context=prepared,
    )

    assert result["layers"]["ui_execution"]["verified_candidates"] == 1
    assert result["layers"]["ui_execution"]["high_confidence_candidates"] == 1
    assert len(result["ui_candidate_findings"]) == 1
    row = result["ui_candidate_findings"][0]
    assert row["ui_verification"]["status"] == "verified"
    assert row["ui_verification"]["reason"] == "sqlite_row_match"
    assert row["evidence_quality"]["level"] == "cross_verified"
    assert row["evidence_quality"]["score"] == 85
    assert row["high_confidence_candidate"] is True
    assert row["candidate_tier"] == "high_confidence_ui_candidate"


def test_scan_preserves_request_verification_via_adapter_and_promotes_cross_verified(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_service as service
    from ai_test_asset_center.ui_execution_adapter import execute_ui_execution_requests
    from ai_test_asset_center.v12_pipeline import _normalize_ui_execution_findings

    project = "enterprise-project"
    manifest = register_source_asset(project, "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    db_path = tmp_path / "orders.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO orders(id, status) VALUES (?, ?)", ("ord_456", "PAID"))
        conn.commit()
    finally:
        conn.close()

    asset_path = tmp_path / "platform_workspace" / project / "defect_discovery" / "ui_followup_execution_requests.json"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text(
        json.dumps(
            {
                "version": "ui_followup_execution_requests_v1",
                "project_id": project,
                "items": [
                    {
                        "request_template_id": "UIFOLLOW_SQLITE_2",
                        "title": "复现场景：订单详情页状态异常-Adapter",
                        "severity": "P1",
                        "path": "/orders/ord_456",
                        "task": "Re-open order details and collect deterministic UI evidence.",
                        "page_hints": ["候选路径：/orders/ord_456"],
                        "browser_plan": {
                            "execution_mode": "safe_read_only",
                            "steps": [{"action": "goto", "url": "/orders/ord_456", "wait_until": "networkidle"}],
                        },
                        "metadata": {
                            "bridge_mode": "page_agent_browser_plan",
                            "verification": {
                                "kind": "sqlite_query",
                                "db_path": str(db_path),
                                "query": "SELECT id, status FROM orders WHERE id = '{object_id}'",
                                "min_rows": 1,
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("QUALIBUG_PAGE_AGENT_BRIDGE_URL", "http://127.0.0.1:8797/execute")

    prepared = service._prepare_v12_scan_body(
        project,
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "service-a",
            "environment_ref": "local-benchmark",
            "source_manifest": manifest,
        },
        local_dev_mode=True,
    )

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_persist_execution_evidence(*args, **kwargs):
        return {
            "status": "persisted",
            "bundle_id": "evb_ui_adapter_verified",
            "manifest_ref": "platform_outputs/enterprise-project/evidence/manifest.json",
        }

    def fake_execute_page_agent_request(project_id, request, runtime_contract, *, root, run_id, execution_context=None):
        return {
            "request_id": request["request_id"],
            "title": request["title"],
            "provider": "page_agent",
            "bridge_provider": "page_agent_browser_plan",
            "status": "executed",
            "execution_status": "executed",
            "current_url": "http://127.0.0.1:8080/orders/ord_456",
            "artifacts": [{"artifact_type": "screenshot", "ref": "platform_workspace/enterprise-project/page_agent_runs/scan/ui_req_sqlite_adapter/final.png"}],
            "findings": [{"title": "订单详情页状态异常", "confidence_score": 0.82}],
            "created_data": {"entity": "order", "id": "ord_456"},
            "duration_ms": 12,
        }

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        runtime_contract = {"status": "approved", "approved_base_url": base_url}
        ui_execution = execute_ui_execution_requests(
            project,
            campaign_context["ui_execution_requests"],
            runtime_contract,
            root=root,
            run_id="scan_ui_adapter",
            execution_context=campaign_context,
        )
        assert ui_execution["results"][0]["metadata"]["verification"]["kind"] == "sqlite_query"
        ui_findings, _ = _normalize_ui_execution_findings(
            ui_execution,
            campaign_id="camp_ui_adapter_verified",
            discovery_round=1,
        )
        return {
            "runtime_contract": runtime_contract,
            "phases": {
                "execution": {"status": "completed", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ui_adapter_verified",
                "scope_id": "service-a",
                "environment_ref": "local-benchmark",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [],
            "ui_findings": ui_findings,
            "ui_execution": ui_execution,
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist_execution_evidence)
    monkeypatch.setattr("ai_test_asset_center.page_agent_bridge.execute_page_agent_request", fake_execute_page_agent_request)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project=project,
        root=tmp_path,
        prd_text=str(prepared.get("prd") or ""),
        api_doc_text=str(prepared.get("api_doc") or ""),
        base_url=str(prepared.get("base_url") or ""),
        campaign_context=prepared,
    )

    assert result["layers"]["ui_execution"]["verified_candidates"] == 1
    assert result["layers"]["ui_execution"]["high_confidence_candidates"] == 1
    assert result["ui_execution"]["executed"] == 1
    row = result["ui_candidate_findings"][0]
    assert row["ui_verification"]["status"] == "verified"
    assert row["ui_verification"]["reason"] == "sqlite_row_match"
    assert row["evidence_quality"]["level"] == "cross_verified"
    assert row["high_confidence_candidate"] is True


def test_scan_preserves_request_http_verification_via_adapter_and_promotes_cross_verified(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_service as service
    from ai_test_asset_center.ui_execution_adapter import execute_ui_execution_requests
    from ai_test_asset_center.v12_pipeline import _normalize_ui_execution_findings

    class _ApiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/orders/ord_http_1":
                body = json.dumps({"id": "ord_http_1", "status": "PAID"}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        project = "enterprise-project"
        manifest = register_source_asset(project, "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

        asset_path = tmp_path / "platform_workspace" / project / "defect_discovery" / "ui_followup_execution_requests.json"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_text(
            json.dumps(
                {
                    "version": "ui_followup_execution_requests_v1",
                    "project_id": project,
                    "items": [
                        {
                            "request_template_id": "UIFOLLOW_HTTP_1",
                            "title": "复现场景：订单详情页异常-HttpGet",
                            "severity": "P1",
                            "path": "/orders/ord_http_1",
                            "task": "Re-open order details and collect deterministic UI evidence.",
                            "page_hints": ["候选路径：/orders/ord_http_1"],
                            "browser_plan": {
                                "execution_mode": "safe_read_only",
                                "steps": [{"action": "goto", "url": "/orders/ord_http_1", "wait_until": "networkidle"}],
                            },
                            "metadata": {
                                "bridge_mode": "page_agent_browser_plan",
                                "verification": {
                                    "kind": "http_get",
                                    "path": "/api/orders/{object_id}",
                                    "expected_statuses": [200],
                                    "body_contains": "PAID",
                                },
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        monkeypatch.setenv("QUALIBUG_PAGE_AGENT_BRIDGE_URL", "http://127.0.0.1:8797/execute")

        prepared = service._prepare_v12_scan_body(
            project,
            tmp_path,
            {"name": "local_dev", "role": "project_owner"},
            {
                "base_url": base_url,
                "scope_id": "service-a",
                "environment_ref": "local-benchmark",
                "source_manifest": manifest,
            },
            local_dev_mode=True,
        )

        def fake_run_preflight(config, api_doc_text):
            return {"ready": True, "checks": [], "summary": "ok"}

        def fake_release_gate(**kwargs):
            return {"verdict": "not_ready", "status": "ready"}

        def fake_persist_execution_evidence(*args, **kwargs):
            return {
                "status": "persisted",
                "bundle_id": "evb_ui_http_verified",
                "manifest_ref": "platform_outputs/enterprise-project/evidence/manifest.json",
            }

        def fake_execute_page_agent_request(project_id, request, runtime_contract, *, root, run_id, execution_context=None):
            return {
                "request_id": request["request_id"],
                "title": request["title"],
                "provider": "page_agent",
                "bridge_provider": "page_agent_browser_plan",
                "status": "executed",
                "execution_status": "executed",
                "current_url": f"{base_url}/orders/ord_http_1",
                "artifacts": [{"artifact_type": "screenshot", "ref": "platform_workspace/enterprise-project/page_agent_runs/scan/ui_req_http_adapter/final.png"}],
                "findings": [{"title": "订单详情页状态异常", "confidence_score": 0.82}],
                "created_data": {"entity": "order", "id": "ord_http_1"},
                "duration_ms": 12,
            }

        def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
            runtime_contract = {"status": "approved", "approved_base_url": base_url}
            ui_execution = execute_ui_execution_requests(
                project,
                campaign_context["ui_execution_requests"],
                runtime_contract,
                root=root,
                run_id="scan_ui_http_adapter",
                execution_context=campaign_context,
            )
            assert ui_execution["results"][0]["metadata"]["verification"]["kind"] == "http_get"
            ui_findings, _ = _normalize_ui_execution_findings(
                ui_execution,
                campaign_id="camp_ui_http_verified",
                discovery_round=1,
            )
            return {
                "runtime_contract": runtime_contract,
                "phases": {
                    "execution": {"status": "completed", "executed": 0},
                    "state_graph": {"coverage_gaps": []},
                    "incremental_discovery": {"selected_slices": []},
                },
                "campaign": {
                    "campaign_id": "camp_ui_http_verified",
                    "scope_id": "service-a",
                    "environment_ref": "local-benchmark",
                    "source_hash": manifest["source_hash"],
                },
                "findings": [],
                "external_findings": [],
                "ui_findings": ui_findings,
                "ui_execution": ui_execution,
                "auto_har": {"status": "no_traffic"},
                "total_duration_ms": 1,
            }

        monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
        monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
        monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist_execution_evidence)
        monkeypatch.setattr("ai_test_asset_center.page_agent_bridge.execute_page_agent_request", fake_execute_page_agent_request)
        monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

        result = scan(
            project=project,
            root=tmp_path,
            prd_text=str(prepared.get("prd") or ""),
            api_doc_text=str(prepared.get("api_doc") or ""),
            base_url=str(prepared.get("base_url") or ""),
            campaign_context=prepared,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert result["layers"]["ui_execution"]["verified_candidates"] == 1
    assert result["layers"]["ui_execution"]["high_confidence_candidates"] == 1
    assert result["ui_execution"]["executed"] == 1
    row = result["ui_candidate_findings"][0]
    assert row["ui_verification"]["status"] == "verified"
    assert row["ui_verification"]["reason"] == "http_status_and_body_match"
    assert row["evidence_quality"]["level"] == "cross_verified"
    assert row["high_confidence_candidate"] is True


def test_scan_materializes_cross_verified_ui_candidate_without_losing_verification_grade(monkeypatch, tmp_path):
    import ai_test_asset_center.private_pilot_service as service
    from ai_test_asset_center.ui_execution_adapter import execute_ui_execution_requests
    from ai_test_asset_center.v12_pipeline import _normalize_ui_execution_findings

    project = "enterprise-project"
    manifest = register_source_asset(project, "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    db_path = tmp_path / "orders.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO orders(id, status) VALUES (?, ?)", ("ord_asset_1", "PAID"))
        conn.commit()
    finally:
        conn.close()

    asset_path = tmp_path / "platform_workspace" / project / "defect_discovery" / "ui_followup_execution_requests.json"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text(
        json.dumps(
            {
                "version": "ui_followup_execution_requests_v1",
                "project_id": project,
                "items": [
                    {
                        "request_template_id": "UIFOLLOW_ASSET_SQLITE_1",
                        "title": "复现场景：订单详情页异常-Asset",
                        "severity": "P1",
                        "path": "/orders/ord_asset_1",
                        "task": "Re-open order details and collect deterministic UI evidence.",
                        "page_hints": ["候选路径：/orders/ord_asset_1"],
                        "browser_plan": {
                            "execution_mode": "safe_read_only",
                            "steps": [{"action": "goto", "url": "/orders/ord_asset_1", "wait_until": "networkidle"}],
                        },
                        "metadata": {
                            "bridge_mode": "page_agent_browser_plan",
                            "verification": {
                                "kind": "sqlite_query",
                                "db_path": str(db_path),
                                "query": "SELECT id, status FROM orders WHERE id = '{object_id}'",
                                "min_rows": 1,
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("QUALIBUG_PAGE_AGENT_BRIDGE_URL", "http://127.0.0.1:8797/execute")

    prepared = service._prepare_v12_scan_body(
        project,
        tmp_path,
        {"name": "local_dev", "role": "project_owner"},
        {
            "base_url": "http://127.0.0.1:8080",
            "scope_id": "service-a",
            "environment_ref": "local-benchmark",
            "source_manifest": manifest,
        },
        local_dev_mode=True,
    )

    def fake_run_preflight(config, api_doc_text):
        return {"ready": True, "checks": [], "summary": "ok"}

    def fake_release_gate(**kwargs):
        return {"verdict": "not_ready", "status": "ready"}

    def fake_persist_execution_evidence(*args, **kwargs):
        return {
            "status": "persisted",
            "bundle_id": "evb_ui_asset_verified",
            "manifest_ref": "platform_outputs/enterprise-project/evidence/manifest.json",
        }

    def fake_execute_page_agent_request(project_id, request, runtime_contract, *, root, run_id, execution_context=None):
        return {
            "request_id": request["request_id"],
            "title": request["title"],
            "provider": "page_agent",
            "bridge_provider": "page_agent_browser_plan",
            "status": "executed",
            "execution_status": "executed",
            "current_url": "http://127.0.0.1:8080/orders/ord_asset_1",
            "artifacts": [{"artifact_type": "screenshot", "ref": "platform_workspace/enterprise-project/page_agent_runs/scan/ui_req_asset/final.png"}],
            "findings": [{"title": "订单详情页状态异常", "confidence_score": 0.82}],
            "created_data": {"entity": "order", "id": "ord_asset_1"},
            "duration_ms": 12,
        }

    def fake_v12_pipeline(*, project, root, prd_text, api_spec_text, db_schema_text, base_url, campaign_context):
        runtime_contract = {"status": "approved", "approved_base_url": base_url}
        ui_execution = execute_ui_execution_requests(
            project,
            campaign_context["ui_execution_requests"],
            runtime_contract,
            root=root,
            run_id="scan_ui_asset_boundary",
            execution_context=campaign_context,
        )
        ui_findings, _ = _normalize_ui_execution_findings(
            ui_execution,
            campaign_id="camp_ui_asset_verified",
            discovery_round=1,
        )
        return {
            "runtime_contract": runtime_contract,
            "phases": {
                "execution": {"status": "completed", "executed": 0},
                "state_graph": {"coverage_gaps": []},
                "incremental_discovery": {"selected_slices": []},
            },
            "campaign": {
                "campaign_id": "camp_ui_asset_verified",
                "scope_id": "service-a",
                "environment_ref": "local-benchmark",
                "source_hash": manifest["source_hash"],
            },
            "findings": [],
            "external_findings": [],
            "ui_findings": ui_findings,
            "ui_execution": ui_execution,
            "auto_har": {"status": "no_traffic"},
            "total_duration_ms": 1,
        }

    monkeypatch.setattr("ai_test_asset_center.scan_diagnostics.run_preflight", fake_run_preflight)
    monkeypatch.setattr("ai_test_asset_center.__main__._evaluate_release_gate", fake_release_gate)
    monkeypatch.setattr("ai_test_asset_center.__main__._persist_execution_evidence", fake_persist_execution_evidence)
    monkeypatch.setattr("ai_test_asset_center.page_agent_bridge.execute_page_agent_request", fake_execute_page_agent_request)
    monkeypatch.setattr("ai_test_asset_center.v12_pipeline.run_v12_pipeline", fake_v12_pipeline)

    result = scan(
        project=project,
        root=tmp_path,
        prd_text=str(prepared.get("prd") or ""),
        api_doc_text=str(prepared.get("api_doc") or ""),
        base_url=str(prepared.get("base_url") or ""),
        campaign_context=prepared,
    )

    workspace_dir = tmp_path / "platform_workspace" / project / "defect_discovery"
    intake_payload = json.loads((workspace_dir / "internal_defect_intake_candidates.json").read_text(encoding="utf-8"))
    regression_payload = json.loads((workspace_dir / "ui_high_confidence_regression_candidates.json").read_text(encoding="utf-8"))

    assert result["layers"]["ui_execution"]["high_confidence_candidates"] == 1
    assert intake_payload["items"][0]["candidate_tier"] == "high_confidence_ui_candidate"
    assert intake_payload["items"][0]["verification_status"] == "verified"
    assert intake_payload["items"][0]["evidence_quality"]["level"] == "cross_verified"
    assert intake_payload["items"][0]["evidence_quality"]["score"] == 85
    assert regression_payload["items"][0]["candidate_tier"] == "high_confidence_ui_candidate"
    assert regression_payload["items"][0]["high_confidence_candidate"] is True
    assert regression_payload["items"][0]["verification_status"] == "verified"
    assert regression_payload["items"][0]["evidence_quality"]["level"] == "cross_verified"
    assert regression_payload["items"][0]["evidence_quality"]["score"] == 85
