from __future__ import annotations

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import professional_ui_accessibility_engine as engine
from ai_test_asset_center.professional_ui_accessibility_rule_governance import (
    CUSTOM_ONLY_RULES,
)
from ai_test_asset_center.professional_ui_accessibility_semantics_guard import (
    _style_indicator_changed,
)


def test_formal_standard_excludes_ambiguous_advisory_rules() -> None:
    removed = {
        "svg_has_name",
        "main_landmark_single",
        "table_headers",
        "heading_order",
        "skip_link_present",
    }
    replacements = {
        "role_img_has_name",
        "main_landmark_present",
        "multiple_main_landmarks_named",
        "explicit_data_table_headers",
        "bypass_blocks_mechanism",
    }

    assert removed.isdisjoint(engine.RULE_CATALOG)
    assert replacements.issubset(engine.RULE_CATALOG)
    assert set(engine.STANDARD_RULES).issubset(engine.RULE_CATALOG)
    assert CUSTOM_ONLY_RULES.issubset(engine.RULE_CATALOG)
    assert CUSTOM_ONLY_RULES.isdisjoint(engine.STANDARD_RULES)
    assert engine._DOM_RULES == frozenset(
        set(engine.RULE_CATALOG) - set(engine._FOCUS_RULES)
    )


def test_governed_dom_script_uses_conservative_preconditions() -> None:
    script = engine._DOM_AUDIT_SCRIPT

    assert "selected.has('role_img_has_name')" in script
    assert "svg[role=\"img\"]" in script
    assert "selected.has('main_landmark_present')" in script
    assert "selected.has('multiple_main_landmarks_named')" in script
    assert "selected.has('explicit_data_table_headers')" in script
    assert "selected.has('bypass_blocks_mechanism')" in script
    assert "native_user_agent_control_size" in script
    assert "Math.hypot(cx - ocx, cy - ocy) < 24" in script
    assert "selected.has('heading_order')" not in script
    assert "selected.has('skip_link_present')" not in script


def test_static_border_is_not_treated_as_focus_indicator() -> None:
    unchanged = {
        "outlineStyle": "none",
        "outlineWidth": "0px",
        "outlineColor": "rgb(0, 0, 0)",
        "outlineOffset": "0px",
        "boxShadow": "none",
        "borderTop": "1px solid rgb(0, 0, 0)",
        "borderRight": "1px solid rgb(0, 0, 0)",
        "borderBottom": "1px solid rgb(0, 0, 0)",
        "borderLeft": "1px solid rgb(0, 0, 0)",
        "backgroundColor": "rgb(255, 255, 255)",
    }

    assert _style_indicator_changed(unchanged, dict(unchanged)) is False


def test_focus_outline_delta_is_deterministic_indicator() -> None:
    before = {
        "outlineStyle": "none",
        "outlineWidth": "0px",
        "outlineColor": "rgb(0, 0, 0)",
        "outlineOffset": "0px",
        "boxShadow": "none",
        "borderTop": "0px none rgb(0, 0, 0)",
        "borderRight": "0px none rgb(0, 0, 0)",
        "borderBottom": "0px none rgb(0, 0, 0)",
        "borderLeft": "0px none rgb(0, 0, 0)",
        "backgroundColor": "rgb(255, 255, 255)",
    }
    after = {
        **before,
        "outlineStyle": "solid",
        "outlineWidth": "2px",
        "outlineColor": "rgb(0, 95, 204)",
    }

    assert _style_indicator_changed(before, after) is True
