from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center import private_pilot_server, private_pilot_service
from ai_test_asset_center.private_pilot_server import (
    customer_delivery_gate_patch_status,
    install_customer_delivery_gate_patch,
    restore_customer_delivery_gate_patch,
)


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
SERVER_ENTRYPOINT = ROOT / "ai_test_asset_center" / "private_pilot_server.py"


def _legacy_ready_but_business_unvalidated() -> dict:
    return {
        "id": "LEGACY-1",
        "title": "旧轻门控会误放行的结果",
        "bug_status": "reproduced",
        "gate_passed": True,
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-07-06T12:00:00Z",
            "request_raw": {"method": "POST", "path": "/api/payments"},
            "response_raw": {"status_code": 200, "body": {"paid_amount": 1}},
        },
        "reproduction": {
            "method": "POST",
            "path": "/api/payments",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"paid_amount": 1}},
        },
        "expected": "订单金额应等于支付金额",
        "actual": "订单金额 100，支付金额 1",
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_qualibug_server_entrypoint_uses_gate_patch_wrapper() -> None:
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    wrapper = SERVER_ENTRYPOINT.read_text(encoding="utf-8")

    assert 'qualibug-server = "ai_test_asset_center.private_pilot_server:run_server"' in pyproject
    assert "install_customer_delivery_gate_patch" in wrapper
    assert "customer_delivery_gate_patch_status" in wrapper
    assert "restore_customer_delivery_gate_patch" in wrapper
    assert "split_customer_delivery_tracks" in wrapper
    assert "_inject_delivery_gate_patch_status" in wrapper
    assert "_inject_evidence_normalization_report" in wrapper
    assert "evidence_bundle_normalization_report" in wrapper
    assert "_inject_main_chain_contract" in wrapper
    assert "_apply_main_chain_readiness_guard" in wrapper
    assert "MAIN_CHAIN_NOT_READY" in wrapper
    assert "main_chain_contract" in wrapper
    assert "customer_delivery_gate_patch" in wrapper
    assert "_ORIGINAL_PARTITION_DELIVERY_TRACKS" in wrapper
    assert "_ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE" in wrapper
    assert "_CUSTOMER_DELIVERY_GATE_PATCH_SOURCE" in wrapper
    assert "_service.run_server()" in wrapper


def test_private_pilot_server_patch_routes_legacy_partition_through_strict_gate() -> None:
    restore_customer_delivery_gate_patch()
    install_customer_delivery_gate_patch()

    defects, clues = private_pilot_service._partition_delivery_tracks([_legacy_ready_but_business_unvalidated()])
    status = customer_delivery_gate_patch_status()

    assert defects == []
    assert [item["id"] for item in clues] == ["LEGACY-1"]
    assert "BUSINESS_EVIDENCE_NOT_VALIDATED" in clues[0]["customer_delivery_gate_reasons"]
    assert clues[0]["customer_visible"] is False
    assert status["patched"] is True
    assert status["source"] == "ai_test_asset_center.private_pilot_server"
    assert status["has_original_partition"] is True
    assert status["has_original_normalizer"] is True
    assert status["active_partition_name"] == "_strict_partition_delivery_tracks"
    assert status["active_normalizer_name"] == "_strict_normalize_command_center_envelope"


def test_private_pilot_server_patch_exposes_status_in_command_center_payload() -> None:
    restore_customer_delivery_gate_patch()
    install_customer_delivery_gate_patch()

    payload = private_pilot_service._normalize_command_center_envelope({
        "ok": True,
        "data": {
            "risks": [_legacy_ready_but_business_unvalidated()],
            "data_contract": {},
            "delivery_tracks": {},
        },
    })
    status = payload["customer_delivery_gate_patch"]

    assert status["patched"] is True
    assert status["source"] == "ai_test_asset_center.private_pilot_server"
    assert payload["data"]["customer_delivery_gate_patch"] == status
    assert payload["data"]["data_contract"]["customer_delivery_gate_patch"] == status
    assert payload["data"]["delivery_tracks"]["customer_delivery_gate_patch"] == status


def test_private_pilot_server_patch_exposes_evidence_normalization_report(monkeypatch, tmp_path: Path) -> None:
    project = "demo"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    report = {
        "phase": "evidence_bundle_normalization",
        "project_id": project,
        "input_item_count": 2,
        "output_item_count": 2,
        "fully_normalized_count": 1,
        "items": [
            {"issue_id": "I1", "normalized": True, "missing_fields": []},
            {"issue_id": "I2", "normalized": False, "missing_fields": ["actual", "execution_receipt"]},
        ],
    }
    _write_json(output_dir / "evidence_bundle_normalization_report.json", report)
    monkeypatch.setattr(private_pilot_server, "ROOT", tmp_path)
    restore_customer_delivery_gate_patch()
    install_customer_delivery_gate_patch()

    payload = private_pilot_service._normalize_command_center_envelope({
        "ok": True,
        "project_id": project,
        "data": {"project_id": project, "risks": [], "data_contract": {}, "delivery_tracks": {}, "executive_summary": {}},
    })
    summary = payload["evidence_bundle_normalization_summary"]

    assert payload["evidence_bundle_normalization_report"] == report
    assert payload["data"]["evidence_bundle_normalization_report"] == report
    assert payload["data"]["evidence_bundle_normalization_summary"] == summary
    assert payload["data"]["data_contract"]["evidence_bundle_normalization_summary"] == summary
    assert payload["data"]["delivery_tracks"]["evidence_bundle_normalization_summary"] == summary
    assert payload["data"]["executive_summary"]["evidence_bundle_normalization_summary"] == summary
    assert payload["data"]["executive_summary"]["evidence_fully_normalized_count"] == 1
    assert payload["data"]["executive_summary"]["evidence_blocked_item_count"] == 1
    assert summary["fully_normalized_count"] == 1
    assert summary["blocked_item_count"] == 1
    assert summary["missing_fields"] == {"actual": 1, "execution_receipt": 1}


