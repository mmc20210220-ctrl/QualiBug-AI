from __future__ import annotations

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import professional_ui_accessibility_engine as engine
from ai_test_asset_center import professional_ui_readonly as professional
from ai_test_asset_center.professional_ui_accessibility_aria_guard import ARIA_RULES
from ai_test_asset_center.professional_ui_accessibility_contract_guard import (
    CUSTOM_STANDARD,
)


_RUNTIME_CONTRACT = {
    "status": "approved",
    "approved_base_url": "https://example.test",
}


def _validate(rule: str) -> dict[str, object]:
    plan = professional.validate_professional_browser_plan(
        {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/records"},
                {"action": engine.ACTION, "rules": [rule]},
            ],
        },
        _RUNTIME_CONTRACT,
    )
    return plan["steps"][1]


def test_aria_rules_extend_supported_and_default_standard_catalogs() -> None:
    assert set(ARIA_RULES).issubset(engine.RULE_CATALOG)
    assert set(ARIA_RULES).issubset(engine.STANDARD_RULES)
    assert set(ARIA_RULES).isdisjoint(engine.CUSTOM_ONLY_RULES)


def test_aria_dom_script_preserves_numeric_and_native_state_boundaries() -> None:
    script = engine._DOM_AUDIT_SCRIPT

    assert "selected.has('aria_reference_unique')" in script
    assert "selected.has('aria_state_value_valid')" in script
    assert "selected.has('aria_required_state_present')" in script
    assert "selected.has('interactive_role_focusable')" in script
    assert "['aria-level','aria-posinset','aria-colindex','aria-rowindex']" in script
    assert "['aria-setsize','aria-colcount','aria-rowcount']" in script
    assert "value !== -1 && value < 1" in script
    assert "input[type=\"checkbox\"]" in script
    assert "input[type=\"radio\"]" in script
    assert "input[type=\"number\"]" in script
    assert "select,input[list]" in script


def test_custom_contract_can_select_one_aria_rule_without_claiming_full_standard() -> None:
    normalized = _validate("aria_state_value_valid")

    assert normalized["standard"] == CUSTOM_STANDARD
    assert normalized["rules"] == ["aria_state_value_valid"]
    assert normalized["require_complete_scan"] is True
    assert normalized["max_violations"] == 0
