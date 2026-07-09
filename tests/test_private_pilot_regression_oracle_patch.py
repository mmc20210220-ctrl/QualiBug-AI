import json
from pathlib import Path

from ai_test_asset_center.private_pilot_regression_oracle_patch import (
    _judge_with_structured_oracle,
    enrich_probe_oracle,
    infer_expected_status_code,
    install_regression_oracle_patch,
    restore_regression_oracle_patch,
)


def test_infer_expected_status_code_is_conservative() -> None:
    assert infer_expected_status_code({"risk_type": "tenant_isolation", "title": "cross tenant order read"}) == 403
    assert infer_expected_status_code({"risk_type": "permission_bypass", "title": "普通用户越权审批"}) == 403
    assert infer_expected_status_code({"risk_type": "input_validation", "title": "非法参数未校验"}) == 400
    assert infer_expected_status_code({"risk_type": "idempotency", "title": "重复提交未冲突"}) == 409
    assert infer_expected_status_code({"risk_type": "money", "title": "金额守恒异常"}) is None


def test_enrich_probe_oracle_preserves_explicit_status_and_expected_text() -> None:
    enriched = enrich_probe_oracle({
        "risk_type": "tenant_isolation",
        "title": "普通用户可读取其他租户订单",
        "expected": "原缺陷信号不应复现。",
        "raw_evidence": {"request_raw": {"status_code": 200}},
    })

    assert enriched["expected_status_code"] == 403
    assert enriched["buggy_status_code"] == 200
    assert enriched["regression_oracle"]["kind"] == "http_status"
    assert "HTTP 403" in enriched["expected"]
    assert "HTTP 200" in enriched["expected"]


def test_judge_with_structured_oracle_turns_review_into_pass_or_fail() -> None:
    def original_judge(probe, execution, skipped=False, skip_reason=""):
        return {
            "regression_probe_id": probe.get("regression_probe_id"),
            "status": "needs_review",
            "passed": False,
            "reason": "missing strong assertion",
            "execution": execution,
        }

    probe = {"regression_probe_id": "p1", "expected_status_code": 403}
    passed = _judge_with_structured_oracle(original_judge, probe, {"reachable": True, "status_code": 403})
    failed = _judge_with_structured_oracle(original_judge, probe, {"reachable": True, "status_code": 200})

    assert passed["status"] == "passed"
    assert passed["passed"] is True
    assert failed["status"] == "failed"
    assert failed["passed"] is False
    assert failed["regression_oracle"]["expected_status_code"] == 403


def test_installed_patch_builds_oracle_probe_and_runner_auto_judges(tmp_path: Path) -> None:
    install_regression_oracle_patch()
    try:
        from ai_test_asset_center.regression_runner import _judge_probe
        from ai_test_asset_center.regression_suite_builder import build_regression_suite

        project = "demo_project"
        workspace = tmp_path / "platform_workspace" / project / "defect_discovery"
        workspace.mkdir(parents=True)
        (workspace / "confirmed_findings.json").write_text(
            json.dumps({
                "evidence_001": {
                    "id": "BUG-001",
                    "title": "普通用户可读取其他租户订单",
                    "severity": "P1",
                    "risk_type": "tenant_isolation",
                    "raw_evidence": {"request_raw": {"status_code": 200}},
                    "reproduction": {"method": "GET", "path": "/api/orders/other-tenant-order"},
                }
            }),
            encoding="utf-8",
        )

        suite = build_regression_suite(project, root=tmp_path, options={})
        probe = suite["modes"]["release"]["items"][0]

        assert probe["expected_status_code"] == 403
        assert probe["buggy_status_code"] == 200
        assert probe["regression_oracle"]["kind"] == "http_status"
        result = _judge_probe(probe, {"reachable": True, "status_code": 403, "error": "", "body_excerpt": ""})
        assert result["status"] == "passed"
        assert result["passed"] is True
    finally:
        restore_regression_oracle_patch()
