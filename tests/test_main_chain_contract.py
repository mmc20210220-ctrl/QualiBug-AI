from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.main_chain_contract import evaluate_main_chain_contract


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_main_chain_contract_identifies_missing_enterprise_inputs(tmp_path: Path) -> None:
    contract = evaluate_main_chain_contract("demo", tmp_path, persist=False)

    assert contract["chain_ready"] is False
    assert contract["summary"]["first_blocked_stage"] == "enterprise_inputs"
    input_stage = contract["stages"][0]
    assert input_stage["status"] == "missing"
    assert "missing_real_project_config" in input_stage["blockers"]
    assert "missing_openapi_contract" in input_stage["blockers"]


def test_main_chain_contract_passes_only_when_full_chain_artifacts_exist(tmp_path: Path) -> None:
    project = "demo"
    input_dir = tmp_path / "platform_inputs" / project
    workspace_dir = tmp_path / "platform_workspace" / project / "real_project"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    knowledge_dir = tmp_path / "platform_outputs" / project / "enterprise_knowledge_center"

    _write_json(input_dir / "real_project_config.json", {"project_id": project, "base_url": "http://127.0.0.1:8000"})
    (input_dir / "prd.md").parent.mkdir(parents=True, exist_ok=True)
    (input_dir / "prd.md").write_text("订单必须只能查看本人数据", encoding="utf-8")
    _write_json(input_dir / "openapi.json", {"paths": {"/api/orders/{id}": {"get": {}}}})
    _write_json(input_dir / "test_accounts.json", {"normal_user": {"token": "env:test"}})
    _write_json(workspace_dir / "normalized_openapi.json", {"paths": {"/api/orders/{id}": {"get": {}}}})
    _write_json(knowledge_dir / "enterprise_business_knowledge_asset.json", {"summary": {"active_source_count": 1, "oracle_count": 1}})
    _write_json(workspace_dir / "defect_probes.json", {"items": [{"probe_id": "P1", "method": "GET", "path": "/api/orders/{id}"}]})
    _write_json(workspace_dir / "probe_execution_result.json", {"items": [{"probe_id": "P1", "response_status": 200, "duration_seconds": 0.1}]})
    _write_json(output_dir / "discovered_issues.json", {"items": [{"issue_id": "I1", "title": "订单越权", "severity": "P1"}]})
    _write_json(output_dir / "evidence_bundle.json", {"items": [{"issue_id": "I1", "request": {"method": "GET", "url": "/api/orders/1"}, "response": {"status_code": 200}, "expected": "只能访问本人订单", "actual": "返回他人订单"}]})
    _write_json(output_dir / "real_project_defect_data.json", {"validated_bug_count": 1, "network_requests": 1, "summary": {"validated_bug_count": 1}})

    contract = evaluate_main_chain_contract(project, tmp_path, persist=True)

    assert contract["chain_ready"] is True
    assert contract["customer_defect_delivery_ready"] is True
    assert contract["summary"]["passed_stage_count"] == 6
    assert contract["summary"]["first_blocked_stage"] == ""
    assert (output_dir / "main_chain_contract.json").exists()
    assert (workspace_dir / "main_chain_contract.json").exists()


def test_main_chain_contract_does_not_mark_candidates_as_customer_ready(tmp_path: Path) -> None:
    project = "demo"
    input_dir = tmp_path / "platform_inputs" / project
    workspace_dir = tmp_path / "platform_workspace" / project / "real_project"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    knowledge_dir = tmp_path / "platform_outputs" / project / "enterprise_knowledge_center"

    _write_json(input_dir / "real_project_config.json", {"project_id": project})
    _write_json(input_dir / "openapi.json", {"paths": {"/api/orders/{id}": {"get": {}}}})
    _write_json(workspace_dir / "normalized_openapi.json", {"paths": {"/api/orders/{id}": {"get": {}}}})
    _write_json(knowledge_dir / "enterprise_business_knowledge_asset.json", {"summary": {"active_source_count": 1, "oracle_count": 1}})
    _write_json(workspace_dir / "defect_probes.json", {"items": [{"probe_id": "P1", "method": "GET", "path": "/api/orders/{id}"}]})
    _write_json(workspace_dir / "probe_execution_result.json", {"items": [{"probe_id": "P1", "response_status": 200, "duration_seconds": 0.1}]})
    _write_json(output_dir / "discovered_issues.json", {"items": [{"issue_id": "I1", "title": "候选线索", "severity": "P2"}]})
    _write_json(output_dir / "evidence_bundle.json", {"items": [{"issue_id": "I1", "request": {"method": "GET"}, "response": {"status_code": 200}, "expected": "拒绝", "actual": "成功"}]})
    _write_json(output_dir / "real_project_defect_data.json", {"candidate_issue_count": 1, "validated_bug_count": 0, "network_requests": 1})

    contract = evaluate_main_chain_contract(project, tmp_path, persist=False)

    assert contract["chain_ready"] is False
    assert contract["customer_defect_delivery_ready"] is False
    assert contract["summary"]["first_blocked_stage"] == "bug_discovery"
    bug_stage = next(stage for stage in contract["stages"] if stage["stage"] == "bug_discovery")
    assert bug_stage["status"] == "partial"
    assert "no_validated_bug" in bug_stage["blockers"]
