from ai_test_asset_center.private_pilot_no_fix_advice_patch import sanitize_customer_payload


def test_sanitize_customer_payload_strips_fix_advice_from_defects() -> None:
    payload = {
        "data": {
            "defects": [
                {
                    "id": "BUG-1",
                    "title": "越权访问已复现",
                    "recommended_fix": "在接口中增加 tenant_id 校验",
                    "technical_details": {
                        "api_endpoint": {"method": "GET", "path": "/api/orders/1"},
                        "possible_root_cause": "缺少租户过滤",
                        "recommended_fix": "添加 where tenant_id = current_tenant",
                    },
                    "evidence_chain": [{"tag": "response", "detail": "HTTP 200"}],
                    "regression": {"latest_status": "failed"},
                }
            ],
            "clues": [
                {"id": "CLUE-1", "title": "疑似状态机问题", "remediation": "修改状态流转代码"}
            ],
            "commercial_assets": {
                "delivery_package": {"release_verdict": "fail"},
            },
        }
    }

    sanitized = sanitize_customer_payload(payload)
    defect = sanitized["data"]["defects"][0]
    clue = sanitized["data"]["clues"][0]
    boundary = sanitized["data"]["product_responsibility_boundary"]
    contract = sanitized["data"]["data_contract"]["product_responsibility_boundary"]

    assert "recommended_fix" not in defect
    assert "recommended_fix" not in defect["technical_details"]
    assert "possible_root_cause" not in defect["technical_details"]
    assert "remediation" not in clue
    assert defect["evidence_chain"][0]["detail"] == "HTTP 200"
    assert defect["regression"]["latest_status"] == "failed"
    assert defect["product_responsibility_boundary"]["contract_version"] == "product_responsibility_boundary.v1"
    assert defect["product_responsibility_boundary"]["no_fix_advice"] is True
    assert boundary["contract_version"] == "product_responsibility_boundary.v1"
    assert boundary["customer_owns"]
    assert contract["display_key"] == "product_responsibility_boundary"
    assert contract["contract_version"] == "product_responsibility_boundary.v1"
    assert "fix advice" in contract["honesty_rule"]


def test_sanitize_customer_payload_strips_nested_repair_fields_without_losing_release_status() -> None:
    payload = {
        "data": {
            "project_id": "demo",
            "release_gate": {"overall_status": "fail"},
            "delivery_tracks": {
                "release_recommendation": "block_release",
                "repair_plan": {"step": "修改代码"},
            },
            "customer_delivery_guard": {
                "status": "blocked_by_release_gate",
                "customer_deliverable": False,
                "code_fix": "patch.diff",
            },
        }
    }

    sanitized = sanitize_customer_payload(payload)
    data = sanitized["data"]

    assert data["release_gate"]["overall_status"] == "fail"
    assert data["delivery_tracks"]["release_recommendation"] == "block_release"
    assert "repair_plan" not in data["delivery_tracks"]
    assert data["customer_delivery_guard"]["status"] == "blocked_by_release_gate"
    assert data["customer_delivery_guard"]["customer_deliverable"] is False
    assert "code_fix" not in data["customer_delivery_guard"]
    assert data["product_responsibility_boundary"]["contract_version"] == "product_responsibility_boundary.v1"
