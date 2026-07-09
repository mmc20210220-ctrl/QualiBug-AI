import json
from pathlib import Path

from ai_test_asset_center.behavior_learning_memory import persist_behavior_learning_memory
from ai_test_asset_center.platform_behavior_learning_memory import (
    apply_platform_learning_to_probe_candidates,
    persist_platform_behavior_learning_memory,
    sanitize_project_signal_for_platform,
)


def test_platform_signal_sanitizer_removes_customer_raw_data() -> None:
    signal = {
        "source": "confirmed_finding",
        "evidence_id": "ev-secret",
        "entity": "customer_secret_order_table",
        "title": "客户A订单 /api/internal/orders/123 泄露 tenant_id=abc",
        "dimensions": ["tenant", "authorization"],
        "surfaces": ["api", "db"],
        "severity": "P1",
        "learning_weight": 1.0,
    }

    sanitized = sanitize_project_signal_for_platform("customer-a-project", signal)
    text = json.dumps(sanitized, ensure_ascii=False)

    assert "客户A" not in text
    assert "/api/internal/orders" not in text
    assert "customer_secret_order_table" not in text
    assert "ev-secret" not in text
    assert sanitized["source_project_hash"]
    assert sanitized["entity_archetype"] == "tenant_scoped_object"
    assert sanitized["dimensions"] == ["authorization", "tenant"]
    assert sanitized["surfaces"] == ["api", "db"]


def test_platform_learning_aggregates_project_memories_without_raw_paths(tmp_path: Path) -> None:
    for project, title in {
        "customer_a": "普通用户跨租户读取订单 /api/a/orders/1 返回 HTTP 200",
        "customer_b": "管理员按钮在普通用户页面可见 /secret/customer-b 页面泄露",
    }.items():
        ws = tmp_path / "platform_workspace" / project / "defect_discovery"
        ws.mkdir(parents=True)
        (ws / "confirmed_findings.json").write_text(json.dumps({
            "ev1": {"title": title, "severity": "P1", "repro_path": "/api/private"}
        }, ensure_ascii=False), encoding="utf-8")
        persist_behavior_learning_memory(project, tmp_path)

    platform = persist_platform_behavior_learning_memory(tmp_path)
    text = json.dumps(platform, ensure_ascii=False)

    assert platform["version"] == "platform_behavior_learning_memory.v1"
    assert platform["summary"]["contributing_project_count"] == 2
    assert platform["summary"]["platform_signal_count"] == 2
    assert "customer_a" not in text
    assert "customer_b" not in text
    assert "/api/a/orders" not in text
    assert "/secret/customer-b" not in text
    assert "普通用户跨租户读取订单" not in text
    assert platform["privacy_rule"].startswith("Stores only sanitized pattern signals")


def test_platform_learning_boosts_new_project_probe_candidates(tmp_path: Path) -> None:
    ws = tmp_path / "platform_workspace" / "customer_a" / "defect_discovery"
    ws.mkdir(parents=True)
    (ws / "confirmed_findings.json").write_text(json.dumps({
        "ev1": {"title": "跨租户越权接口返回 HTTP 200", "severity": "P1", "repro_path": "/api/orders/1"}
    }, ensure_ascii=False), encoding="utf-8")
    persist_behavior_learning_memory("customer_a", tmp_path)
    platform = persist_platform_behavior_learning_memory(tmp_path)

    space = {
        "summary": {},
        "probe_candidates": [
            {
                "probe_id": "tenant_probe",
                "entity": "orders",
                "priority": 0.4,
                "surface_plan": ["api", "db"],
                "oracle_intent": ["promise_violation:tenant", "promise_violation:authorization"],
            },
            {
                "probe_id": "low_signal_probe",
                "entity": "profile",
                "priority": 0.4,
                "surface_plan": ["ui"],
                "oracle_intent": ["promise_violation:performance"],
            },
        ],
    }

    boosted = apply_platform_learning_to_probe_candidates(space, platform)

    assert boosted["summary"]["platform_learning_signal_count"] == 1
    assert boosted["summary"]["platform_learning_contributing_project_count"] == 1
    assert boosted["summary"]["platform_learning_boosted_probe_count"] >= 1
    assert boosted["probe_candidates"][0]["probe_id"] == "tenant_probe"
    assert boosted["probe_candidates"][0]["priority"] > 0.4
    assert boosted["probe_candidates"][0]["platform_learning_memory_version"] == "platform_behavior_learning_memory.v1"
