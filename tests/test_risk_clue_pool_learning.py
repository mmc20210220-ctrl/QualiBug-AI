import json
from pathlib import Path

from ai_test_asset_center.risk_clue_pool import (
    get_platform_learning,
    get_project_learning,
    save_risk_clues,
)
from ai_test_asset_center.private_pilot_coverage_steering_patch import _steer_slices


def test_risk_clue_pool_builds_project_and_platform_learning(tmp_path: Path) -> None:
    result = save_risk_clues(
        "customer_a",
        tmp_path,
        [
            {
                "title": "普通用户跨租户读取订单接口返回 HTTP 200",
                "severity": "P1",
                "verdict": "confirmed",
                "validation_status": "confirmed",
                "repro_path": "/api/private/orders/1",
                "evidence": "api response leaked tenant data",
            },
            {
                "title": "退款重复提交需要沙箱数据验证",
                "severity": "P2",
                "verdict": "blocked",
                "validation_status": "blocked_requires_sandbox",
                "evidence": "POST /api/refunds requires sandbox approval",
            },
        ],
    )

    project_learning = get_project_learning("customer_a", tmp_path)
    platform_learning = get_platform_learning(tmp_path)

    assert result["project_learning_signal_count"] >= 2
    assert project_learning["version"] == "risk_clue_pool_project_learning.v2"
    assert project_learning["priority_weights"]["tenant_isolation"] > 0
    assert project_learning["priority_weights"]["authorization_access_control"] > 0
    assert platform_learning["version"] == "risk_clue_pool_platform_learning.v1"
    assert platform_learning["signal_count"] >= 1
    assert platform_learning["contributing_project_count"] == 1


def test_platform_learning_does_not_store_customer_raw_paths_or_titles(tmp_path: Path) -> None:
    save_risk_clues(
        "customer_secret_project",
        tmp_path,
        [
            {
                "title": "客户A秘密订单 /api/internal/customer-a/orders/123 泄露 tenant_id=secret",
                "severity": "P1",
                "verdict": "confirmed",
                "validation_status": "confirmed",
                "repro_path": "/api/internal/customer-a/orders/123",
            }
        ],
    )

    platform_path = tmp_path / "platform_outputs" / "_platform" / "risk_clue_pool" / "platform_learning.json"
    text = platform_path.read_text(encoding="utf-8")

    assert "客户A秘密订单" not in text
    assert "/api/internal/customer-a" not in text
    assert "tenant_id=secret" not in text
    assert "customer_secret_project" not in text
    assert "tenant_isolation" in text


def test_coverage_steering_uses_existing_risk_clue_learning(tmp_path: Path) -> None:
    save_risk_clues(
        "customer_a",
        tmp_path,
        [
            {
                "title": "跨租户越权接口返回 HTTP 200",
                "severity": "P1",
                "verdict": "confirmed",
                "validation_status": "confirmed",
                "repro_path": "/api/orders/1",
            }
        ],
    )

    slices = [
        {"slice_id": "money", "kind": "money", "entity": "orders", "priority": 0.2},
        {"slice_id": "tenant", "kind": "isolation", "entity": "orders", "priority": 0.2},
    ]
    ordered, diagnostic = _steer_slices(slices, root=tmp_path, project="customer_a")

    assert diagnostic["status"] == "applied"
    assert diagnostic["learning_steered_slice_count"] >= 1
    assert ordered[0]["slice_id"] == "tenant"
    assert ordered[0]["_learning_steering_reason"] == "prioritize_from_project_and_platform_risk_clue_pool"
