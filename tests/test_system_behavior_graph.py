from ai_test_asset_center.private_pilot_regression_run_visibility_patch import inject_regression_run
from ai_test_asset_center.system_behavior_graph import build_system_behavior_graph


def _payload() -> dict:
    return {
        "data": {
            "project_id": "behavior_project",
            "defects": [
                {
                    "id": "BUG-1",
                    "title": "普通用户越权读取他人订单",
                    "risk_type": "permission_bypass",
                    "bug_status": "reproduced",
                    "repro_method": "GET",
                    "repro_path": "/api/orders/1001",
                    "source_entity": "订单",
                    "raw_evidence": {
                        "request_raw": {"method": "GET", "path": "/api/orders/1001", "actor": "buyer_a"},
                        "response_raw": {"status_code": 200, "body": "{\"order_id\":1001}"},
                        "has_real_evidence": True,
                    },
                    "reproduction": {"method": "GET", "path": "/api/orders/1001", "actor": "buyer_a"},
                }
            ],
            "clues": [
                {
                    "id": "CLUE-1",
                    "title": "订单状态流转异常",
                    "risk_type": "state_machine",
                    "repro_method": "POST",
                    "repro_path": "/api/orders/1001/pay",
                    "source_entity": "订单",
                }
            ],
            "coverage_matrix": {
                "risk_families": [
                    {"risk_family": "tenant_isolation", "coverage_status": "planned"}
                ]
            },
            "value_metrics": {},
            "data_contract": {},
        }
    }


def test_build_system_behavior_graph_from_existing_payload_facts() -> None:
    graph = build_system_behavior_graph(_payload()["data"])

    assert graph["graph_version"] == "system_behavior_graph.v1"
    assert graph["status"] == "evidence_backed_partial"
    assert graph["summary"]["business_object_count"] == 1
    assert graph["summary"]["api_count"] == 2
    assert graph["summary"]["invariant_count"] >= 3
    assert graph["planner_contract"]["can_seed_behavior_slices"] is True
    assert graph["planner_contract"]["required_missing_inputs"] == []
    assert any(item["label"] == "权限边界不变量" for item in graph["invariants"])
    assert any(item["label"] == "状态流转不变量" for item in graph["invariants"])
    assert "partial graph must not be presented as full system understanding" in graph["honesty_rule"]


def test_command_center_injects_system_behavior_graph() -> None:
    injected = inject_regression_run(_payload())
    data = injected["data"]

    assert data["system_behavior_graph"]["status"] == "evidence_backed_partial"
    assert data["system_behavior_graph"]["summary"]["business_object_count"] == 1
    assert data["system_behavior_graph"]["summary"]["api_count"] == 2
    assert data["value_metrics"]["behavior_graph_status"] == "evidence_backed_partial"
    assert data["value_metrics"]["behavior_graph_object_count"] == 1
    assert data["data_contract"]["system_behavior_graph"]["display_key"] == "system_behavior_graph"
    assert "partial" in data["data_contract"]["system_behavior_graph"]["honesty_rule"]
