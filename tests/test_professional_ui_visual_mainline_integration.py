from __future__ import annotations

from typing import Any

import pytest

from ai_test_asset_center import formal_ui_surface
from ai_test_asset_center import formal_ui_surface_guard
from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
from ai_test_asset_center import professional_ui_readonly
from ai_test_asset_center import professional_ui_responsive_accessibility
from ai_test_asset_center import professional_ui_visual_baseline as visual
from ai_test_asset_center import scan_ui_contract_overlay as overlay
from ai_test_asset_center.observer_contracts_base import _receipt
from ai_test_asset_center.professional_ui_contract_guard import (
    install_professional_ui_contract_guard,
)
from ai_test_asset_center.professional_ui_coverage_projection import (
    build_professional_ui_coverage,
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
from ai_test_asset_center.professional_ui_visual_baseline_governance import (
    install_visual_baseline_governance,
)
from ai_test_asset_center.professional_ui_visual_determinism_guard import (
    FONT_READINESS,
    RENDERER_PROFILE,
    SCROLL_ORIGIN,
    install_visual_determinism_guard,
)
from ai_test_asset_center.professional_ui_visual_image_guard import (
    install_visual_image_guard,
)
from ai_test_asset_center.professional_ui_visual_viewport_guard import (
    install_visual_viewport_guard,
)
from ai_test_asset_center.scan_ui_interaction_contract_guard import (
    install_scan_ui_interaction_contract_guard,
)

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720


def _install_full_ui_chain() -> None:
    formal_ui_surface.install_formal_ui_surface()
    formal_ui_surface_guard.install_formal_ui_read_only_guard()
    professional_ui_readonly.install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    professional_ui_responsive_accessibility.install_professional_ui_responsive_accessibility()
    visual.install_professional_ui_visual_baseline()
    install_visual_baseline_governance()
    install_visual_image_guard()
    install_visual_determinism_guard()
    interaction.install_controlled_ui_interaction()
    install_controlled_ui_interaction_contract_guard()
    install_controlled_ui_interaction_privacy_guard()
    install_persistent_ui_cleanup_probe()
    install_visual_viewport_guard()
    install_scan_ui_interaction_contract_guard()


def _viewport_step(*, phase: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "action": "set_viewport",
        "width": VIEWPORT_WIDTH,
        "height": VIEWPORT_HEIGHT,
    }
    if phase:
        row["phase"] = phase
    return row


def _visual_step() -> dict[str, Any]:
    return {
        "action": visual.ACTION,
        "baseline_ref": "visual_baselines/orders.png",
        "baseline_sha256": "a" * 64,
        "max_changed_pixel_ratio": 0.001,
        "channel_tolerance": 2,
        "full_page": False,
        "animations_disabled": True,
        "renderer_profile": RENDERER_PROFILE,
        "scroll_origin": SCROLL_ORIGIN,
        "font_readiness": FONT_READINESS,
        "viewport_width": VIEWPORT_WIDTH,
        "viewport_height": VIEWPORT_HEIGHT,
        "mask_selectors": ["[data-testid=clock]"],
        "mask_locator_intents": [],
        "mask_regions": [],
    }


def _source_ref() -> dict[str, Any]:
    return {
        "source_id": "ui-design-orders",
        "version": "v1",
        "locator": "screen:orders",
        "kind": "formal_ui_contract",
        "quote_hash": "b" * 64,
    }


def test_direct_scan_visual_contract_reaches_formal_ui_overlay_and_compiler() -> None:
    _install_full_ui_chain()
    request = {
        "request_id": "visual-orders-readonly",
        "title": "Orders visual baseline",
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/orders",
        "execution_mode": "safe_read_only",
        "operation_ref": "get-orders-page",
        "actor_role": "public",
        "source_refs": [_source_ref()],
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/orders"},
                _viewport_step(),
                _visual_step(),
            ],
        },
    }

    asset, receipt = overlay.overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [request]},
    )

    assert receipt["status"] == "OVERLAID"
    assert receipt["contract_added_count"] == 1
    assert receipt["coverage_gap_count"] == 0
    contract = asset["ui_formal_contracts"][0]
    steps = contract["ui_request"]["browser_plan"]["steps"]
    assert [row["action"] for row in steps] == [
        "goto",
        "set_viewport",
        visual.ACTION,
    ]

    compiled = formal_ui_surface._compile_ui_protocol({
        "property_spec": {
            "ui_request": contract["ui_request"],
            "actor_ref": "actor-public",
        },
        "operation_ref": "get-orders-page",
        "treatment_actor_ref": "actor-public",
    })

    assert compiled["status"] == "COMPILED"
    assert compiled["assertion"]["kind"] == formal_ui_surface.ASSERTION_KIND
    assert compiled["assertion"]["ui_expectation_count"] == 1


