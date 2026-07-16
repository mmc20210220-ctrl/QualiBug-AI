from __future__ import annotations

"""Fail-closed DOM/accessibility + visual evidence locator resolution."""

import hashlib
import json
from pathlib import Path
from typing import Any


LOCATOR_RECEIPT_SCHEMA = "qualibug.multimodal-locator.v1"
_ALLOWED_INTENT_FIELDS = frozenset({
    "role",
    "name",
    "text",
    "label",
    "test_id",
    "css",
})


class MultimodalLocatorError(ValueError):
    """A UI target is missing, ambiguous, invisible, or visually unobserved."""


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_locator_intent(value: dict[str, Any]) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise MultimodalLocatorError("locator_intent_missing")
    unknown = sorted(set(value) - _ALLOWED_INTENT_FIELDS)
    if unknown:
        raise MultimodalLocatorError(
            "locator_intent_fields_unsupported:" + ",".join(unknown)
        )
    intent = {
        field: _text(value.get(field))
        for field in _ALLOWED_INTENT_FIELDS
        if _text(value.get(field))
    }
    if not intent:
        raise MultimodalLocatorError("locator_intent_empty")
    if "role" in intent and "name" not in intent and "text" not in intent:
        raise MultimodalLocatorError("locator_accessible_name_missing")
    return intent


def _candidate(page: Any, intent: dict[str, str]) -> tuple[Any, str]:
    if intent.get("css"):
        return page.locator(intent["css"]), "source_css"
    if intent.get("test_id"):
        return page.get_by_test_id(intent["test_id"]), "source_test_id"
    if intent.get("role"):
        locator = page.get_by_role(
            intent["role"],
            name=intent.get("name") or intent.get("text"),
            exact=True,
        )
        if intent.get("text") and intent.get("name"):
            locator = locator.filter(has_text=intent["text"])
        return locator, "accessibility_role"
    if intent.get("label"):
        return page.get_by_label(intent["label"], exact=True), "accessible_label"
    if intent.get("text"):
        return page.get_by_text(intent["text"], exact=True), "visible_text"
    raise MultimodalLocatorError("locator_intent_not_resolvable")


def resolve_multimodal_locator(
    page: Any,
    locator_intent: dict[str, Any],
    *,
    artifact_dir: Path,
    step_index: int,
) -> tuple[Any, dict[str, Any]]:
    """Resolve exactly one element and bind DOM plus screenshot evidence."""

    intent = validate_locator_intent(locator_intent)
    locator, strategy = _candidate(page, intent)
    count = int(locator.count())
    if count == 0:
        raise MultimodalLocatorError("multimodal_locator_target_missing")
    if count != 1:
        raise MultimodalLocatorError(
            f"multimodal_locator_target_ambiguous:{count}"
        )
    if locator.is_visible() is not True:
        raise MultimodalLocatorError("multimodal_locator_target_not_visible")
    box = locator.bounding_box()
    if (
        not isinstance(box, dict)
        or float(box.get("width") or 0) <= 0
        or float(box.get("height") or 0) <= 0
    ):
        raise MultimodalLocatorError("multimodal_locator_visual_bounds_missing")
    dom = locator.evaluate(
        """element => ({
          tag: (element.tagName || '').toLowerCase(),
          id: element.id || '',
          role: element.getAttribute('role') || '',
          aria_label: element.getAttribute('aria-label') || '',
          text: (element.innerText || element.textContent || '').trim().slice(0, 500)
        })"""
    )
    if not isinstance(dom, dict):
        raise MultimodalLocatorError("multimodal_locator_dom_observation_missing")
    output_dir = Path(artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / f"locator_step_{int(step_index)}.png"
    screenshot = locator.screenshot(path=str(screenshot_path))
    if isinstance(screenshot, bytearray):
        screenshot = bytes(screenshot)
    if isinstance(screenshot, bytes) and not screenshot_path.exists():
        screenshot_path.write_bytes(screenshot)
    if not screenshot_path.is_file():
        raise MultimodalLocatorError("multimodal_locator_visual_capture_missing")
    visual = screenshot_path.read_bytes()
    if len(visual) < 8 or not visual.startswith(b"\x89PNG\r\n\x1a\n"):
        raise MultimodalLocatorError("multimodal_locator_visual_capture_invalid")
    normalized_box = {
        field: round(float(box.get(field) or 0), 3)
        for field in ("x", "y", "width", "height")
    }
    receipt = {
        "schema_version": LOCATOR_RECEIPT_SCHEMA,
        "status": "VERIFIED",
        "step_index": int(step_index),
        "locator_strategy": strategy,
        "locator_intent_fingerprint": _fingerprint(intent),
        "dom_fingerprint": _fingerprint(dom),
        "visual_fingerprint": hashlib.sha256(visual).hexdigest(),
        "bounding_box": normalized_box,
        "visible": True,
        "enabled": bool(locator.is_enabled()),
        "screenshot": screenshot_path.name,
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    return locator, receipt
