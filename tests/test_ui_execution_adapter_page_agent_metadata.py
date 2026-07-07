from __future__ import annotations

from ai_test_asset_center.ui_execution_adapter import _page_agent_request_result


def test_page_agent_request_result_preserves_request_metadata_for_downstream_verification(tmp_path, monkeypatch) -> None:
    def fake_execute_page_agent_request(project_id, request, runtime_contract, *, root, run_id, execution_context=None):
        return {
            "request_id": request["request_id"],
            "title": request["title"],
            "provider": "page_agent",
            "bridge_provider": "page_agent_browser_plan",
            "status": "executed",
            "execution_status": "executed",
            "current_url": "http://127.0.0.1:8080/orders/ord_123",
            "artifacts": [],
            "findings": [],
            "created_data": {"entity": "order", "id": "ord_123"},
            "duration_ms": 12,
        }

    monkeypatch.setattr("ai_test_asset_center.page_agent_bridge.execute_page_agent_request", fake_execute_page_agent_request)

    result = _page_agent_request_result(
        "demo-project",
        {
            "request_id": "ui_req_meta",
            "title": "Open order detail page",
            "provider": "page_agent",
            "task": "Inspect order detail",
            "start_url": "http://127.0.0.1:8080/orders/ord_123",
            "metadata": {
                "verification": {
                    "kind": "sqlite_query",
                    "db_path": "orders.sqlite3",
                    "query": "SELECT 1",
                }
            },
        },
        {"status": "approved", "approved_base_url": "http://127.0.0.1:8080"},
        root=tmp_path,
        run_id="scan_ui",
    )

    assert result["metadata"]["verification"]["kind"] == "sqlite_query"
    assert result["metadata"]["verification"]["db_path"] == "orders.sqlite3"
