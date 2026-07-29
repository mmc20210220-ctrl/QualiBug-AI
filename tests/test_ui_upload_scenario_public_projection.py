from __future__ import annotations

import json

from ai_test_asset_center import ui_upload_scenario_registry as registry
from ai_test_asset_center.ui_upload_scenario_public_projection import (
    install_ui_upload_scenario_public_projection,
)


def test_public_upload_scenario_summary_excludes_contract_secrets() -> None:
    install_ui_upload_scenario_public_projection()

    projected = registry._public_record({
        "scenario_id": "uisc_0123456789abcdef0123",
        "scenario_ref": "uisr_0123456789abcdef0123",
        "title": "客户上传",
        "status": "active",
        "authority": "approved_copy",
        "source_id": "src-upload",
        "fixture_binding_refs": ["uifb_0123456789abcdef0123"],
        "contract": {
            "actor_role": "admin",
            "safe_prerequisite_operation": {"method": "GET"},
            "submission_contract": {
                "mode": "click_submit",
                "submit_selector": "#private-submit",
                "cleanup_action": "click",
                "cleanup_selector": "#private-delete",
                "persistent_compensation_required": True,
            },
            "ui_request": {
                "metadata": {
                    "upload_submission_mode": "click_submit",
                    "upload_persistent_compensation_required": True,
                    "prerequisite_method": "GET",
                },
                "browser_plan": {
                    "state_probes": [{"url": "/private/state"}],
                    "steps": [{"selector": "#private-file", "text": "private success"}],
                },
            },
        },
    })

    assert projected["submission_mode"] == "click_submit"
    assert projected["business_cleanup_required"] is True
    assert projected["cleanup_action"] == "click"
    assert projected["safe_prerequisite_method"] == "GET"
    assert projected["actor_role"] == "admin"
    assert projected["raw_selectors_included"] is False
    assert projected["raw_assertion_text_included"] is False
    assert projected["raw_probe_urls_included"] is False
    serialized = json.dumps(projected, ensure_ascii=False)
    assert "#private-submit" not in serialized
    assert "#private-delete" not in serialized
    assert "#private-file" not in serialized
    assert "/private/state" not in serialized
    assert "private success" not in serialized
