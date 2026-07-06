from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center import real_project_discovery_with_chain


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_complete_chain(root: Path, project: str) -> None:
    input_dir = root / "platform_inputs" / project
    workspace_dir = root / "platform_workspace" / project / "real_project"
    output_dir = root / "platform_outputs" / project / "real_project"
    knowledge_dir = root / "platform_outputs" / project / "enterprise_knowledge_center"

    _write_json(input_dir / "real_project_config.json", {"project_id": project, "base_url": "http://127.0.0.1:8000"})
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "prd.md").write_text("订单只能查看本人数据", encoding="utf-8")
    _write_json(input_dir / "openapi.json", {"paths": {"/api/orders/{id}": {"get": {}}}})
    _write_json(input_dir / "test_accounts.json", {"normal_user": {"token": "env:test"}})
    _write_json(workspace_dir / "normalized_openapi.json", {"paths": {"/api/orders/{id}": {"get": {}}}})
    _write_json(knowledge_dir / "enterprise_business_knowledge_asset.json", {"summary": {"active_source_count": 1, "oracle_count": 1}})
    _write_json(workspace_dir / "defect_probes.json", {"items": [{"probe_id": "P1", "method": "GET", "path": "/api/orders/{id}"}]})
    _write_json(workspace_dir / "probe_execution_result.json", {"items": [{"probe_id": "P1", "response_status": 200, "duration_seconds": 0.1}]})
    _write_json(output_dir / "discovered_issues.json", {"items": [{"issue_id": "I1", "title": "订单越权", "severity": "P1"}]})
    _write_json(output_dir / "evidence_bundle.json", {"items": [{"issue_id": "I1", "request": {"method": "GET", "url": "/api/orders/1"}, "response": {"status_code": 200}, "expected": "只能访问本人订单", "actual": "返回他人订单"}]})
    _write_json(output_dir / "real_project_defect_data.json", {"validated_bug_count": 1, "network_requests": 1, "summary": {"validated_bug_count": 1}})


def test_chain_aware_discovery_runner_attaches_main_chain_contract(monkeypatch, tmp_path: Path) -> None:
    project = "demo"
    _seed_complete_chain(tmp_path, project)

    def fake_discovery(project_id: str, root: Path) -> dict:
        assert project_id == project
        assert root == tmp_path
        return {"status": "succeeded", "summary": {"validated_bug_count": 1}}

    monkeypatch.setattr(real_project_discovery_with_chain, "_run_real_project_discovery", fake_discovery)

    result = real_project_discovery_with_chain.run_real_project_discovery_with_chain(project, tmp_path)

    assert result["status"] == "succeeded"
    assert result["main_chain_contract"]["chain_ready"] is True
    assert result["main_chain_contract"]["customer_defect_delivery_ready"] is True
    assert result["summary"]["main_chain_ready"] is True
    assert result["summary"]["main_chain_first_blocked_stage"] == ""
    assert (tmp_path / "platform_outputs" / project / "real_project" / "main_chain_contract.json").exists()
    assert (tmp_path / "platform_workspace" / project / "real_project" / "main_chain_contract.json").exists()


def test_chain_aware_discovery_cli_entrypoint_is_registered() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")

    assert 'qualibug-discover-chain = "ai_test_asset_center.real_project_discovery_with_chain:main"' in source
