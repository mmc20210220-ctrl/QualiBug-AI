import json
from pathlib import Path

from ai_test_asset_center.risk_clue_pool import (
    get_platform_learning,
    get_project_learning,
    save_risk_clues,
)
from ai_test_asset_center.private_pilot_coverage_steering_patch import _steer_slices


def _write_system_promise_history(tmp_path: Path, project: str) -> None:
    history_path = tmp_path / "platform_outputs" / project / "regression_run" / "regression_run_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(
            [
                {
                    "items": [
                        {
                            "issue_id": "E-1",
                            "regression_probe_id": "REG-1",
                            "title": "客户原始标题不能进入平台学习",
                            "status": "failed",
                            "path": "/api/secret/orders/1",
                            "regression_contract": {
                                "contract_type": "system_behavior_promise_regression",
                                "system_behavior_space": {
                                    "promise_id": "promise_secret_customer_a",
                                    "dimensions": ["money", "tenant", "ui_api_contract"],
                                    "surface_plan": ["api", "db", "ui"],
                                    "source_family": "money",
                                },
                                "promise_id": "promise_secret_customer_a",
                                "dimensions": ["money", "tenant", "ui_api_contract"],
                                "surface_plan": ["api", "db", "ui"],
                            },
                        }
                    ]
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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
    assert project_learning["version"] == "risk_clue_pool_project_learning.v3"
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


def test_risk_clue_pool_learns_structured_system_promise_regression_contract(tmp_path: Path) -> None:
    project = "customer_a"
    _write_system_promise_history(tmp_path, project)

    save_risk_clues(project, tmp_path, [])
    project_learning = get_project_learning(project, tmp_path)
    platform_learning = get_platform_learning(tmp_path)

    assert project_learning["system_promise_signal_count"] == 1
    assert project_learning["priority_weights"]["money_quantity_conservation"] > 0
    assert project_learning["priority_weights"]["tenant_isolation"] > 0
    assert project_learning["priority_weights"]["ui_api_contract_drift"] > 0
    assert project_learning["priority_weights"]["surface_combo:api+db+ui"] > 0

    platform_text = json.dumps(platform_learning, ensure_ascii=False)
    assert "promise_secret_customer_a" not in platform_text
    assert "/api/secret/orders" not in platform_text
    assert "客户原始标题" not in platform_text
    assert "system_promise_signal" in platform_text


def test_project_learning_refreshes_from_regression_history_without_save_risk_clues(tmp_path: Path) -> None:
    project = "customer_a"
    _write_system_promise_history(tmp_path, project)

    project_learning = get_project_learning(project, tmp_path)
    platform_learning = get_platform_learning(tmp_path)

    assert project_learning["system_promise_signal_count"] == 1
    assert project_learning["priority_weights"]["money_quantity_conservation"] > 0
    assert project_learning["priority_weights"]["surface_combo:api+db+ui"] > 0
    assert platform_learning["signal_count"] >= 1


def test_coverage_steering_uses_structured_system_promise_learning(tmp_path: Path) -> None:
    project = "customer_a"
    _write_system_promise_history(tmp_path, project)

    slices = [
        {"slice_id": "plain_state", "kind": "state_machine", "entity": "orders", "priority": 0.2},
        {
            "slice_id": "system_promise_money_tenant_ui",
            "kind": "invariant",
            "entity": "orders",
            "priority": 0.2,
            "_selection_origin": "system_behavior_space",
            "_system_behavior_dimensions": ["money", "tenant", "ui_api_contract"],
            "_system_behavior_surface_plan": ["api", "db", "ui"],
        },
    ]
    ordered, diagnostic = _steer_slices(slices, root=tmp_path, project=project)

    assert diagnostic["status"] == "applied"
    assert diagnostic["system_behavior_learning_steered_slice_count"] == 1
    assert ordered[0]["slice_id"] == "system_promise_money_tenant_ui"
    assert ordered[0]["_learning_steering"]["system_behavior_slice"] is True
    assert "money_quantity_conservation" in ordered[0]["_learning_steering"]["matched_families"]
    assert ordered[0]["_learning_steering"]["surfaces"] == ["api", "db", "ui"]
