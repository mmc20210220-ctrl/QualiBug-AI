"""主链 6 (Bug 识别) 回归测试：被生产数据禁触安全边界拦截的执行产物
绝不可以被判为"已复现/已确认缺陷"。

跨链背景：主链 1 建立 production_data_exclusion 边界，主链 5 在执行引擎中
强制拦截命中项（不发请求）。若 oracle 仍基于"缺失的响应"产出 violation，
主链 6 的判定环节必须把它降级为 auditable candidate，且不能作为缺陷交付客户。
"""
from types import SimpleNamespace

import pytest

from ai_test_asset_center.v12_pipeline import _confirmed_oracle_finding
from ai_test_asset_center.__main__ import _dedupe_findings


def _make_scenario():
    return SimpleNamespace(
        actor_token="",
        actors=["readonly"],
        title="管理员创建用户",
        category="scenario_flow",
        behavior_slice_id="BHV_1",
        steps=[{"action": "create"}],
        execution_policy="runtime_approved",
    )


def _make_oracle_result(violated_rule="server_5xx", severity="P0"):
    return SimpleNamespace(
        passed=False,
        oracle_name="HttpStatusOracle",
        layer="L1",
        violated_rule=violated_rule,
        expected="服务应正常响应",
        actual="HTTP 500",
        severity=severity,
        confidence=0.95,
        explanation="命中 5xx 视为服务缺陷",
        to_dict=lambda: {"passed": False, "oracle_name": "HttpStatusOracle"},
    )


def _make_evidence(confirm=True):
    return SimpleNamespace(
        reproduction_steps="POST /api/admin/users",
        vote_summary={"confirmation_threshold_met": confirm},
        evidence_id="EVID_1",
        layers_triggered=["L1"],
    )


def _blocked_trace(with_errors=True):
    trace = {
        "steps": [
            {
                "action": "create",
                "method": "POST",
                "path": "/api/admin/users",
                "status": 0,
                "response": {
                    "status_code": 0,
                    "body": {"error": "production_data_exclusion_matched:/api/admin"},
                },
                "expected_status": 201,
                "skipped_reason": "production_data_exclusion_matched:/api/admin",
                "execution_blocked": True,
            }
        ],
        "production_data_blocked": True,
        "production_data_block_reason": "production_data_exclusion_matched:/api/admin",
    }
    if with_errors:
        trace["errors"] = ["production_data_exclusion_matched:/api/admin"]
    return trace


def test_blocked_step_downgraded_to_candidate():
    """Fix A: 被拦截的执行产物降级为 candidate，绝不交付为 defect。"""
    trace = _blocked_trace(with_errors=True)
    finding = _confirmed_oracle_finding(
        _make_scenario(), trace, _make_oracle_result(),
        _make_evidence(confirm=True),
        campaign_id="c1", discovery_round=1, base_url="http://x",
    )
    assert finding["gate_passed"] is False
    assert finding["confirmation_status"] == "candidate"
    assert finding["bug_status"] == "suspected"
    assert finding["customer_delivery_status"] == "blocked_safety_boundary"
    assert finding["blocked_by_safety_boundary"] is True
    assert finding["blocked_reason"] == "production_data_exclusion_matched:/api/admin"


def test_blocked_without_errors_still_downgraded():
    """Fix A 防御纵深：即使 trace.errors 为空，显式守卫仍强制降级
    （不依赖主链 5 写入 errors 的副作用）。"""
    trace = _blocked_trace(with_errors=False)
    assert "errors" not in trace  # 明确构造无 errors 场景
    finding = _confirmed_oracle_finding(
        _make_scenario(), trace, _make_oracle_result(),
        _make_evidence(confirm=True),
        campaign_id="c1", discovery_round=1, base_url="http://x",
    )
    assert finding["gate_passed"] is False
    assert finding["confirmation_status"] == "candidate"
    assert finding["bug_status"] == "suspected"
    assert finding["customer_delivery_status"] == "blocked_safety_boundary"


