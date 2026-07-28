from __future__ import annotations

from ai_test_asset_center.ui_execution_adapter import _plan_with_start_url


def test_start_url_is_inserted_after_deterministic_browser_configuration() -> None:
    plan = {
        "execution_mode": "safe_read_only",
        "steps": [
            {"action": "set_viewport", "width": 1280, "height": 720},
            {
                "action": "set_media",
                "color_scheme": "light",
                "reduced_motion": "reduce",
            },
            {"action": "expect_visual_baseline", "baseline_ref": "visual_baselines/a.png"},
        ],
    }

    normalized = _plan_with_start_url(plan, "/orders")

    assert normalized["steps"] == [
        {"action": "set_viewport", "width": 1280, "height": 720},
        {
            "action": "set_media",
            "color_scheme": "light",
            "reduced_motion": "reduce",
        },
        {"action": "goto", "url": "/orders"},
        {"action": "expect_visual_baseline", "baseline_ref": "visual_baselines/a.png"},
    ]
    assert plan["steps"][0] == {
        "action": "set_viewport",
        "width": 1280,
        "height": 720,
    }


def test_empty_existing_goto_receives_start_url_without_duplicate_navigation() -> None:
    plan = {
        "steps": [
            {"action": "set_viewport", "width": 1280, "height": 720},
            {"action": "goto"},
            {"action": "expect_text", "selector": "h1", "text": "Orders"},
        ],
    }

    normalized = _plan_with_start_url(plan, "/orders")

    assert normalized["steps"][1] == {"action": "goto", "url": "/orders"}
    assert sum(
        1 for step in normalized["steps"] if step.get("action") == "goto"
    ) == 1


def test_source_declared_goto_url_is_not_overwritten_by_request_start_url() -> None:
    plan = {
        "steps": [
            {"action": "set_viewport", "width": 1280, "height": 720},
            {"action": "goto", "url": "/source-authoritative-orders"},
            {"action": "expect_url", "pattern": "/orders"},
        ],
    }

    normalized = _plan_with_start_url(plan, "/request-fallback")

    assert normalized["steps"][1]["url"] == "/source-authoritative-orders"
    assert sum(
        1 for step in normalized["steps"] if step.get("action") == "goto"
    ) == 1


def test_blank_start_url_leaves_plan_unchanged() -> None:
    plan = {"steps": [{"action": "set_viewport", "width": 390, "height": 844}]}

    normalized = _plan_with_start_url(plan, "  ")

    assert normalized is plan
