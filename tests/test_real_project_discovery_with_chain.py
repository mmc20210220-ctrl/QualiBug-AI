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
    (input_dir / "prd.md").write_text("sample requirement", encoding="utf-8")
    _write_json(input_dir / "openapi.json", {"paths": {"/sample": {"get": {}}}})
    _write_json(input_dir / "test_accounts.json", {"normal_user": {"token": "env:test"}})
    _write_json(workspace_dir / "normalized_openapi.json", {"paths": {"/sample": {"get": {}}}})
    _write_json(knowledge_dir / "enterprise_business_knowledge_asset.json", {"summary": {"active_source_count": 1, "oracle_count": 1}})
    _write_json(workspace_dir / "defect_probes.json", {"items": [{"probe_id": "P1", "method": "GET", "path": "/sample"}]})
    _write_json(workspace_dir / "probe_execution_result.json", {"items": [{
        "probe_id": "P1",
        "execution_id": "EXEC-1",
        "timestamp": "2026-07-06T12:00:00Z",
        "response_status": 200,
        "duration_seconds": 0.1,
        "request": {"method": "GET", "url": "/sample"},
        "response": {"status_code": 200},
    }]})
    _write_json(output_dir / "discovered_issues.json", {"items": [{"issue_id": "I1", "title": "sample issue", "severity": "P1", "expected": "expected value", "actual": "actual value"}]})
    _write_json(output_dir / "evidence_bundle.json", {"items": [{"issue_id": "I1", "probe_id": "P1"}]})
    _write_json(output_dir / "real_project_defect_data.json", {"validated_bug_count": 1, "network_requests": 1, "summary": {"validated_bug_count": 1}})


def test_chain_aware_discovery_runner_normalizes_evidence_before_contract(monkeypatch, tmp_path: Path) -> None:
    project = "demo"
    _seed_complete_chain(tmp_path, project)

    def fake_discovery(project_id: str, root: Path) -> dict:
        assert project_id == project
        assert root == tmp_path
        return {"status": "succeeded", "summary": {"validated_bug_count": 1}}

    monkeypatch.setattr(real_project_discovery_with_chain, "_run_real_project_discovery", fake_discovery)

    result = real_project_discovery_with_chain.run_real_project_discovery_with_chain(project, tmp_path)
    bundle = json.loads((tmp_path / "platform_outputs" / project / "real_project" / "evidence_bundle.json").read_text(encoding="utf-8"))

    assert result["status"] == "succeeded"
    assert result["evidence_bundle_normalization"]["fully_normalized_count"] == 1
    assert result["summary"]["evidence_bundle_fully_normalized_count"] == 1
    assert bundle["items"][0]["reproduction"]["is_synthetic"] is False
    assert result["main_chain_contract"]["chain_ready"] is True
    assert result["main_chain_contract"]["customer_defect_delivery_ready"] is True
    assert result["summary"]["main_chain_ready"] is True
    assert result["summary"]["main_chain_first_blocked_stage"] == ""
    assert (tmp_path / "platform_outputs" / project / "real_project" / "main_chain_contract.json").exists()
    assert (tmp_path / "platform_workspace" / project / "real_project" / "main_chain_contract.json").exists()
    assert (tmp_path / "platform_outputs" / project / "real_project" / "evidence_bundle_normalization_report.json").exists()


def test_chain_aware_discovery_cli_entrypoint_is_registered() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")

    assert 'qualibug-discover-chain = "ai_test_asset_center.real_project_discovery_with_chain:main"' in source
