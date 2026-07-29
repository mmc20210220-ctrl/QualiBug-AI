from __future__ import annotations

from ai_test_asset_center.private_pilot_ui_upload_scenario_health_patch import (
    upload_scenario_health_status,
)
from ai_test_asset_center.ui_upload_scenario_public_projection import (
    install_ui_upload_scenario_public_projection,
)
from ai_test_asset_center.ui_upload_scenario_semantic_authority import (
    install_ui_upload_scenario_semantic_authority,
)


def test_upload_scenario_health_exposes_submission_and_business_cleanup_authority() -> None:
    install_ui_upload_scenario_semantic_authority()
    install_ui_upload_scenario_public_projection()

    status = upload_scenario_health_status()

    assert status["checks"]["submission_compensation_authority_installed"] is True
    assert status["checks"]["minimized_public_projection_installed"] is True
    assert status["governance"]["explicit_submission_mode_required"] is True
    assert status["governance"]["supported_submission_modes"] == [
        "auto_on_file_selection",
        "click_submit",
    ]
    assert status["governance"]["click_submit_selector_required"] is True
    assert status["governance"]["business_compensation_selector_required"] is True
    assert status["governance"]["clearing_file_input_is_business_cleanup"] is False
    assert status["governance"]["public_projection_contains_raw_selectors"] is False
    assert status["governance"]["public_projection_contains_assertion_text"] is False
    assert status["governance"]["public_projection_contains_probe_urls"] is False
