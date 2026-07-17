from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "ai_test_asset_center" / "private_pilot_service.py"
ADAPTER = ROOT / "ai_test_asset_center" / "command_center_delivery_contract.py"


def test_command_center_delivery_normalization_adapter_available() -> None:
    adapter_source = ADAPTER.read_text(encoding="utf-8")

    assert "def normalize_command_center_delivery" in adapter_source
    assert "split_customer_delivery_tracks" in adapter_source
    assert "data[\"defects\"] = defects" in adapter_source
    assert "data[\"clues\"] = all_clues" in adapter_source
    assert "data[\"risks\"] = defects" in adapter_source
    assert "_sync_command_center_counters" in adapter_source


def test_command_center_service_calls_existing_envelope_normalizer_before_response() -> None:
    service_source = SERVICE.read_text(encoding="utf-8")
    routing_source = (ROOT / "ai_test_asset_center" / "private_pilot_http_routing.py").read_text(encoding="utf-8")

    assert "def _normalize_command_center_envelope" in service_source
    assert "payload = _normalize_command_center_envelope(payload)" in routing_source
    assert "return self._json(payload)" in routing_source


def test_command_center_service_imports_the_canonical_gate_directly() -> None:
    from ai_test_asset_center import private_pilot_service
    from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks

    assert private_pilot_service._partition_delivery_tracks is split_customer_delivery_tracks


def test_command_center_normalizer_rechecks_prefilled_defects() -> None:
    from ai_test_asset_center.private_pilot_service import _normalize_command_center_envelope

    candidate = {
        "title": "Prefilled defect without traceable customer evidence",
        "bug_status": "reproduced",
        "gate_passed": True,
        "repro_method": "GET",
        "repro_path": "/api/resources",
        "reproduction": {
            "method": "GET",
            "path": "/api/resources",
            "is_synthetic": False,
            "har_evidence": {"status_code": 500},
        },
        "raw_evidence": {"has_real_evidence": True},
    }

    normalized = _normalize_command_center_envelope({
        "data": {"defects": [candidate], "clues": [], "risks": [candidate]},
    })

    assert normalized["data"]["defects"] == []
    assert normalized["data"]["risks"] == []
    assert normalized["data"]["clues"][0]["customer_delivery_gate_reasons"]