def test_non_blocked_real_evidence_confirmed():
    """对照：真实执行且有确凿证据时，正常判为已复现缺陷。"""
    trace = {
        "steps": [
            {
                "action": "create",
                "method": "POST",
                "path": "/api/orders",
                "status": 500,
                "response": {"status_code": 500, "body": {"error": "boom"}},
                "expected_status": 201,
            }
        ],
    }
    finding = _confirmed_oracle_finding(
        _make_scenario(), trace, _make_oracle_result(violated_rule="server_5xx"),
        _make_evidence(confirm=True),
        campaign_id="c1", discovery_round=1, base_url="http://x",
    )
    assert finding["gate_passed"] is True
    assert finding["confirmation_status"] == "confirmed"
    assert finding["bug_status"] == "reproduced"
    assert finding["customer_delivery_status"] == "defect"
    assert finding["blocked_by_safety_boundary"] is False


def test_expected_success_4xx_requires_valid_success_control():
    trace = {
        "steps": [
            {
                "action": "read",
                "method": "GET",
                "path": "/api/resources/missing-id",
                "status": 404,
                "response": {"status_code": 404, "body": {"error": "not_found"}},
                "expected_status": 200,
            }
        ]
    }
    finding = _confirmed_oracle_finding(
        _make_scenario(), trace, _make_oracle_result(violated_rule="expected_status_mismatch", severity="P1"),
        _make_evidence(confirm=True),
        campaign_id="c1", discovery_round=1, base_url="http://x",
    )
    assert finding["gate_passed"] is False
    assert finding["confirmation_status"] == "candidate"
    assert finding["customer_delivery_status"] == "candidate"
    assert finding["evidence_status"]["missing_requirements"] == ["VALID_SUCCESS_CONTROL_REQUIRED"]


def test_expected_success_4xx_can_confirm_after_valid_control():
    trace = {
        "request_contract_validation": {"valid_success_control": True},
        "steps": [
            {
                "action": "read",
                "method": "GET",
                "path": "/api/resources/known-id",
                "status": 404,
                "response": {"status_code": 404, "body": {"error": "not_found"}},
                "expected_status": 200,
            }
        ],
    }
    finding = _confirmed_oracle_finding(
        _make_scenario(), trace, _make_oracle_result(violated_rule="expected_status_mismatch", severity="P1"),
        _make_evidence(confirm=True),
        campaign_id="c1", discovery_round=1, base_url="http://x",
    )
    assert finding["gate_passed"] is True
    assert finding["confirmation_status"] == "confirmed"
    assert finding["customer_delivery_status"] == "defect"


def test_protocol_dedupe_collapses_dynamic_ids_but_preserves_business_values():
    def finding(resource_id: str, amount: int) -> dict:
        return {
            "title": f"server failure {resource_id}",
            "category": "protocol",
            "oracle": {
                "oracle_name": "HttpStatusOracle",
                "violated_rule": "server_5xx",
                "expected": "service should respond normally",
            },
            "raw_evidence": {
                "request_raw": {
                    "method": "POST",
                    "path": f"/api/orders/{resource_id}/pay",
                    "actor": "buyer",
                    "body": {"amount": amount, "requestId": resource_id},
                },
                "response_raw": {"status_code": 500, "body": {"error": "boom"}},
            },
            "evidence": {
                "request": f"POST /api/orders/{resource_id}/pay",
                "reproduction_steps": [f"POST /api/orders/{resource_id}/pay"],
            },
        }

    first_id = "4dd73e49-bb11-4bb4-887e-9e284dd315c6"
    second_id = "cc0aa36d-32e9-4a0e-b7ef-494a76b7eb45"
    third_id = "5cbf4db8-eaf9-414e-bc52-cad3e191f0af"
    deduped, report = _dedupe_findings([
        finding(first_id, 100),
        finding(second_id, 100),
        finding(third_id, 200),
    ])

    assert len(deduped) == 2
    assert report["collapsed_count"] == 1
    assert sorted(item["_duplicate_count"] for item in deduped) == [1, 2]


def test_blocked_reason_surfaced_on_trace_level():
    """Fix A: trace 级（非 step 级）的拦截原因也应被捕获。"""
    trace = {
        "steps": [],
        "production_data_blocked": True,
        "production_data_block_reason": "production_data_exclusion_matched:secret",
    }
    finding = _confirmed_oracle_finding(
        _make_scenario(), trace, _make_oracle_result(),
        _make_evidence(confirm=True),
        campaign_id="c1", discovery_round=1, base_url="http://x",
    )
    assert finding["blocked_by_safety_boundary"] is True
    assert finding["blocked_reason"] == "production_data_exclusion_matched:secret"
    assert finding["customer_delivery_status"] == "blocked_safety_boundary"
