from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import ui_execution_adapter as adapter


def test_normalization_preserves_source_bound_formal_contract_fields() -> None:
    rows = adapter.normalize_ui_execution_requests([{
        "id": "visible-order-state",
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/orders/1",
        "execution_mode": "safe_read_only",
        "actor_ref": "actor_order_viewer",
        "severity": "P1",
        "source_refs": [{"source_id": "prd", "locator": "REQ-12"}],
        "success_criteria": {"kind": "title_contains", "expected": "Order"},
        "browser_plan": {"steps": [{"action": "goto", "url": "/orders/1"}]},
    }])

    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "visible-order-state"
    assert row["actor_ref"] == "actor_order_viewer"
    assert row["severity"] == "P1"
    assert row["source_refs"] == [{"source_id": "prd", "locator": "REQ-12"}]
    assert row["success_criteria"] == {
        "kind": "title_contains",
        "expected": "Order",
    }


def test_provider_findings_are_quarantined_as_clues(monkeypatch, tmp_path: Path) -> None:
    def fake_page_agent(*args, **kwargs):  # noqa: ANN002,ANN003
        return {
            "request_id": "ui-1",
            "provider": "page_agent",
            "status": "executed",
            "execution_status": "executed",
            "confirmation_status": "candidate",
            "artifacts": [],
            "findings": [{
                "finding_id": "provider-must-not-be-formal",
                "title": "Model guessed a visual defect",
            }],
        }

    monkeypatch.setattr(adapter, "execute_page_agent_request", None, raising=False)
    from ai_test_asset_center import page_agent_bridge
    monkeypatch.setattr(page_agent_bridge, "execute_page_agent_request", fake_page_agent)

    result = adapter.execute_ui_execution_requests(
        "project",
        [{
            "request_id": "ui-1",
            "provider": "page_agent",
            "execution_mode": "safe_read_only",
            "source_refs": [{"source_id": "prd", "locator": "REQ-1"}],
            "success_criteria": {"kind": "page_reachable", "expected": True},
        }],
        {"status": "approved", "approved_base_url": "https://example.test"},
        root=tmp_path,
        run_id="run-1",
    )

    assert result["findings"] == []
    assert [row["finding_id"] for row in result["provider_clues"]] == [
        "provider-must-not-be-formal"
    ]
    assert result["results"][0]["findings"] == []


def test_request_and_plan_execution_mode_mismatch_blocks_before_browser(tmp_path: Path) -> None:
    result = adapter.execute_ui_execution_requests(
        "project",
        [{
            "request_id": "ui-mode-drift",
            "provider": "playwright_browser_plan",
            "execution_mode": "safe_read_only",
            "browser_plan": {
                "execution_mode": "approved_sandbox_write",
                "steps": [{"action": "goto", "url": "/"}],
            },
            "source_refs": [{"source_id": "prd", "locator": "REQ-2"}],
            "success_criteria": {"kind": "page_reachable", "expected": True},
        }],
        {"status": "approved", "approved_base_url": "https://example.test"},
        root=tmp_path,
        run_id="run-2",
    )

    assert result["status"] == "blocked"
    assert result["findings"] == []
    assert result["results"][0]["reason"] == "UI_EXECUTION_MODE_MISMATCH"
