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

    assert "def _normalize_command_center_envelope" in service_source
    assert "payload = _normalize_command_center_envelope(payload)" in service_source
    assert "return self._json(payload)" in service_source


def test_command_center_service_normalizer_is_the_remaining_gate_integration_point() -> None:
    service_source = SERVICE.read_text(encoding="utf-8")

    assert "def _partition_delivery_tracks" in service_source
    assert "def _normalize_command_center_envelope" in service_source
    assert "def _is_customer_delivery_risk" in service_source
