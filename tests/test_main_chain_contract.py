from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.main_chain_contract import evaluate_main_chain_contract


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_chain_until_evidence(root: Path, project: str, *, validated_bug_count: int = 1) -> tuple[Path, Path, Path, Path]:
    input_dir = root / "platform_inputs" / project
    workspace_dir = root / "platform_workspace" / project / "real_project"
    output_dir = root / "platform_outputs" / project / "real_project"
    knowledge_dir = root / "platform_outputs" / project / "enterprise_knowledge_center"

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
    _write_json(output_dir / "real_project_defect_data.json", {"validated_bug_count": validated_bug_count, "network_requests": 1, "summary": {"validated_bug_count": validated_bug_count}})
    return input_dir, workspace_dir, output_dir, knowledge_dir


def _strict_evidence_item() -> dict:
    return {
        "issue_id": "I1",
        "probe_id": "P1",
        "execution_id": "EXEC-1",
        "timestamp": "2026-07-06T12:00:00Z",
        "request": {"method": "GET", "url": "/api/orders/1"},
        "response": {"status_code": 200, "body": {"owner_id": "other-user"}},
        "expected": "只能访问本人订单",
        "actual": "返回他人订单",
        "reproduction": {"method": "GET", "path": "/api/orders/1", "is_synthetic": False},
        "proof": {"can_reproduce": True, "repro_rate": 100},
    }


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
    _, workspace_dir, output_dir, _ = _seed_chain_until_evidence(tmp_path, project)
    _write_json(output_dir / "evidence_bundle.json", {"items": [_strict_evidence_item()]})

    contract = evaluate_main_chain_contract(project, tmp_path, persist=True)

    assert contract["chain_ready"] is True
    assert contract["customer_defect_delivery_ready"] is True
    assert contract["summary"]["passed_stage_count"] == 6
    assert contract["summary"]["first_blocked_stage"] == ""
    assert (output_dir / "main_chain_contract.json").exists()
    assert (workspace_dir / "main_chain_contract.json").exists()


def test_main_chain_contract_does_not_mark_candidates_as_customer_ready(tmp_path: Path) -> None:
    project = "demo"
    _, _, output_dir, _ = _seed_chain_until_evidence(tmp_path, project, validated_bug_count=0)
    _write_json(output_dir / "discovered_issues.json", {"items": [{"issue_id": "I1", "title": "候选线索", "severity": "P2"}]})
    _write_json(output_dir / "evidence_bundle.json", {"items": [_strict_evidence_item()]})
    _write_json(output_dir / "real_project_defect_data.json", {"candidate_issue_count": 1, "validated_bug_count": 0, "network_requests": 1})

    contract = evaluate_main_chain_contract(project, tmp_path, persist=False)

    assert contract["chain_ready"] is False
    assert contract["customer_defect_delivery_ready"] is False
    assert contract["summary"]["first_blocked_stage"] == "bug_discovery"
    bug_stage = next(stage for stage in contract["stages"] if stage["stage"] == "bug_discovery")
    assert bug_stage["status"] == "partial"
    assert "no_validated_bug" in bug_stage["blockers"]


def test_main_chain_contract_rejects_evidence_without_replay_and_execution_receipt(tmp_path: Path) -> None:
    project = "demo"
    _, _, output_dir, _ = _seed_chain_until_evidence(tmp_path, project)
    _write_json(output_dir / "evidence_bundle.json", {
        "items": [{
            "issue_id": "I1",
            "request": {"method": "GET", "url": "/api/orders/1"},
            "response": {"status_code": 200},
            "expected": "只能访问本人订单",
            "actual": "返回他人订单",
        }]
    })

    contract = evaluate_main_chain_contract(project, tmp_path, persist=False)

    assert contract["chain_ready"] is False
    assert contract["summary"]["first_blocked_stage"] == "evidence_chain"
    evidence_stage = next(stage for stage in contract["stages"] if stage["stage"] == "evidence_chain")
    assert evidence_stage["status"] == "partial"
    assert "missing_replay_or_reproduction" in evidence_stage["blockers"]
    assert "missing_execution_receipt" in evidence_stage["blockers"]
    assert "strict_evidence_not_linked_to_all_issues" in evidence_stage["blockers"]


def test_main_chain_contract_rejects_synthetic_evidence(tmp_path: Path) -> None:
    project = "demo"
    _, _, output_dir, _ = _seed_chain_until_evidence(tmp_path, project)
    synthetic = _strict_evidence_item()
    synthetic["reproduction"] = {"method": "GET", "path": "/api/orders/1", "is_synthetic": True}
    _write_json(output_dir / "evidence_bundle.json", {"items": [synthetic]})

    contract = evaluate_main_chain_contract(project, tmp_path, persist=False)

    assert contract["chain_ready"] is False
    evidence_stage = next(stage for stage in contract["stages"] if stage["stage"] == "evidence_chain")
    assert evidence_stage["status"] == "partial"
    assert "synthetic_evidence_present" in evidence_stage["blockers"]
    assert "strict_evidence_not_linked_to_all_issues" in evidence_stage["blockers"]


def test_main_chain_contract_requires_expected_and_actual_pair(tmp_path: Path) -> None:
    project = "demo"
    _, _, output_dir, _ = _seed_chain_until_evidence(tmp_path, project)
    incomplete = _strict_evidence_item()
    incomplete.pop("actual")
    _write_json(output_dir / "evidence_bundle.json", {"items": [incomplete]})

    contract = evaluate_main_chain_contract(project, tmp_path, persist=False)

    assert contract["chain_ready"] is False
    evidence_stage = next(stage for stage in contract["stages"] if stage["stage"] == "evidence_chain")
    assert evidence_stage["status"] == "partial"
    assert "missing_expected_actual_pair" in evidence_stage["blockers"]
    assert "strict_evidence_not_linked_to_all_issues" in evidence_stage["blockers"]