def test_visual_and_cleanup_evidence_survive_one_observer_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_full_ui_chain()
    visual_observation = {
        "expectation": visual.ACTION,
        "status": "VIOLATION_OBSERVED",
        "reason_code": "UI_VISUAL_CHANGED_PIXEL_BUDGET_EXCEEDED",
        "changed_pixel_ratio": 0.02,
        "raw_pixels_in_receipt": False,
        "ai_visual_judgement_used": False,
    }
    cleanup_receipt = {
        "schema_version": interaction.CLEANUP_RECEIPT_SCHEMA,
        "receipt_id": "cleanup-accepted",
        "status": "ACCEPTED",
        "reason_code": "",
        "raw_state_included": False,
    }

    def fake_base_observer(envelope: dict[str, Any]) -> dict[str, Any]:
        visual._append_observation(visual_observation)
        interaction._LAST_CLEANUP_CONTEXT.set({
            "cleanup_receipt": cleanup_receipt,
            "interaction_count": 1,
            "cleanup_interaction_count": 1,
        })
        return _receipt(
            observer_id=formal_ui_surface.OBSERVER_ID,
            status="OBSERVED",
            evidence={
                formal_ui_surface.EVIDENCE_KEY: {
                    "expectation_satisfied": False,
                    "violation_observed": True,
                },
            },
        )

    monkeypatch.setattr(
        formal_ui_surface,
        visual._ORIGINAL_OBSERVER,
        fake_base_observer,
    )
    monkeypatch.setattr(
        formal_ui_surface,
        interaction.ORIGINAL_OBSERVER,
        visual._observer_with_visual_receipts,
    )

    receipt = interaction._observer_with_cleanup_evidence({})
    evidence = receipt["evidence"][formal_ui_surface.EVIDENCE_KEY]

    assert evidence["visual_baseline_observations"] == [visual_observation]
    assert evidence["visual_ai_judgement_consumed"] is False
    assert evidence["cleanup_receipt"] == cleanup_receipt
    assert evidence["cleanup_equivalence_accepted"] is True
    assert evidence["interaction_count"] == 1
    assert evidence["cleanup_interaction_count"] == 1


def test_interactive_visual_violation_is_suppressed_when_cleanup_is_unproven() -> None:
    _install_full_ui_chain()
    obligation_id = "interactive-visual-obligation"
    plan = {
        "execution_mode": "approved_sandbox_write",
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
            "url": "/api/orders/summary",
            "json_pointer": "/count",
        }],
        "steps": [
            {"phase": "setup", "action": "goto", "url": "/orders"},
            _viewport_step(phase="setup"),
            {"phase": "treatment", "action": "click", "selector": "#create"},
            {"phase": "assertion", **_visual_step()},
            {"phase": "cleanup", "action": "click", "selector": "#remove"},
        ],
    }
    result = {
        "test_obligations": {
            "obligations": [{
                "obligation_id": obligation_id,
                "risk_family": formal_ui_surface.RISK_FAMILY,
                "property": {
                    "ui_cleanup_authority": {"equivalence_required": True},
                    "ui_request": {"browser_plan": plan},
                },
            }],
        },
        "experiment_execution": {
            "results": {
                obligation_id: {
                    "obligation_id": obligation_id,
                    "observer_receipts": [{
                        "observer_id": formal_ui_surface.OBSERVER_ID,
                        "status": "OBSERVED",
                        "evidence": {
                            formal_ui_surface.EVIDENCE_KEY: {
                                "cleanup_receipt": {
                                    "status": "INDETERMINATE",
                                    "reason_code": (
                                        "UI_CLEANUP_EQUIVALENCE_MISMATCH"
                                    ),
                                },
                                "visual_baseline_observations": [{
                                    "status": "VIOLATION_OBSERVED",
                                    "reason_code": (
                                        "UI_VISUAL_CHANGED_PIXEL_BUDGET_EXCEEDED"
                                    ),
                                    "dimension_match": True,
                                    "changed_pixel_ratio": 0.02,
                                    "declared_viewport": {
                                        "width": VIEWPORT_WIDTH,
                                        "height": VIEWPORT_HEIGHT,
                                    },
                                    "actual_viewport": {
                                        "width": VIEWPORT_WIDTH,
                                        "height": VIEWPORT_HEIGHT,
                                    },
                                    "viewport_match": True,
                                    "ai_visual_judgement_used": False,
                                }],
                            },
                        },
                    }],
                    "oracle_verdict": {"status": "VIOLATION"},
                },
            },
        },
        "obligation_attempt_ledger": {
            "attempts": [{
                "obligation_id": obligation_id,
                "risk_family": formal_ui_surface.RISK_FAMILY,
                "terminal_status": "DELIVERABLE",
                "reason_code": "",
            }],
        },
    }

    coverage = build_professional_ui_coverage(result)

    visual_dimension = coverage["dimensions"]["visual_regression"]
    workflow_dimension = coverage["dimensions"]["workflow_interaction"]
    for row in (visual_dimension, workflow_dimension):
        assert row["violation_count"] == 0
        assert row["deliverable_count"] == 0
        assert row["blocked_or_indeterminate_count"] == 1
        assert row["cleanup_equivalence_indeterminate_count"] == 1
    invariant = coverage["cleanup_delivery_invariant"]
    assert invariant["invalid_oracle_without_cleanup_count"] == 1
    assert invariant["invalid_deliverable_without_cleanup_count"] == 1
