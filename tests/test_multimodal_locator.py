from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center.browser_execution import (
    BrowserExecutionError,
    validate_browser_plan,
)
from ai_test_asset_center.multimodal_locator import (
    MultimodalLocatorError,
    resolve_multimodal_locator,
)


class _Locator:
    def __init__(self, count: int = 1) -> None:
        self._count = count

    def filter(self, **_: object) -> "_Locator":
        return self

    def count(self) -> int:
        return self._count

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def bounding_box(self) -> dict[str, float]:
        return {"x": 10, "y": 20, "width": 120, "height": 32}

    def evaluate(self, _: str) -> dict[str, str]:
        return {
            "tag": "button",
            "id": "submit",
            "role": "button",
            "aria_label": "Submit order",
            "text": "Submit order",
        }

    def screenshot(self, **_: object) -> bytes:
        return b"\x89PNG\r\n\x1a\nvisual-element-evidence"


class _Page:
    def __init__(self, count: int = 1) -> None:
        self.result = _Locator(count)

    def get_by_role(self, *_: object, **__: object) -> _Locator:
        return self.result


def _runtime_contract() -> dict[str, object]:
    return {
        "status": "approved",
        "approved_base_url": "http://127.0.0.1:3001",
    }


def test_multimodal_locator_requires_unique_dom_and_visual_evidence(
    tmp_path: Path,
) -> None:
    locator, receipt = resolve_multimodal_locator(
        _Page(),
        {"role": "button", "name": "Submit order"},
        artifact_dir=tmp_path,
        step_index=2,
    )

    assert isinstance(locator, _Locator)
    assert receipt["schema_version"] == "qualibug.multimodal-locator.v1"
    assert receipt["status"] == "VERIFIED"
    assert receipt["locator_strategy"] == "accessibility_role"
    assert len(receipt["dom_fingerprint"]) == 64
    assert len(receipt["visual_fingerprint"]) == 64
    assert (tmp_path / "locator_step_2.png").read_bytes().startswith(b"\x89PNG")


def test_multimodal_locator_fails_closed_when_target_is_ambiguous(
    tmp_path: Path,
) -> None:
    with pytest.raises(MultimodalLocatorError, match="ambiguous"):
        resolve_multimodal_locator(
            _Page(count=2),
            {"role": "button", "name": "Save"},
            artifact_dir=tmp_path,
            step_index=1,
        )


def test_browser_plan_accepts_constrained_locator_intent() -> None:
    plan = validate_browser_plan(
        {
            "execution_mode": "approved_sandbox_write",
            "write_approved": True,
            "steps": [
                {"action": "goto", "url": "/orders"},
                {
                    "action": "click",
                    "locator_intent": {
                        "role": "button",
                        "name": "Submit order",
                    },
                },
            ],
        },
        _runtime_contract(),
    )

    assert plan["steps"][1]["locator_intent"] == {
        "role": "button",
        "name": "Submit order",
    }


def test_browser_plan_rejects_unbound_interaction_target() -> None:
    with pytest.raises(BrowserExecutionError, match="browser_locator_missing"):
        validate_browser_plan(
            {
                "execution_mode": "approved_sandbox_write",
                "write_approved": True,
                "steps": [
                    {"action": "goto", "url": "/orders"},
                    {"action": "click"},
                ],
            },
            _runtime_contract(),
        )
