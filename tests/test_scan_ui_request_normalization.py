from __future__ import annotations

from ai_test_asset_center import formal_ui_surface
from ai_test_asset_center import formal_ui_surface_guard
from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
from ai_test_asset_center import professional_ui_readonly
from ai_test_asset_center import professional_ui_responsive_accessibility
from ai_test_asset_center import scan_ui_contract_overlay as overlay
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
from ai_test_asset_center.professional_ui_persistent_cleanup_probe import (
    EQUIVALENCE_SCOPE,
    PERSISTENT_PROBE_PROPERTY,
    install_persistent_ui_cleanup_probe,
)
from ai_test_asset_center.scan_ui_interaction_contract_guard import (
    install_scan_ui_interaction_contract_guard,
)


def _install() -> None:
    formal_ui_surface.install_formal_ui_surface()
    formal_ui_surface_guard.install_formal_ui_read_only_guard()
    professional_ui_readonly.install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    professional_ui_responsive_accessibility.install_professional_ui_responsive_accessibility()
    interaction.install_controlled_ui_interaction()
    install_controlled_ui_interaction_contract_guard()
    install_controlled_ui_interaction_privacy_guard()
    install_persistent_ui_cleanup_probe()
    install_scan_ui_interaction_contract_guard()


def test_scan_guard_keeps_normalized_write_mode_when_request_mode_is_omitted() -> None:
    _install()
    request = {
        "request_id": "ui-plan-mode-authority",
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/records",
        "operation_ref": "get-records",
        "actor_role": "public",
        "source_refs": [{
            "source_id": "ui-spec",
            "version": "v1",
            "locator": "workflow:records",
            "kind": "formal_ui_contract",
            "quote_hash": "a" * 64,
        }],
        # Request-level execution_mode is deliberately absent. The explicit plan
        # mode is authoritative and the parser normalizes it onto the request.
        "browser_plan": {
            "execution_mode": "approved_sandbox_write",
            "write_approved": True,
            "interaction_contract": {
                "cleanup_strategy": "browser_compensation",
                "equivalence": "source_declared_state_probes",
                "equivalence_scope": EQUIVALENCE_SCOPE,
                "target_scope": "approved_nonproduction_target",
                "evidence_policy": EVIDENCE_POLICY,
            },
            "state_probes": [{
                "probe_id": "count-persistent",
                "property": PERSISTENT_PROBE_PROPERTY,
                "method": "GET",
                "url": "/api/records/summary",
                "json_pointer": "/count",
                "expected_status_class": 2,
                "max_response_bytes": 100_000,
            }],
            "steps": [
                {"phase": "setup", "action": "goto", "url": "/records"},
                {"phase": "treatment", "action": "click", "selector": "#create"},
                {
                    "phase": "assertion",
                    "action": "expect_text",
                    "selector": "#result",
                    "text": "Created",
                },
                {"phase": "cleanup", "action": "click", "selector": "#remove"},
            ],
        },
    }

    asset, receipt = overlay.overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [request]},
    )

    assert receipt["status"] == "OVERLAID"
    normalized = asset["ui_formal_contracts"][0]["ui_request"]
    assert normalized["execution_mode"] == "approved_sandbox_write"
    assert normalized["browser_plan"]["execution_mode"] == (
        "approved_sandbox_write"
    )
