from __future__ import annotations

import sqlite3

from ai_test_asset_center.__main__ import _mark_high_confidence_ui_candidates, _verify_ui_candidate_findings
from ai_test_asset_center.v12_pipeline import _ui_bridge_finding


def test_ui_candidate_verification_accepts_consistent_page_agent_execution_evidence(tmp_path) -> None:
    items = [
        {
            "title": "订单详情页状态异常",
            "confidence_score": 0.71,
            "execution_status": "executed",
            "evidence": {
                "target": "http://127.0.0.1:8080/orders/ord_123",
                "reproduction_steps": ["打开订单详情页", "观察状态标签与按钮是否一致"],
            },
            "raw_evidence": {
                "has_real_evidence": True,
                "ui_execution_result": {
                    "request_id": "ui_req_1",
                    "bridge_provider": "page_agent_browser_plan",
                    "status": "executed",
                    "current_url": "http://127.0.0.1:8080/orders/ord_123",
                    "artifact_refs": ["platform_workspace/demo/page_agent_runs/ui_req_1/final.png"],
                },
                "created_data": {
                    "object_type": "order",
                    "object_id": "ord_123",
                    "data_scope_ref": "order:ord_123",
                    "object_url": "http://127.0.0.1:8080/orders/ord_123",
                },
            },
            "evidence_quality": {},
        }
    ]

    verified = _verify_ui_candidate_findings(items, root=tmp_path, runtime_contract={})
    row = verified[0]

    assert row["ui_verification"]["status"] == "verified"
    assert row["ui_verification"]["reason"] == "page_agent_execution_evidence_consistent"
    assert "current_url_contains_object_id" in row["ui_verification"]["signals"]
    assert row["evidence_quality"]["level"] == "runtime_consistent"
    assert row["evidence_quality"]["score"] == 80
    assert row["confidence_score"] == 0.8

    promoted = _mark_high_confidence_ui_candidates(verified)[0]
    assert promoted["high_confidence_candidate"] is False
    assert promoted["candidate_tier"] == "ui_candidate"


def test_ui_candidate_verification_rejects_page_agent_evidence_without_object_binding(tmp_path) -> None:
    items = [
        {
            "title": "订单详情页状态异常",
            "confidence_score": 0.71,
            "execution_status": "executed",
            "evidence": {
                "target": "http://127.0.0.1:8080/orders/list",
                "reproduction_steps": ["打开订单列表页"],
            },
            "raw_evidence": {
                "has_real_evidence": True,
                "ui_execution_result": {
                    "request_id": "ui_req_2",
                    "bridge_provider": "page_agent_browser_plan",
                    "status": "executed",
                    "current_url": "http://127.0.0.1:8080/orders/list",
                    "artifact_refs": ["platform_workspace/demo/page_agent_runs/ui_req_2/final.png"],
                },
                "created_data": {
                    "object_type": "order",
                    "object_id": "ord_999",
                    "data_scope_ref": "order:ord_999",
                },
            },
            "evidence_quality": {},
        }
    ]

    verified = _verify_ui_candidate_findings(items, root=tmp_path, runtime_contract={})
    row = verified[0]

    assert row["ui_verification"]["status"] == "mismatch"
    assert row["ui_verification"]["reason"] == "page_agent_object_binding_incomplete"
    assert row["confidence_score"] == 0.71


def test_ui_candidate_verification_uses_explicit_sqlite_verification_from_page_agent_metadata(tmp_path) -> None:
    db_path = tmp_path / "orders.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO orders(id, status) VALUES (?, ?)", ("ord_123", "PAID"))
        conn.commit()
    finally:
        conn.close()

    finding = _ui_bridge_finding(
        {"title": "订单详情页状态异常", "confidence_score": 0.82},
        request_result={
            "request_id": "ui_req_sqlite",
            "provider": "page_agent",
            "bridge_provider": "page_agent_browser_plan",
            "status": "executed",
            "current_url": "http://127.0.0.1:8080/orders/ord_123",
            "artifacts": [{"artifact_type": "screenshot", "ref": "platform_workspace/demo/page_agent_runs/ui_req_sqlite/final.png"}],
            "created_data": {"entity": "order", "id": "ord_123"},
            "metadata": {
                "verification": {
                    "kind": "sqlite_query",
                    "db_path": str(db_path),
                    "query": "SELECT id, status FROM orders WHERE id = '{object_id}'",
                    "min_rows": 1,
                }
            },
        },
        campaign_id="camp_ui",
        discovery_round=3,
    )

    verified = _verify_ui_candidate_findings([finding], root=tmp_path, runtime_contract={})
    row = verified[0]
    assert row["ui_verification"]["status"] == "verified"
    assert row["ui_verification"]["reason"] == "sqlite_row_match"
    assert row["evidence_quality"]["level"] == "cross_verified"
    assert row["evidence_quality"]["score"] == 85

    promoted = _mark_high_confidence_ui_candidates(verified)[0]
    assert promoted["high_confidence_candidate"] is True
    assert promoted["candidate_tier"] == "high_confidence_ui_candidate"
