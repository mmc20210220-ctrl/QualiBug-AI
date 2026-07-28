"""Source-level viewport binding for formal visual baselines.

Every ``expect_visual_baseline`` must declare the exact CSS-pixel viewport used
when the immutable baseline was approved. A matching ``set_viewport`` must occur
before that expectation in the same browser plan. This guard works during
enterprise ingestion and direct-scan admission; it opens no browser or files.
"""
from __future__ import annotations

from typing import Any

from . import _formal_ui_contracts as _contracts
from ._formal_ui_browser_matrix_guard import (
    install_formal_ui_browser_matrix_guard,
)

ACTION = "expect_visual_baseline"
_INSTALL_MARKER = "_qualibug_formal_ui_visual_viewport_guard_installed"
_ORIGINAL_MARKER = "_qualibug_original_formal_ui_validator_before_visual_viewport"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if not minimum <= number <= maximum:
        return None
    return number


def _viewport_missing_requirements(contract: dict[str, Any]) -> list[str]:
    request = _dict(contract.get("ui_request"))
    plan = _dict(request.get("browser_plan"))
    steps = [row for row in _list(plan.get("steps")) if isinstance(row, dict)]
    active: tuple[int, int] | None = None
    missing: list[str] = []
    visual_index = 0
    for step in steps:
        action = _text(step.get("action")).lower()
        if action == "set_viewport":
            width = _positive_int(step.get("width"), minimum=240, maximum=7680)
            height = _positive_int(step.get("height"), minimum=240, maximum=4320)
            active = (width, height) if width and height else None
            continue
        if action != ACTION:
            continue
        visual_index += 1
        prefix = f"{ACTION}[{visual_index}]"
        width = _positive_int(
            step.get("viewport_width"),
            minimum=240,
            maximum=7680,
        )
        height = _positive_int(
            step.get("viewport_height"),
            minimum=240,
            maximum=4320,
        )
        if width is None:
            missing.append(f"{prefix}.browser_visual_viewport_width_invalid")
        if height is None:
            missing.append(f"{prefix}.browser_visual_viewport_height_invalid")
        if active is None:
            missing.append(f"{prefix}.browser_visual_viewport_configuration_missing")
        elif width is not None and height is not None and active != (width, height):
            missing.append(f"{prefix}.browser_visual_viewport_configuration_mismatch")
    return list(dict.fromkeys(missing))


def _gap_from_contract(
    contract: dict[str, Any],
    *,
    source_id: str,
    locator: str,
    missing: list[str],
) -> dict[str, Any]:
    request = _dict(contract.get("ui_request"))
    return {
        "gap_type": "formal_ui_contract_incomplete",
        "reason_code": "FORMAL_UI_CONTRACT_INCOMPLETE",
        "contract_id": _text(
            contract.get("contract_id") or request.get("request_id")
        ),
        "source_id": source_id,
        "source_locator": locator,
        "missing_requirements": missing,
        "status": "unsupported",
    }


def install_formal_ui_visual_viewport_guard() -> None:
    if getattr(_contracts, _INSTALL_MARKER, False):
        install_formal_ui_browser_matrix_guard()
        return
    original = getattr(
        _contracts,
        _ORIGINAL_MARKER,
        _contracts._validate_contract,
    )
    setattr(_contracts, _ORIGINAL_MARKER, original)

    def validate_with_visual_viewport(
        raw: dict[str, Any],
        *,
        source_id: str,
        locator: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        contract, gap = original(raw, source_id=source_id, locator=locator)
        if gap or not contract:
            return contract, gap
        missing = _viewport_missing_requirements(contract)
        if not missing:
            return contract, None
        return None, _gap_from_contract(
            contract,
            source_id=source_id,
            locator=locator,
            missing=missing,
        )

    _contracts._validate_contract = validate_with_visual_viewport
    setattr(_contracts, _INSTALL_MARKER, True)
    # Matrix admission is another source-only guard. Installing it here keeps the
    # enterprise knowledge facade independent from the browser runtime while ensuring
    # direct ingestion and scan admission share the same strict contract boundary.
    install_formal_ui_browser_matrix_guard()


__all__ = ["install_formal_ui_visual_viewport_guard"]
