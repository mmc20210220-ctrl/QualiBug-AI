from __future__ import annotations

import json

from ai_test_asset_center import discovery_runtime_semantic_binding as runtime_binding
from ai_test_asset_center import scan_ui_contract_overlay as overlay


def _request(*source_ids: str) -> dict[str, object]:
    return {
        "request_id": "visual-orders-source-join",
        "title": "Orders visual source join",
        "provider": "playwright_browser_plan",
        "start_url": "/orders",
        "execution_mode": "safe_read_only",
        "operation_ref": "get-orders-page",
        "actor_role": "public",
        "source_refs": [
            {
                "source_id": source_id,
                "locator": "screen:orders",
                "kind": "formal_ui_contract",
            }
            for source_id in source_ids
        ],
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "set_viewport", "width": 1280, "height": 720},
                {"action": "goto", "url": "/orders"},
                {
                    "action": "expect_visual_baseline",
                    "baseline_ref": (
                        "visual_baselines/orders__123456789abc.png"
                    ),
                    "baseline_sha256": "a" * 64,
                    "max_changed_pixel_ratio": 0.001,
                    "channel_tolerance": 2,
                    "full_page": False,
                    "animations_disabled": True,
                    "renderer_profile": "chromium_css_scale_v1",
                    "scroll_origin": "document_start",
                    "font_readiness": "document_fonts_ready",
                    "viewport_width": 1280,
                    "viewport_height": 720,
                    "mask_selectors": [],
                    "mask_locator_intents": [],
                    "mask_regions": [],
                },
            ],
        },
    }


def test_discovery_runtime_captures_guarded_overlay_alias() -> None:
    assert runtime_binding.overlay_scan_ui_contracts is (
        overlay.overlay_scan_ui_contracts
    )
    assert getattr(
        overlay,
        "_qualibug_scan_ui_source_registry_guard_installed",
        False,
    ) is True


def test_bound_scan_context_is_joined_to_enterprise_source_registry() -> None:
    token = overlay.bind_scan_ui_contract_context({
        "ui_execution_requests": [_request("src-ui-orders")],
    })
    try:
        asset, receipt = overlay.overlay_scan_ui_contracts({
            "sources": [
                {"source_id": "src-ui-orders", "status": "active"},
            ]
        })
    finally:
        overlay.reset_scan_ui_contract_context(token)

    assert receipt["contract_added_count"] == 1
    assert receipt["coverage_gap_count"] == 0
    guard = receipt["source_registry_guard"]
    assert guard["status"] == "ACCEPTED"
    assert guard["trusted_source_count"] == 1
    assert guard["rejected_request_count"] == 0
    assert asset["ui_formal_contracts"][0]["source_refs"][0][
        "source_id"
    ] == "src-ui-orders"


def test_mixed_known_and_unknown_source_refs_lose_formal_authority() -> None:
    asset, receipt = overlay.overlay_scan_ui_contracts(
        {
            "sources": [
                {"source_id": "src-ui-orders", "status": "active"},
            ]
        },
        {
            "ui_execution_requests": [
                _request("src-ui-orders", "src-invented-orders")
            ]
        },
    )

    assert receipt["contract_added_count"] == 0
    assert receipt["coverage_gap_count"] == 1
    assert asset.get("ui_formal_contracts", []) == []
    guard = receipt["source_registry_guard"]
    assert guard["status"] == "REJECTED"
    assert guard["rejected_request_count"] == 1
    assert guard["rejections"][0]["unknown_source_count"] == 1
    serialized = json.dumps(receipt)
    assert "src-ui-orders" not in serialized
    assert "src-invented-orders" not in serialized