def test_private_pilot_server_patch_exposes_main_chain_contract_in_command_center_payload(monkeypatch, tmp_path: Path) -> None:
    project = "demo"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    contract = {
        "project_id": project,
        "chain_ready": False,
        "customer_defect_delivery_ready": False,
        "summary": {
            "passed_stage_count": 3,
            "partial_stage_count": 1,
            "missing_stage_count": 2,
            "first_blocked_stage": "execution",
            "first_blocked_next_action": "补齐 base_url、测试账号和真实执行回执。",
        },
        "stages": [{"stage": "execution", "status": "missing"}],
    }
    _write_json(output_dir / "main_chain_contract.json", contract)
    monkeypatch.setattr(private_pilot_server, "ROOT", tmp_path)
    restore_customer_delivery_gate_patch()
    install_customer_delivery_gate_patch()

    payload = private_pilot_service._normalize_command_center_envelope({
        "ok": True,
        "project_id": project,
        "data": {"project_id": project, "risks": [], "data_contract": {}, "delivery_tracks": {}, "executive_summary": {}},
    })
    summary = payload["main_chain_contract_summary"]

    assert payload["main_chain_contract"] == contract
    assert payload["data"]["main_chain_contract"] == contract
    assert payload["data"]["main_chain_contract_summary"] == summary
    assert payload["data"]["data_contract"]["main_chain_contract"] == summary
    assert payload["data"]["delivery_tracks"]["main_chain_contract"] == summary
    assert payload["data"]["executive_summary"]["main_chain_ready"] is False
    assert payload["data"]["executive_summary"]["main_chain_first_blocked_stage"] == "execution"
    assert summary["first_blocked_next_action"] == "补齐 base_url、测试账号和真实执行回执。"


def test_private_pilot_server_main_chain_guard_blocks_false_delivery_readiness(monkeypatch, tmp_path: Path) -> None:
    project = "demo"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    contract = {
        "project_id": project,
        "chain_ready": False,
        "customer_defect_delivery_ready": False,
        "summary": {
            "passed_stage_count": 2,
            "partial_stage_count": 2,
            "missing_stage_count": 2,
            "first_blocked_stage": "execution",
            "first_blocked_next_action": "补齐真实执行回执。",
        },
    }
    _write_json(output_dir / "main_chain_contract.json", contract)
    monkeypatch.setattr(private_pilot_server, "ROOT", tmp_path)
    restore_customer_delivery_gate_patch()
    install_customer_delivery_gate_patch()

    payload = private_pilot_service._normalize_command_center_envelope({
        "ok": True,
        "project_id": project,
        "customer_defect_delivery_ready": True,
        "data": {
            "project_id": project,
            "risks": [],
            "customer_defect_delivery_ready": True,
            "scan_meta": {"customer_defect_delivery_ready": True},
            "value_metrics": {"customer_defect_delivery_ready": True},
            "data_contract": {"customer_defect_delivery_ready": True},
            "delivery_tracks": {"customer_defect_delivery_ready": True},
            "executive_summary": {
                "release_ready": True,
                "customer_delivery_ready": True,
                "customer_defect_delivery_ready": True,
            },
        },
    })
    data = payload["data"]

    assert payload["customer_defect_delivery_ready"] is False
    assert "MAIN_CHAIN_NOT_READY" in payload["delivery_blockers"]
    assert data["customer_defect_delivery_ready"] is False
    assert "MAIN_CHAIN_NOT_READY" in data["delivery_blockers"]
    for key in ("scan_meta", "value_metrics", "data_contract", "delivery_tracks"):
        assert data[key]["customer_defect_delivery_ready"] is False
        assert data[key]["main_chain_ready"] is False
        assert "MAIN_CHAIN_NOT_READY" in data[key]["delivery_blockers"]
    assert data["executive_summary"]["release_ready"] is False
    assert data["executive_summary"]["customer_delivery_ready"] is False
    assert data["executive_summary"]["customer_defect_delivery_ready"] is False
    assert data["executive_summary"]["main_chain_first_blocked_stage"] == "execution"
    assert data["executive_summary"]["delivery_readiness_label"] == "主链路未闭合，禁止声明客户交付就绪"
    assert data["main_chain_delivery_blocker"]["reason"] == "MAIN_CHAIN_NOT_READY"


def test_private_pilot_server_patch_can_restore_original_partition_and_normalizer_for_diagnostics() -> None:
    install_customer_delivery_gate_patch()
    assert customer_delivery_gate_patch_status()["patched"] is True

    restore_customer_delivery_gate_patch()
    status = customer_delivery_gate_patch_status()

    assert status["patched"] is False
    assert status["source"] == ""
    assert status["has_original_partition"] is False
    assert status["has_original_normalizer"] is False
    assert status["active_partition_name"] == "_partition_delivery_tracks"
    assert status["active_normalizer_name"] == "_normalize_command_center_envelope"
