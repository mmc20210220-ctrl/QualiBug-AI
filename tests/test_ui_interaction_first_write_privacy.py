from __future__ import annotations

import json

from ai_test_asset_center import formal_ui_surface
from ai_test_asset_center import formal_ui_surface_guard
from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
from ai_test_asset_center import professional_ui_readonly
from ai_test_asset_center import professional_ui_responsive_accessibility
from ai_test_asset_center.professional_ui_contract_guard import (
    install_professional_ui_contract_guard,
)
from ai_test_asset_center.professional_ui_interaction_contract_guard import (
    install_controlled_ui_interaction_contract_guard,
)
from ai_test_asset_center.professional_ui_interaction_privacy_guard import (
    EVIDENCE_POLICY,
    install_controlled_ui_interaction_privacy_guard,
)


def _install_privacy_chain() -> None:
    formal_ui_surface.install_formal_ui_surface()
    formal_ui_surface_guard.install_formal_ui_read_only_guard()
    professional_ui_readonly.install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    professional_ui_responsive_accessibility.install_professional_ui_responsive_accessibility()
    interaction.install_controlled_ui_interaction()
    install_controlled_ui_interaction_contract_guard()
    install_controlled_ui_interaction_privacy_guard()


def test_first_interaction_json_serialization_is_already_minimized() -> None:
    _install_privacy_chain()
    raw_result = {
        "status": "failed",
        "reason": "PlaywrightError: password=top-secret",
        "execution_mode": "approved_sandbox_write",
        "artifact_dir": "platform_workspace/p/browser_runs/r1",
        "cleanup_receipt": {
            "status": "INDETERMINATE",
            "reason_code": "UI_CLEANUP_EXECUTION_FAILED",
        },
        "steps": [],
        "console": [{"type": "error", "text": "token=top-secret"}],
        "network": [{
            "method": "POST",
            "status": 500,
            "url": "https://example.test/save?token=top-secret",
        }],
        "trace_ref": "trace.zip",
        "har_ref": "network.har",
        "duration_ms": 12,
    }

    serialized = interaction.json.dumps(raw_result, sort_keys=True)
    persisted = json.loads(serialized)

    assert "top-secret" not in serialized
    assert persisted["reason"].startswith("UI_INTERACTION_RUNTIME_ERROR:")
    assert persisted["trace_ref"] == ""
    assert persisted["har_ref"] == ""
    assert "text" not in persisted["console"][0]
    assert "url" not in persisted["network"][0]
    assert persisted["evidence_privacy"]["policy"] == EVIDENCE_POLICY
    assert persisted["evidence_privacy"][
        "first_persisted_artifact_minimized"
    ] is True


def test_json_proxy_does_not_rewrite_non_execution_fingerprinting_payloads() -> None:
    _install_privacy_chain()
    payload = {
        "execution_mode": "approved_sandbox_write",
        "status": "candidate",
        "value": "ordinary-source-value",
    }

    serialized = interaction.json.dumps(payload, sort_keys=True)

    assert json.loads(serialized) == payload
