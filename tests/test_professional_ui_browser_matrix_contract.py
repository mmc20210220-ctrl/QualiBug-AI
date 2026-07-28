from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center import (
    extract_formal_ui_contracts,
)
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_browser_matrix_guard import (
    AGGREGATION_POLICY,
    SCHEMA_VERSION,
    normalize_browser_matrix,
)


def _profile(
    profile_id: str,
    engine: str,
    *,
    device_class: str = "desktop",
    width: int = 1280,
    height: int = 720,
    is_mobile: bool = False,
    has_touch: bool = False,
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "browser_engine": engine,
        "device_class": device_class,
        "viewport_width": width,
        "viewport_height": height,
        "device_scale_factor": 1,
        "is_mobile": is_mobile,
        "has_touch": has_touch,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "color_scheme": "light",
        "reduced_motion": "no-preference",
    }


def _matrix(*profiles: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "profiles": list(profiles),
    }


def _request(*, steps: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "request_id": "matrix-orders",
        "title": "Orders browser matrix",
        "provider": "playwright_browser_plan",
        "start_url": "/orders",
        "execution_mode": "safe_read_only",
        "browser_matrix": _matrix(
            _profile("chromium-desktop", "chromium"),
            _profile("firefox-desktop", "firefox"),
            _profile(
                "webkit-mobile",
                "webkit",
                device_class="mobile",
                width=390,
                height=844,
                is_mobile=True,
                has_touch=True,
            ),
        ),
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": steps or [
                {"action": "goto", "url": "/orders"},
                {"action": "expect_visible", "selector": "[data-page=orders]"},
                {"action": "expect_no_horizontal_overflow", "tolerance_px": 0},
            ],
        },
    }


def _document(request: dict[str, object]) -> str:
    return json.dumps({
        "schema_version": "qualibug.ui-formal-contract.v2",
        "contract_id": "matrix-orders-contract",
        "operation_ref": "get-orders-page",
        "actor_role": "qa_viewer",
        "ui_request": request,
    })


def test_valid_three_engine_matrix_enters_formal_source_contract() -> None:
    contracts, gaps = extract_formal_ui_contracts(
        _document(_request()),
        source_id="src-ui-orders",
    )

    assert gaps == []
    assert len(contracts) == 1
    matrix = contracts[0]["ui_request"]["browser_matrix"]
    assert matrix["schema_version"] == SCHEMA_VERSION
    assert matrix["aggregation_policy"] == AGGREGATION_POLICY
    assert [row["browser_engine"] for row in matrix["profiles"]] == [
        "chromium",
        "firefox",
        "webkit",
    ]
    assert matrix["profiles"][2]["is_mobile"] is True
    assert matrix["profiles"][2]["has_touch"] is True


def test_duplicate_profile_identity_is_visible_source_gap() -> None:
    request = _request()
    request["browser_matrix"] = _matrix(
        _profile("desktop", "chromium"),
        _profile("desktop", "firefox"),
    )

    contracts, gaps = extract_formal_ui_contracts(
        _document(request),
        source_id="src-ui-orders",
    )

    assert contracts == []
    assert gaps[0]["reason_code"] == "FORMAL_UI_BROWSER_MATRIX_INCOMPLETE"
    assert gaps[0]["missing_requirements"] == [
        "browser_matrix.profile_id_duplicate"
    ]


def test_firefox_mobile_emulation_is_rejected_fail_closed() -> None:
    request = _request()
    request["browser_matrix"] = _matrix(
        _profile("chromium-desktop", "chromium"),
        _profile(
            "firefox-mobile",
            "firefox",
            device_class="mobile",
            width=390,
            height=844,
            is_mobile=True,
            has_touch=True,
        ),
    )

    contracts, gaps = extract_formal_ui_contracts(
        _document(request),
        source_id="src-ui-orders",
    )

    assert contracts == []
    assert gaps[0]["missing_requirements"] == [
        "browser_matrix.firefox_mobile_unsupported"
    ]


def test_visual_pixel_baseline_cannot_be_reused_across_engines() -> None:
    visual_step = {
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
    request = _request(steps=[
        {"action": "set_viewport", "width": 1280, "height": 720},
        {"action": "goto", "url": "/orders"},
        visual_step,
    ])

    contracts, gaps = extract_formal_ui_contracts(
        _document(request),
        source_id="src-ui-orders",
    )

    assert contracts == []
    assert gaps[0]["missing_requirements"] == [
        "browser_matrix.profile_specific_visual_baselines_required"
    ]


def test_matrix_normalizer_does_not_infer_missing_device_identity() -> None:
    raw = _matrix(
        _profile("chromium-desktop", "chromium"),
        {
            "profile_id": "webkit-mobile",
            "browser_engine": "webkit",
            "device_class": "mobile",
            "viewport_width": 390,
            "viewport_height": 844,
            "device_scale_factor": 3,
            "is_mobile": True,
            # has_touch is intentionally omitted: mobile identity cannot be guessed.
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "color_scheme": "light",
            "reduced_motion": "no-preference",
        },
    )

    try:
        normalize_browser_matrix(raw)
    except ValueError as exc:
        assert str(exc) == "browser_matrix.touch_device_requires_has_touch"
    else:  # pragma: no cover - a regression would make the test fail explicitly
        raise AssertionError("mobile matrix profile was accepted without has_touch")
