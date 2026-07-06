from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import private_pilot_service
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


def test_qualibug_server_entrypoint_uses_gate_patch_wrapper() -> None:
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    wrapper = SERVER_ENTRYPOINT.read_text(encoding="utf-8")

    assert 'qualibug-server = "ai_test_asset_center.private_pilot_server:run_server"' in pyproject
    assert "install_customer_delivery_gate_patch" in wrapper
    assert "customer_delivery_gate_patch_status" in wrapper
    assert "restore_customer_delivery_gate_patch" in wrapper
    assert "split_customer_delivery_tracks" in wrapper
    assert "_inject_delivery_gate_patch_status" in wrapper
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
