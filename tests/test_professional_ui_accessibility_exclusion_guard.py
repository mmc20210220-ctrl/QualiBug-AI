from __future__ import annotations

import copy
import json

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import professional_ui_accessibility_engine as engine
from ai_test_asset_center import professional_ui_accessibility_observation_guard as observations
from ai_test_asset_center import professional_ui_readonly as professional


_RUNTIME = {
    "status": "approved",
    "approved_base_url": "https://example.test",
}


def _step() -> dict[str, object]:
    plan = professional.validate_professional_browser_plan(
        {
            "execution_mode": "safe_read_only",
            "steps": [{
                "action": engine.ACTION,
                "rules": ["buttons_have_name"],
                "exclude_selectors": ["[broken"],
            }],
        },
        _RUNTIME,
    )
    return plan["steps"][0]


class _InvalidSelectorPage:
    def evaluate(self, _script: str, _value: object = None) -> int:
        return 1


class _ValidationFailurePage:
    def evaluate(self, _script: str, _value: object = None) -> int:
        raise RuntimeError("page context lost")


def test_invalid_exclusion_selector_is_indeterminate_and_minimized() -> None:
    token = observations._OBSERVATIONS.set([])
    try:
        receipt = engine._execute_engine(_InvalidSelectorPage(), _step())
        captured = copy.deepcopy(observations._OBSERVATIONS.get())
    finally:
        observations._OBSERVATIONS.reset(token)

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "UI_ACCESSIBILITY_EXCLUSION_SELECTOR_INVALID"
    assert receipt["complete_observation"] is False
    assert receipt["raw_exclusion_selectors_included"] is False
    assert len(receipt["exclude_selector_fingerprints"]) == 1
    serialized = json.dumps(receipt, ensure_ascii=False)
    assert "[broken" not in serialized
    assert captured[0]["reason_code"] == receipt["reason_code"]


def test_exclusion_validation_runtime_failure_is_not_a_ui_violation() -> None:
    token = observations._OBSERVATIONS.set([])
    try:
        receipt = engine._execute_engine(_ValidationFailurePage(), _step())
        captured = copy.deepcopy(observations._OBSERVATIONS.get())
    finally:
        observations._OBSERVATIONS.reset(token)

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "UI_ACCESSIBILITY_EXCLUSION_VALIDATION_FAILED"
    assert receipt["violation_count"] == 0
    assert receipt["ai_accessibility_judgement_used"] is False
    assert captured[0]["status"] == "INDETERMINATE"
