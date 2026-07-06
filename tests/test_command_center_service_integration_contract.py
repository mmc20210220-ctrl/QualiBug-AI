from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "ai_test_asset_center" / "private_pilot_service.py"
ADAPTER = ROOT / "ai_test_asset_center" / "command_center_delivery_contract.py"


def test_command_center_service_has_delivery_normalization_adapter_available() -> None:
    adapter_source = ADAPTER.read_text(encoding="utf-8")

    assert "def normalize_command_center_delivery" in adapter_source
    assert "split_customer_delivery_tracks" in adapter_source
    assert "data[\"defects\"] = defects" in adapter_source
    assert "data[\"clues\"] = all_clues" in adapter_source
    assert "data[\"risks\"] = defects" in adapter_source


def test_command_center_service_must_call_delivery_normalization_before_response() -> None:
    service_source = SERVICE.read_text(encoding="utf-8")

    assert "command-center" in service_source
    assert "normalize_command_center_delivery" in service_source, (
        "private_pilot_service.py must call normalize_command_center_delivery() "
        "as the final command-center response step so legacy defects/risks/findings "
        "cannot bypass the backend customer-delivery gate."
    )
