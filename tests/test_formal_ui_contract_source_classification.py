from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center import (
    ingest_enterprise_knowledge_documents,
)
from ai_test_asset_center.enterprise_knowledge_center import _crud, _parsing


def _contract_document() -> dict[str, object]:
    return {
        "schema_version": "qualibug.ui-formal-contract.v2",
        "ui_formal_contracts": [
            {
                "contract_id": "visual-orders",
                "title": "Orders visual baseline",
                "operation_ref": "get-orders-page",
                "actor_role": "public",
                "ui_request": {
                    "request_id": "visual-orders",
                    "title": "Orders visual baseline",
                    "provider": "playwright_browser_plan",
                    "start_url": "/orders",
                    "execution_mode": "safe_read_only",
                    "browser_plan": {
                        "execution_mode": "safe_read_only",
                        "steps": [
                            {
                                "action": "set_viewport",
                                "width": 1280,
                                "height": 720,
                            },
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
                },
            }
        ],
    }


def test_explicit_formal_contract_json_is_primary_uiux_spec() -> None:
    text = json.dumps(_contract_document())

    assert _parsing._classify_source(
        "orders-visual-contract.json",
        text,
    ) == "uiux_spec"
    assert _parsing._classify_source_multi(
        "orders-visual-contract.json",
        text,
    )[0] == "uiux_spec"
    assert _crud._classify_source(
        "orders-visual-contract.json",
        text,
    ) == "uiux_spec"


def test_ordinary_json_is_not_promoted_to_executable_ui_contract() -> None:
    text = json.dumps({"orders": [{"id": 1, "status": "created"}]})

    assert _parsing._classify_source(
        "orders.json",
        text,
    ) != "uiux_spec"


def test_formal_contract_json_ingestion_runs_ui_parser_without_filename_markers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "orders-contract.json"
    source.write_text(
        json.dumps(_contract_document()),
        encoding="utf-8",
    )

    result = ingest_enterprise_knowledge_documents(
        "visual-project",
        [{"file_path": str(source)}],
        root=tmp_path,
        actor={"name": "alice", "role": "qa_lead"},
    )

    assert result["ok"] is True
    assert len(result["created"]) == 1
    record = result["created"][0]
    assert record["source_type"] == "uiux_spec"
    assert record["parse"]["parse_status"] == "parsed"
    assert record["parse"]["ui_spec_count"] >= 2
    assert record["parse"]["errors"] == []
