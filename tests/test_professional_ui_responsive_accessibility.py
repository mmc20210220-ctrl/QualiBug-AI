from __future__ import annotations

import pytest

from ai_test_asset_center import formal_ui_surface as formal_ui
from ai_test_asset_center import professional_ui_readonly as professional
from ai_test_asset_center import scan_ui_contract_overlay as scan_overlay
from ai_test_asset_center import source_ui_contract_binding as source_binding
from ai_test_asset_center.browser_execution import BrowserExecutionError
from ai_test_asset_center.professional_ui_contract_guard import (
    install_professional_ui_contract_guard,
)
from ai_test_asset_center.professional_ui_responsive_accessibility import (
    ACCESSIBILITY_ACTION,
    ACCESSIBILITY_RULES,
    install_professional_ui_responsive_accessibility,
)


def _install() -> None:
    professional.install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    install_professional_ui_responsive_accessibility()


def _runtime() -> dict:
    return {
        "status": "approved",
        "approved_base_url": "https://example.test",
        "declared_adapters": ["ui_browser"],
    }


def test_responsive_and_accessibility_vocabularies_share_one_authority() -> None:
    _install()

    assert ACCESSIBILITY_ACTION in professional.PROFESSIONAL_EXPECTATIONS
    assert "set_viewport" in professional.READ_ONLY_ACTIONS
    assert "set_media" in professional.READ_ONLY_ACTIONS
    assert "set_viewport" not in professional.PROFESSIONAL_EXPECTATIONS
    assert formal_ui._SUPPORTED_EXPECTATIONS == professional.PROFESSIONAL_EXPECTATIONS
    assert scan_overlay._EXPECTATION_ACTIONS == professional.PROFESSIONAL_EXPECTATIONS
    assert source_binding._EXPECTATION_ACTIONS == professional.PROFESSIONAL_EXPECTATIONS


def test_responsive_plan_validates_configuration_before_execution() -> None:
    _install()
    normalized = professional.validate_professional_browser_plan(
        {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "set_viewport", "width": 375, "height": 812},
                {
                    "action": "set_media",
                    "color_scheme": "dark",
                    "reduced_motion": "reduce",
                },
                {"action": "goto", "url": "/orders"},
                {"action": "expect_no_horizontal_overflow", "tolerance_px": 0},
                {
                    "action": ACCESSIBILITY_ACTION,
                    "rules": ["html_lang", "buttons_have_name"],
                    "max_violations": 0,
                },
            ],
        },
        _runtime(),
    )

    assert normalized["steps"][0] == {
        "action": "set_viewport",
        "width": 375,
        "height": 812,
        "step_index": 1,
    }
    assert normalized["steps"][1]["color_scheme"] == "dark"
    assert normalized["steps"][1]["reduced_motion"] == "reduce"
    assert normalized["steps"][4]["rules"] == [
        "html_lang",
        "buttons_have_name",
    ]


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        (
            {"action": "set_viewport", "width": 100, "height": 812},
            "browser_viewport_width_invalid",
        ),
        (
            {"action": "set_viewport", "width": 375, "height": 100},
            "browser_viewport_height_invalid",
        ),
        (
            {
                "action": "set_media",
                "color_scheme": "sepia",
                "reduced_motion": "reduce",
            },
            "browser_color_scheme_invalid",
        ),
        (
            {
                "action": ACCESSIBILITY_ACTION,
                "rules": [],
                "max_violations": 0,
            },
            "browser_accessibility_rules_missing",
        ),
        (
            {
                "action": ACCESSIBILITY_ACTION,
                "rules": ["guess_user_happiness"],
                "max_violations": 0,
            },
            "browser_accessibility_rules_unsupported:guess_user_happiness",
        ),
    ],
)
def test_invalid_responsive_or_accessibility_contract_is_blocked(
    step: dict,
    reason: str,
) -> None:
    _install()
    with pytest.raises(BrowserExecutionError, match=rf"^{reason}$"):
        professional.validate_professional_browser_plan(
            {
                "execution_mode": "safe_read_only",
                "steps": [
                    {"action": "goto", "url": "/orders"},
                    step,
                ],
            },
            _runtime(),
        )


class _ResponsivePage:
    def __init__(self) -> None:
        self.viewport = None
        self.media = None

    def set_viewport_size(self, viewport: dict) -> None:
        self.viewport = dict(viewport)

    def emulate_media(self, **kwargs: object) -> None:
        self.media = dict(kwargs)


def test_responsive_configuration_produces_non_sensitive_receipts() -> None:
    _install()
    page = _ResponsivePage()

    viewport_receipt = professional._execute_expectation(
        page=page,
        step={"action": "set_viewport", "width": 390, "height": 844},
        console=[],
        network=[],
    )
    media_receipt = professional._execute_expectation(
        page=page,
        step={
            "action": "set_media",
            "color_scheme": "dark",
            "reduced_motion": "reduce",
        },
        console=[],
        network=[],
    )

    assert page.viewport == {"width": 390, "height": 844}
    assert page.media == {
        "color_scheme": "dark",
        "reduced_motion": "reduce",
    }
    assert viewport_receipt["raw_observed_value_included"] is False
    assert media_receipt["raw_observed_value_included"] is False


class _AccessiblePage:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.rules = None

    def evaluate(self, script: str, rules: list[str]) -> dict:
        assert "buttons_have_name" in script
        self.rules = list(rules)
        return self.response


def test_accessibility_audit_passes_with_zero_violations() -> None:
    _install()
    page = _AccessiblePage({"counts": {}, "findings": [], "truncated": False})

    receipt = professional._execute_expectation(
        page=page,
        step={
            "action": ACCESSIBILITY_ACTION,
            "rules": sorted(ACCESSIBILITY_RULES),
            "max_violations": 0,
        },
        console=[],
        network=[],
    )

    assert receipt["violation_count"] == 0
    assert receipt["violation_fingerprints"] == []
    assert receipt["raw_dom_included"] is False
    assert receipt["raw_page_text_included"] is False
    assert page.rules == sorted(ACCESSIBILITY_RULES)


def test_accessibility_budget_violation_is_a_typed_ui_violation() -> None:
    _install()
    page = _AccessiblePage({
        "counts": {"buttons_have_name": 2, "unique_ids": 1},
        "findings": [
            {"rule": "buttons_have_name", "tag": "button", "id": "x"},
            {"rule": "buttons_have_name", "tag": "button", "id": "y"},
            {"rule": "unique_ids", "tag": "div", "id": "same"},
        ],
        "truncated": False,
    })

    with pytest.raises(
        professional.ProfessionalUIExpectationError,
        match=(
            r"^UI_EXPECTATION_UNSATISFIED:expect_accessibility_basics:"
            r"violation_budget_exceeded_3_buttons_have_name,unique_ids$"
        ),
    ):
        professional._execute_expectation(
            page=page,
            step={
                "action": ACCESSIBILITY_ACTION,
                "rules": ["buttons_have_name", "unique_ids"],
                "max_violations": 0,
            },
            console=[],
            network=[],
        )
