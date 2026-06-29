from __future__ import annotations

from ai_test_asset_center.defect_family_registry import get_defect_family, iter_defect_families, resolve_defect_family


def test_defect_family_registry_exposes_full_spectrum_families() -> None:
    families = iter_defect_families()
    family_ids = {item["family_id"] for item in families}

    assert "api_contract" in family_ids
    assert "performance" in family_ids
    assert "stability" in family_ids
    assert "compatibility" in family_ids
    assert "ui" in family_ids
    assert "uiux" in family_ids


def test_resolve_defect_family_maps_runtime_signals() -> None:
    assert resolve_defect_family({"risk_type": "api_contract"})["family_id"] == "api_contract"
    assert resolve_defect_family({"title": "页面渲染失败"})["family_id"] == "ui"
    assert resolve_defect_family({"title": "timeout spike detected"})["family_id"] == "performance"
    assert get_defect_family("uiux")["display_name"] == "UIUX Bug"

