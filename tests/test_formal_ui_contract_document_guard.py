from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center import (
    extract_formal_ui_contracts,
)
from ai_test_asset_center.scan_ui_contract_overlay import overlay_scan_ui_contracts


def _visual_step() -> dict[str, object]:
    return {
        "action": "expect_visual_baseline",
        "baseline_ref": "visual_baselines/orders__123456789abc.png",
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
    }


def _ui_request() -> dict[str, object]:
    return {
        "request_id": "visual-orders",
        "title": "Orders visual baseline",
        "provider": "playwright_browser_plan",
        "start_url": "/orders",
        "execution_mode": "safe_read_only",
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "set_viewport", "width": 1280, "height": 720},
                {"action": "goto", "url": "/orders"},
                _visual_step(),
            ],
        },
    }


def test_formal_contract_document_container_does_not_create_phantom_gap() -> None:
    document = {
        "schema_version": "qualibug.ui-formal-contract.v2",
        "ui_formal_contracts": [
            {
                "contract_id": "visual-orders",
                "title": "Orders visual baseline",
                "operation_ref": "get-orders-page",
                "actor_role": "public",
                "ui_request": _ui_request(),
            }
        ],
    }

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps(document),
        source_id="src-ui-orders",
    )

    assert len(contracts) == 1
    assert contracts[0]["contract_id"] == "visual-orders"
    assert contracts[0]["source_id"] == "src-ui-orders"
    assert contracts[0]["ui_request"]["browser_plan"]["steps"][2][
        "action"
    ] == "expect_visual_baseline"
    assert gaps == []


def test_standalone_formal_contract_schema_remains_supported() -> None:
    standalone = {
        "schema_version": "qualibug.ui-formal-contract.v2",
        "contract_id": "visual-orders-standalone",
        "operation_ref": "get-orders-page",
        "actor_role": "public",
        "ui_request": _ui_request(),
    }

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps(standalone),
        source_id="src-ui-orders",
    )

    assert len(contracts) == 1
    assert contracts[0]["contract_id"] == "visual-orders-standalone"
    assert gaps == []


def test_direct_scan_visual_request_shape_enters_existing_overlay() -> None:
    request = {
        **_ui_request(),
        "operation_ref": "get-orders-page",
        "actor_role": "public",
        "source_refs": [
            {
                "source_id": "src-ui-orders",
                "locator": "screen:orders",
                "kind": "formal_ui_contract",
            }
        ],
    }

    asset, receipt = overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [request]},
    )

    assert receipt["status"] == "OVERLAID"
    assert receipt["contract_added_count"] == 1
    assert receipt["coverage_gap_count"] == 0
    contract = asset["ui_formal_contracts"][0]
    assert contract["operation_ref"] == "get-orders-page"
    assert contract["actor_role"] == "public"
    assert contract["source_refs"][0]["source_id"] == "src-ui-orders"
