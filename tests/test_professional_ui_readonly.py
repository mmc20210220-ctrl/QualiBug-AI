from __future__ import annotations

import pytest

from ai_test_asset_center import formal_ui_surface as formal_ui
from ai_test_asset_center import scan_ui_contract_overlay as scan_overlay
from ai_test_asset_center import source_ui_contract_binding as source_binding
from ai_test_asset_center.browser_execution import BrowserExecutionError
from ai_test_asset_center.professional_ui_contract_guard import (
    install_professional_ui_contract_guard,
)
from ai_test_asset_center.professional_ui_readonly import (
    PROFESSIONAL_EXPECTATIONS,
    ProfessionalUIExpectationError,
    _execute_expectation,
    install_professional_ui_readonly,
    validate_professional_browser_plan,
)


def _runtime() -> dict:
    return {
        "status": "approved",
        "approved_base_url": "https://example.test",
        "declared_adapters": ["ui_browser"],
    }


def _source_ref() -> dict:
    return {
        "source_id": "ui-spec-orders",
        "version": "v1",
        "locator": "screen:orders:toolbar",
        "kind": "formal_ui_contract",
        "quote_hash": "a" * 64,
    }


def _request(action: str, **step_fields: object) -> dict:
    return {
        "request_id": f"ui-{action}",
        "title": f"Professional assertion {action}",
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/orders",
        "execution_mode": "safe_read_only",
        "operation_id": "list_orders",
        "actor_role": "public",
        "source_refs": [_source_ref()],
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/orders"},
                {"action": action, **step_fields},
            ],
        },
    }


def test_professional_vocabulary_is_shared_by_scan_ir_and_oracle_authorities() -> None:
    install_professional_ui_readonly()
    install_professional_ui_contract_guard()

    assert scan_overlay._EXPECTATION_ACTIONS == PROFESSIONAL_EXPECTATIONS
    assert source_binding._EXPECTATION_ACTIONS == PROFESSIONAL_EXPECTATIONS
    assert formal_ui._SUPPORTED_EXPECTATIONS == PROFESSIONAL_EXPECTATIONS


def test_professional_contract_enters_the_existing_scan_overlay() -> None:
    install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    request = _request(
        "expect_accessible_name",
        selector="#create-order",
        expected="Create order",
    )

    asset, receipt = scan_overlay.overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [request]},
    )

    assert receipt["status"] == "OVERLAID"
    assert receipt["contract_added_count"] == 1
    assert asset["ui_formal_contracts"][0]["ui_request"][
        "browser_plan"
    ]["steps"][1]["action"] == "expect_accessible_name"


def test_readonly_plan_accepts_professional_ui_and_ux_assertions() -> None:
    plan = {
        "execution_mode": "safe_read_only",
        "steps": [
            {"action": "goto", "url": "/orders"},
            {"action": "expect_visible", "selector": "#orders-table"},
            {
                "action": "expect_attribute",
                "selector": "#orders-table",
                "name": "aria-label",
                "expected": "Orders",
            },
            {
                "action": "expect_dimensions",
                "selector": "#create-order",
                "min_width": 44,
                "min_height": 44,
            },
            {"action": "expect_not_obscured", "selector": "#create-order"},
            {"action": "expect_no_horizontal_overflow", "tolerance_px": 0},
            {"action": "expect_no_console_errors", "ignore_patterns": ["known"]},
            {
                "action": "expect_no_failed_requests",
                "status_threshold": 400,
                "ignore_url_patterns": ["/telemetry"],
            },
        ],
    }

    normalized = validate_professional_browser_plan(plan, _runtime())

    assert normalized["execution_mode"] == "safe_read_only"
    assert [row["action"] for row in normalized["steps"]] == [
        "goto",
        "expect_visible",
        "expect_attribute",
        "expect_dimensions",
        "expect_not_obscured",
        "expect_no_horizontal_overflow",
        "expect_no_console_errors",
        "expect_no_failed_requests",
    ]


def test_professional_increment_refuses_interactive_actions() -> None:
    with pytest.raises(
        BrowserExecutionError,
        match=r"^browser_action_unsupported:click$",
    ):
        validate_professional_browser_plan(
            {
                "execution_mode": "safe_read_only",
                "steps": [
                    {"action": "goto", "url": "/orders"},
                    {"action": "click", "selector": "#create-order"},
                ],
            },
            _runtime(),
        )


def test_professional_increment_refuses_write_mode_even_without_clicks() -> None:
    with pytest.raises(
        BrowserExecutionError,
        match=r"^professional_ui_readonly_mode_required$",
    ):
        validate_professional_browser_plan(
            {
                "execution_mode": "approved_sandbox_write",
                "write_approved": True,
                "steps": [{"action": "goto", "url": "/orders"}],
            },
            _runtime(),
        )


def test_malformed_source_regex_is_rejected_before_browser_execution() -> None:
    install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    with pytest.raises(
        BrowserExecutionError,
        match=r"^browser_console_ignore_pattern_invalid$",
    ):
        validate_professional_browser_plan(
            {
                "execution_mode": "safe_read_only",
                "steps": [
                    {"action": "goto", "url": "/orders"},
                    {
                        "action": "expect_no_console_errors",
                        "ignore_patterns": ["["],
                    },
                ],
            },
            _runtime(),
        )


class _AbsentLocator:
    def count(self) -> int:
        return 0


class _AbsentPage:
    def locator(self, selector: str) -> _AbsentLocator:
        assert selector == "#deleted-dialog"
        return _AbsentLocator()


def test_hidden_expectation_accepts_source_target_absence() -> None:
    install_professional_ui_readonly()
    install_professional_ui_contract_guard()

    receipt = _execute_expectation(
        page=_AbsentPage(),
        step={
            "action": "expect_hidden",
            "selector": "#deleted-dialog",
            "step_index": 1,
        },
        console=[],
        network=[],
    )

    assert receipt["hidden_by_absence"] is True
    assert receipt["locator"]["matched_count"] == 0
    assert receipt["raw_observed_value_included"] is False


class _OverflowPage:
    def evaluate(self, script: str) -> dict:
        assert "scrollWidth" in script
        return {"scrollWidth": 1100, "clientWidth": 1000}


def test_layout_overflow_is_a_typed_expectation_failure() -> None:
    with pytest.raises(
        ProfessionalUIExpectationError,
        match=r"^UI_EXPECTATION_UNSATISFIED:expect_no_horizontal_overflow:overflow_detected$",
    ):
        _execute_expectation(
            page=_OverflowPage(),
            step={
                "action": "expect_no_horizontal_overflow",
                "tolerance_px": 0,
            },
            console=[],
            network=[],
        )


def test_professional_expectation_failure_is_classified_as_observed_violation() -> None:
    install_professional_ui_readonly()
    install_professional_ui_contract_guard()

    assert formal_ui._timeout_expectation_failure(
        "UI_EXPECTATION_UNSATISFIED:expect_visible:target_missing",
        {"action": "expect_visible"},
    ) is True
    assert formal_ui._timeout_expectation_failure(
        "TypeError:browser crashed",
        {"action": "expect_visible"},
    ) is False
