"""Bind formal visual comparison to one active governed baseline record.

File existence and SHA-256 are necessary but insufficient authority. This
installer requires the exact visual baseline reference to have one active
project registry record whose content identity, CSS viewport, screenshot mode
and deterministic renderer profile match the source-declared expectation.
Revoked, unregistered, ambiguous or identity-drifted baselines are
INDETERMINATE and cannot become formal defects.
"""
from __future__ import annotations

import contextvars
import copy
from pathlib import Path
from typing import Any

from . import professional_ui_visual_baseline as _visual
from .visual_baseline_registry import active_visual_baseline_record

_INSTALL_MARKER = "_qualibug_visual_registry_binding_installed"
_ORIGINAL_BYTES = "_qualibug_visual_baseline_bytes_before_registry_binding"
_ORIGINAL_EXECUTOR = "_qualibug_visual_executor_before_registry_binding"
_ACTIVE_RECORD: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_active_visual_baseline_record",
    default={},
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _identity_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseline_registry_id": _text(record.get("baseline_id")),
        "baseline_registry_authority": _text(record.get("authority")),
        "baseline_registry_namespace": _text(record.get("namespace")),
        "baseline_registry_status": _text(record.get("status")),
        "baseline_registry_identity_verified": True,
    }


def _update_last_observation(fields: dict[str, Any]) -> None:
    rows = [copy.deepcopy(row) for row in _visual._OBSERVATIONS.get()]
    if not rows:
        return
    rows[-1].update(copy.deepcopy(fields))
    _visual._OBSERVATIONS.set(rows)


def _require_registry_identity(step: dict[str, Any]) -> dict[str, Any]:
    runtime = _dict(_visual._RUNTIME_CONTEXT.get())
    root = _text(runtime.get("root"))
    project = _text(runtime.get("project"))
    if not root or not project:
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_RUNTIME_CONTEXT_MISSING"
        )
    ref = _text(step.get("baseline_ref"))
    record = active_visual_baseline_record(project, ref, root=Path(root))
    if not record:
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_NOT_ACTIVE"
        )
    if _text(record.get("sha256")).lower() != _text(
        step.get("baseline_sha256")
    ).lower():
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_REGISTRY_HASH_MISMATCH"
        )
    if (
        _integer(record.get("viewport_width"))
        != _integer(step.get("viewport_width"))
        or _integer(record.get("viewport_height"))
        != _integer(step.get("viewport_height"))
    ):
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_VIEWPORT_IDENTITY_MISMATCH"
        )
    if record.get("full_page") is not step.get("full_page"):
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_SCREENSHOT_MODE_MISMATCH"
        )
    if (
        _text(record.get("renderer_profile"))
        != _text(step.get("renderer_profile"))
        or _text(record.get("scroll_origin"))
        != _text(step.get("scroll_origin"))
        or _text(record.get("font_readiness"))
        != _text(step.get("font_readiness"))
    ):
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_RENDERER_IDENTITY_MISMATCH"
        )
    namespace = _text(record.get("namespace"))
    authority = _text(record.get("authority"))
    if namespace == "visual_baselines" and authority != "source_registered":
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_AUTHORITY_INVALID"
        )
    if namespace == "approved_visual_baselines" and authority != "approved_copy":
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_AUTHORITY_INVALID"
        )
    if _text(record.get("status")) != "active":
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_NOT_ACTIVE"
        )
    return record


def install_visual_registry_binding() -> None:
    if getattr(_visual, _INSTALL_MARKER, False):
        return
    original_bytes = getattr(
        _visual,
        _ORIGINAL_BYTES,
        _visual._baseline_bytes,
    )
    original_execute = getattr(
        _visual,
        _ORIGINAL_EXECUTOR,
        _visual._execute_visual_baseline,
    )
    setattr(_visual, _ORIGINAL_BYTES, original_bytes)
    setattr(_visual, _ORIGINAL_EXECUTOR, original_execute)

    def baseline_bytes_with_registry(
        step: dict[str, Any],
    ) -> tuple[bytes, str]:
        record = _require_registry_identity(step)
        data, digest = original_bytes(step)
        if digest != _text(record.get("sha256")).lower():
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_BASELINE_FILE_REGISTRY_HASH_MISMATCH"
            )
        _ACTIVE_RECORD.set(copy.deepcopy(record))
        return data, digest

    def execute_with_registry(page: Any, step: dict[str, Any]) -> dict[str, Any]:
        token = _ACTIVE_RECORD.set({})
        try:
            receipt = original_execute(page, step)
            record = _dict(_ACTIVE_RECORD.get())
        except Exception:
            record = _dict(_ACTIVE_RECORD.get())
            if record:
                _update_last_observation(_identity_fields(record))
            raise
        finally:
            _ACTIVE_RECORD.reset(token)
        if record:
            fields = _identity_fields(record)
            receipt.update(fields)
            _update_last_observation(fields)
        return receipt

    _visual._baseline_bytes = baseline_bytes_with_registry
    _visual._execute_visual_baseline = execute_with_registry
    setattr(_visual, _INSTALL_MARKER, True)


__all__ = ["install_visual_registry_binding"]
