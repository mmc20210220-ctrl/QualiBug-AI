from __future__ import annotations

import pytest

from ai_test_asset_center.browser_execution import BrowserExecutionError, validate_browser_plan


CONTRACT = {
    "status": "approved",
    "approved_base_url": "https://test.example.invalid",
    "source_manifest": {"source_id": "ui-map", "source_hash": "a" * 64},
}


def test_safe_read_only_browser_plan_accepts_observation_steps():
    plan = validate_browser_plan(
        {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/dashboard"},
                {"action": "expect_text", "selector": "main", "text": "Overview"},
                {"action": "screenshot"},
            ],
        },
        CONTRACT,
    )
    assert plan["steps"][0]["url"] == "https://test.example.invalid/dashboard"
    assert plan["execution_mode"] == "safe_read_only"


def test_safe_read_only_browser_plan_rejects_interaction():
    with pytest.raises(BrowserExecutionError, match="browser_interaction_requires_approval"):
        validate_browser_plan(
            {"execution_mode": "safe_read_only", "steps": [{"action": "click", "selector": "button"}]},
            CONTRACT,
        )


def test_sandbox_interaction_requires_explicit_write_approval():
    with pytest.raises(BrowserExecutionError, match="browser_write_approval_missing"):
        validate_browser_plan(
            {"execution_mode": "approved_sandbox_write", "steps": [{"action": "click", "selector": "button"}]},
            CONTRACT,
        )

    plan = validate_browser_plan(
        {"execution_mode": "approved_sandbox_write", "write_approved": True, "steps": [{"action": "click", "selector": "button"}]},
        CONTRACT,
    )
    assert plan["steps"][0]["action"] == "click"


def test_browser_target_cannot_escape_approved_base_url():
    with pytest.raises(BrowserExecutionError, match="browser_target_outside_approved_base_url"):
        validate_browser_plan(
            {"steps": [{"action": "goto", "url": "https://other.example.invalid/"}]},
            CONTRACT,
        )
