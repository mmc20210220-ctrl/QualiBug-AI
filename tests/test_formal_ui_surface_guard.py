from __future__ import annotations

from ai_test_asset_center import formal_ui_surface as ui
from ai_test_asset_center.formal_ui_surface_guard import (
    install_formal_ui_read_only_guard,
)


def _envelope(actions: list[dict]) -> dict:
    return {
        "property_spec": {
            "ui_request": {
                "request_id": "guarded_ui_request",
                "provider": "playwright_browser_plan",
                "start_url": "/orders/123",
                "execution_mode": "approved_sandbox_write",
                "browser_plan": {"steps": actions},
            }
        },
        "operation_ref": "bir_op_get_order",
        "treatment_actor_ref": "actor_admin",
    }


def test_interactive_ui_plan_is_blocked_until_cleanup_equivalence_exists() -> None:
    ui.install_formal_ui_surface()
    install_formal_ui_read_only_guard()

    result = ui._compile_ui_protocol(_envelope([
        {"action": "goto", "url": "/orders/123"},
        {"action": "click", "selector": "button.approve"},
        {"action": "expect_text", "selector": ".status", "text": "Approved"},
    ]))

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_TARGET_POLICY"
    assert "cleanup_equivalence" in result["detail"]
    assert "click" in result["detail"]


def test_read_only_ui_expectation_still_compiles() -> None:
    ui.install_formal_ui_surface()
    install_formal_ui_read_only_guard()

    result = ui._compile_ui_protocol(_envelope([
        {"action": "goto", "url": "/orders/123"},
        {"action": "expect_text", "selector": ".status", "text": "Approved"},
    ]))

    assert result["status"] == "COMPILED"
    assert result["assertion"]["kind"] == ui.ASSERTION_KIND


def test_non_object_browser_plan_is_a_visible_binding_block() -> None:
    ui.install_formal_ui_surface()
    install_formal_ui_read_only_guard()

    envelope = _envelope([])
    envelope["property_spec"]["ui_request"]["browser_plan"] = "not-an-object"
    result = ui._compile_ui_protocol(envelope)

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert result["detail"] == "ui_browser_plan_not_an_object"
