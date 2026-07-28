"""Source-declared deterministic visual baseline regression for formal UI tests.

This module extends the one professional UI authority with
``expect_visual_baseline``. A visual verdict requires an immutable project-scoped
PNG baseline, its exact SHA-256 identity, an explicit pixel-change budget and a
source-declared screenshot mode. No AI or provider-authored visual opinion can
become a formal defect.

Dynamic regions are masked in both baseline and current images using current DOM
geometry. Password/secret controls and governed-interaction sensitive locators
are masked automatically. Only hashes, dimensions, counts and ratios enter the
typed observer receipt; raw pixels are never embedded in JSON evidence.

Baselines are never created or updated by execution. Missing/corrupt/mismatched
baseline material is INDETERMINATE. A valid comparison exceeding the declared
budget is a typed UI violation.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import io
import re
from pathlib import Path, PurePosixPath
from typing import Any

from . import formal_ui_surface as _formal
from . import formal_ui_surface_guard as _guard
from . import professional_ui_interaction_cleanup as _interaction
from . import professional_ui_interaction_privacy_guard as _privacy
from . import professional_ui_readonly as _professional
from . import scan_ui_contract_overlay as _overlay
from . import source_ui_contract_binding as _source_binding
from . import ui_execution_adapter as _adapter
from .enterprise_knowledge_center import _formal_ui_contracts as _source_parser
from .multimodal_locator import MultimodalLocatorError, validate_locator_intent

ACTION = "expect_visual_baseline"
BASELINE_SCOPE = "project_approved_visual_baseline"
COMPARISON_METHOD = "rgba_max_channel_absolute_difference"
MAX_BASELINE_BYTES = 25_000_000
MAX_MASKS = 64
MAX_CHANGED_PIXEL_RATIO = 1.0
MAX_CHANNEL_TOLERANCE = 32
_INSTALL_MARKER = "_qualibug_professional_visual_baseline_installed"
_ORIGINAL_VALIDATE_STEP = "_qualibug_ui_validator_before_visual_baseline"
_ORIGINAL_EXECUTE = "_qualibug_ui_executor_before_visual_baseline"
_ORIGINAL_ADAPTER = "_qualibug_ui_adapter_before_visual_baseline"
_ORIGINAL_OBSERVER = "_qualibug_ui_observer_before_visual_baseline"
_ORIGINAL_SOURCE_GAPS = "_qualibug_source_ui_gaps_before_visual_baseline"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_visual_baseline_runtime_context",
    default={},
)
_OBSERVATIONS: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "qualibug_visual_baseline_observations",
    default=[],
)


class VisualBaselineObservationError(RuntimeError):
    """Baseline material or visual observation could not be trusted."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: Any) -> str:
    return _professional._fingerprint(value)


def _number(
    value: Any,
    *,
    minimum: float,
    maximum: float,
    code: str,
) -> float:
    if isinstance(value, bool):
        raise _professional._browser.BrowserExecutionError(code)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _professional._browser.BrowserExecutionError(code) from exc
    if not minimum <= number <= maximum:
        raise _professional._browser.BrowserExecutionError(code)
    return number


def _integer(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    code: str,
) -> int:
    number = _number(value, minimum=minimum, maximum=maximum, code=code)
    if int(number) != number:
        raise _professional._browser.BrowserExecutionError(code)
    return int(number)


def _validate_relative_baseline_ref(value: Any) -> str:
    ref = _text(value, limit=800).replace("\\", "/")
    if not ref:
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_baseline_ref_missing"
        )
    path = PurePosixPath(ref)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".png":
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_baseline_ref_invalid"
        )
    return ref


def _validate_mask_regions(value: Any) -> list[dict[str, int]]:
    rows = [dict(row) for row in _list(value) if isinstance(row, dict)]
    if len(rows) > MAX_MASKS:
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_mask_limit_exceeded"
        )
    normalized: list[dict[str, int]] = []
    for row in rows:
        normalized.append({
            "x": _integer(
                row.get("x"),
                minimum=0,
                maximum=100_000,
                code="browser_visual_mask_region_invalid",
            ),
            "y": _integer(
                row.get("y"),
                minimum=0,
                maximum=100_000,
                code="browser_visual_mask_region_invalid",
            ),
            "width": _integer(
                row.get("width"),
                minimum=1,
                maximum=100_000,
                code="browser_visual_mask_region_invalid",
            ),
            "height": _integer(
                row.get("height"),
                minimum=1,
                maximum=100_000,
                code="browser_visual_mask_region_invalid",
            ),
        })
    return normalized


def _validate_visual_step(raw: dict[str, Any]) -> None:
    raw["baseline_ref"] = _validate_relative_baseline_ref(raw.get("baseline_ref"))
    baseline_sha = _text(raw.get("baseline_sha256")).lower()
    if not _SHA256_RE.fullmatch(baseline_sha):
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_baseline_sha256_invalid"
        )
    raw["baseline_sha256"] = baseline_sha
    if "max_changed_pixel_ratio" not in raw:
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_changed_pixel_budget_missing"
        )
    raw["max_changed_pixel_ratio"] = _number(
        raw.get("max_changed_pixel_ratio"),
        minimum=0.0,
        maximum=MAX_CHANGED_PIXEL_RATIO,
        code="browser_visual_changed_pixel_budget_invalid",
    )
    raw["channel_tolerance"] = _integer(
        raw.get("channel_tolerance", 0),
        minimum=0,
        maximum=MAX_CHANNEL_TOLERANCE,
        code="browser_visual_channel_tolerance_invalid",
    )
    if not isinstance(raw.get("full_page"), bool):
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_full_page_boolean_required"
        )
    if raw.get("animations_disabled") is not True:
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_animations_must_be_disabled"
        )
    selectors = [_text(value, limit=500) for value in _list(raw.get("mask_selectors"))]
    if any(not value for value in selectors) or len(selectors) > MAX_MASKS:
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_mask_selectors_invalid"
        )
    raw["mask_selectors"] = list(dict.fromkeys(selectors))
    intents: list[dict[str, str]] = []
    for value in _list(raw.get("mask_locator_intents")):
        if not isinstance(value, dict):
            raise _professional._browser.BrowserExecutionError(
                "browser_visual_mask_locator_intents_invalid"
            )
        try:
            intents.append(validate_locator_intent(value))
        except MultimodalLocatorError as exc:
            raise _professional._browser.BrowserExecutionError(str(exc)) from exc
    if len(intents) > MAX_MASKS:
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_mask_limit_exceeded"
        )
    raw["mask_locator_intents"] = intents
    raw["mask_regions"] = _validate_mask_regions(raw.get("mask_regions"))
    if (
        len(raw["mask_selectors"])
        + len(raw["mask_locator_intents"])
        + len(raw["mask_regions"])
        > MAX_MASKS
    ):
        raise _professional._browser.BrowserExecutionError(
            "browser_visual_mask_limit_exceeded"
        )


def _safe_baseline_path(root: Path, project: str, ref: str) -> Path:
    root_path = Path(root).resolve()
    project_key = _professional._browser._safe_project(project)
    inputs = (root_path / "platform_inputs" / project_key).resolve()
    approved = (
        root_path
        / "platform_workspace"
        / project_key
        / "approved_visual_baselines"
    ).resolve()
    ref_path = PurePosixPath(ref)
    candidates: list[Path] = []
    if ref_path.parts and ref_path.parts[0] in {"platform_inputs", "platform_workspace"}:
        candidates.append((root_path / Path(*ref_path.parts)).resolve())
    else:
        candidates.extend([
            (inputs / Path(*ref_path.parts)).resolve(),
            (approved / Path(*ref_path.parts)).resolve(),
        ])
    valid: list[Path] = []
    for candidate in candidates:
        allowed = any(
            candidate == scope or scope in candidate.parents
            for scope in (inputs, approved)
        )
        if (
            allowed
            and candidate.is_file()
            and candidate.suffix.lower() == ".png"
            and candidate.stat().st_size <= MAX_BASELINE_BYTES
        ):
            valid.append(candidate)
    unique = list(dict.fromkeys(valid))
    if not unique:
        raise VisualBaselineObservationError("UI_VISUAL_BASELINE_NOT_FOUND")
    if len(unique) != 1:
        raise VisualBaselineObservationError("UI_VISUAL_BASELINE_REF_AMBIGUOUS")
    return unique[0]


def _baseline_bytes(step: dict[str, Any]) -> tuple[bytes, str]:
    context = _dict(_RUNTIME_CONTEXT.get())
    root_value = _text(context.get("root"))
    project = _text(context.get("project"))
    if not (root_value and project):
        raise VisualBaselineObservationError("UI_VISUAL_RUNTIME_CONTEXT_MISSING")
    path = _safe_baseline_path(
        Path(root_value),
        project,
        _text(step.get("baseline_ref")),
    )
    data = path.read_bytes()
    if len(data) < 8 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise VisualBaselineObservationError("UI_VISUAL_BASELINE_PNG_INVALID")
    digest = hashlib.sha256(data).hexdigest()
    if digest != _text(step.get("baseline_sha256")).lower():
        raise VisualBaselineObservationError("UI_VISUAL_BASELINE_HASH_MISMATCH")
    return data, digest


def _boxes_for_locator(locator: Any, *, required: bool) -> list[dict[str, float]]:
    count = int(locator.count())
    if required and count == 0:
        raise VisualBaselineObservationError("UI_VISUAL_MASK_TARGET_MISSING")
    if count > 200:
        raise VisualBaselineObservationError("UI_VISUAL_MASK_TARGET_LIMIT_EXCEEDED")
    boxes: list[dict[str, float]] = []
    for index in range(count):
        box = locator.nth(index).bounding_box()
        if not isinstance(box, dict):
            continue
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        boxes.append({
            "x": float(box.get("x") or 0),
            "y": float(box.get("y") or 0),
            "width": width,
            "height": height,
        })
    if required and not boxes:
        raise VisualBaselineObservationError("UI_VISUAL_MASK_BOUNDS_MISSING")
    return boxes


def _mask_boxes(page: Any, step: dict[str, Any]) -> list[dict[str, float]]:
    boxes: list[dict[str, float]] = []
    for selector in _list(step.get("mask_selectors")):
        boxes.extend(_boxes_for_locator(page.locator(selector), required=True))
    for intent in _list(step.get("mask_locator_intents")):
        locator, _strategy = _professional._candidate(
            page,
            {"locator_intent": intent},
        )
        boxes.extend(_boxes_for_locator(locator, required=True))

    # Automatic privacy masks are optional because a page may not contain these controls.
    for selector in (
        "input[type=password]",
        "[data-sensitive=true]",
        "[autocomplete=current-password]",
        "[autocomplete=new-password]",
        "[autocomplete=one-time-code]",
    ):
        boxes.extend(_boxes_for_locator(page.locator(selector), required=False))
    for sensitive_step in _privacy._SENSITIVE_STEPS.get():
        try:
            locator, _strategy = _interaction._candidate(page, sensitive_step)
        except Exception:
            continue
        boxes.extend(_boxes_for_locator(locator, required=False))

    for region in _list(step.get("mask_regions")):
        if isinstance(region, dict):
            boxes.append({
                "x": float(region.get("x") or 0),
                "y": float(region.get("y") or 0),
                "width": float(region.get("width") or 0),
                "height": float(region.get("height") or 0),
            })
    if len(boxes) > 300:
        raise VisualBaselineObservationError("UI_VISUAL_MASK_BOX_LIMIT_EXCEEDED")
    return boxes


def _open_rgba(data: bytes) -> Any:
    try:
        from PIL import Image
    except ImportError as exc:
        raise VisualBaselineObservationError("UI_VISUAL_PILLOW_UNAVAILABLE") from exc
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image.convert("RGBA")
    except Exception as exc:
        raise VisualBaselineObservationError("UI_VISUAL_PNG_DECODE_FAILED") from exc


def _apply_masks(image: Any, boxes: list[dict[str, float]]) -> int:
    from PIL import ImageDraw

    width, height = image.size
    draw = ImageDraw.Draw(image)
    applied = 0
    for box in boxes:
        x1 = max(0, min(width, int(box["x"])))
        y1 = max(0, min(height, int(box["y"])))
        x2 = max(0, min(width, int(box["x"] + box["width"] + 0.999)))
        y2 = max(0, min(height, int(box["y"] + box["height"] + 0.999)))
        if x2 <= x1 or y2 <= y1:
            continue
        draw.rectangle((x1, y1, x2 - 1, y2 - 1), fill=(0, 0, 0, 0))
        applied += 1
    return applied


def _compare_images(
    baseline: Any,
    current: Any,
    *,
    channel_tolerance: int,
) -> dict[str, Any]:
    try:
        from PIL import ImageChops, ImageStat
    except ImportError as exc:
        raise VisualBaselineObservationError("UI_VISUAL_PILLOW_UNAVAILABLE") from exc
    if baseline.size != current.size:
        return {
            "dimension_match": False,
            "baseline_width": int(baseline.size[0]),
            "baseline_height": int(baseline.size[1]),
            "current_width": int(current.size[0]),
            "current_height": int(current.size[1]),
            "changed_pixel_count": None,
            "changed_pixel_ratio": None,
            "mean_max_channel_delta": None,
            "change_bounding_box": None,
        }
    diff = ImageChops.difference(baseline, current)
    channels = diff.split()
    max_channel = channels[0]
    for channel in channels[1:]:
        max_channel = ImageChops.lighter(max_channel, channel)
    threshold = max_channel.point(
        lambda value: 255 if value > channel_tolerance else 0
    )
    histogram = threshold.histogram()
    changed = int(histogram[255])
    total = int(baseline.size[0] * baseline.size[1])
    bbox = threshold.getbbox()
    return {
        "dimension_match": True,
        "baseline_width": int(baseline.size[0]),
        "baseline_height": int(baseline.size[1]),
        "current_width": int(current.size[0]),
        "current_height": int(current.size[1]),
        "changed_pixel_count": changed,
        "changed_pixel_ratio": (changed / total) if total else 0.0,
        "mean_max_channel_delta": float(ImageStat.Stat(max_channel).mean[0]),
        "change_bounding_box": (
            {
                "x": int(bbox[0]),
                "y": int(bbox[1]),
                "width": int(bbox[2] - bbox[0]),
                "height": int(bbox[3] - bbox[1]),
            }
            if bbox
            else None
        ),
    }


def _append_observation(row: dict[str, Any]) -> None:
    rows = [copy.deepcopy(value) for value in _OBSERVATIONS.get()]
    rows.append(copy.deepcopy(row))
    _OBSERVATIONS.set(rows)


def _execute_visual_baseline(page: Any, step: dict[str, Any]) -> dict[str, Any]:
    observation_base = {
        "expectation": ACTION,
        "comparison_method": COMPARISON_METHOD,
        "baseline_scope": BASELINE_SCOPE,
        "baseline_ref_fingerprint": _fingerprint(_text(step.get("baseline_ref"))),
        "declared_baseline_sha256": _text(step.get("baseline_sha256")).lower(),
        "max_changed_pixel_ratio": float(step.get("max_changed_pixel_ratio")),
        "channel_tolerance": int(step.get("channel_tolerance") or 0),
        "full_page": bool(step.get("full_page")),
        "animations_disabled": True,
        "raw_pixels_in_receipt": False,
        "ai_visual_judgement_used": False,
        "baseline_auto_updated": False,
    }
    try:
        baseline_data, baseline_digest = _baseline_bytes(step)
        boxes = _mask_boxes(page, step)
        current_data = page.screenshot(
            full_page=bool(step.get("full_page")),
            animations="disabled",
            caret="hide",
            scale="css",
        )
        if isinstance(current_data, bytearray):
            current_data = bytes(current_data)
        if not isinstance(current_data, bytes) or not current_data.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise VisualBaselineObservationError("UI_VISUAL_CURRENT_PNG_INVALID")
        baseline_image = _open_rgba(baseline_data)
        current_image = _open_rgba(current_data)
        baseline_masked = baseline_image.copy()
        current_masked = current_image.copy()
        baseline_mask_count = _apply_masks(baseline_masked, boxes)
        current_mask_count = _apply_masks(current_masked, boxes)
        comparison = _compare_images(
            baseline_masked,
            current_masked,
            channel_tolerance=int(step.get("channel_tolerance") or 0),
        )
        baseline_comparable = io.BytesIO()
        current_comparable = io.BytesIO()
        baseline_masked.save(baseline_comparable, format="PNG")
        current_masked.save(current_comparable, format="PNG")
        receipt = {
            **observation_base,
            "status": "OBSERVED",
            "reason_code": "",
            "baseline_sha256": baseline_digest,
            "current_screenshot_sha256": hashlib.sha256(current_data).hexdigest(),
            "comparable_baseline_sha256": hashlib.sha256(
                baseline_comparable.getvalue()
            ).hexdigest(),
            "comparable_current_sha256": hashlib.sha256(
                current_comparable.getvalue()
            ).hexdigest(),
            "mask_box_count": len(boxes),
            "baseline_mask_applied_count": baseline_mask_count,
            "current_mask_applied_count": current_mask_count,
            **comparison,
        }
        if comparison["dimension_match"] is not True:
            receipt["status"] = "VIOLATION_OBSERVED"
            receipt["reason_code"] = "UI_VISUAL_DIMENSION_MISMATCH"
            _append_observation(receipt)
            raise _professional.ProfessionalUIExpectationError(
                ACTION,
                "dimension_mismatch",
            )
        changed_ratio = float(comparison["changed_pixel_ratio"] or 0.0)
        limit = float(step.get("max_changed_pixel_ratio"))
        if changed_ratio > limit:
            receipt["status"] = "VIOLATION_OBSERVED"
            receipt["reason_code"] = "UI_VISUAL_CHANGED_PIXEL_BUDGET_EXCEEDED"
            _append_observation(receipt)
            actual_ppm = int(round(changed_ratio * 1_000_000))
            limit_ppm = int(round(limit * 1_000_000))
            raise _professional.ProfessionalUIExpectationError(
                ACTION,
                f"changed_pixel_ratio_ppm_{actual_ppm}_limit_{limit_ppm}",
            )
        _append_observation(receipt)
        return receipt
    except _professional.ProfessionalUIExpectationError:
        raise
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, VisualBaselineObservationError)
            else f"UI_VISUAL_OBSERVATION_ERROR:{type(exc).__name__}"
        )
        _append_observation({
            **observation_base,
            "status": "INDETERMINATE",
            "reason_code": reason,
            "error_fingerprint": _fingerprint(f"{type(exc).__name__}:{str(exc)}"),
        })
        if isinstance(exc, VisualBaselineObservationError):
            raise
        raise VisualBaselineObservationError(reason) from exc


def _source_visual_gaps(expectations: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for index, step in enumerate(expectations, start=1):
        if _text(step.get("action")).lower() != ACTION:
            continue
        prefix = f"{ACTION}[{index}]"
        try:
            _validate_visual_step(copy.deepcopy(step))
        except Exception as exc:
            missing.append(f"{prefix}.{str(exc)}")
    return missing


def _adapter_with_visual_context(
    project_id: str,
    request: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str,
) -> dict[str, Any]:
    original = getattr(_adapter, _ORIGINAL_ADAPTER)
    token = _RUNTIME_CONTEXT.set({
        "root": str(Path(root)),
        "project": _professional._browser._safe_project(project_id),
        "request_id": _text(request.get("request_id")),
    })
    try:
        return original(
            project_id,
            request,
            runtime_contract,
            root=root,
            run_id=run_id,
        )
    finally:
        _RUNTIME_CONTEXT.reset(token)


def _observer_with_visual_receipts(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    original = getattr(_formal, _ORIGINAL_OBSERVER)
    token = _OBSERVATIONS.set([])
    try:
        receipt = original(envelope)
        observations = [copy.deepcopy(row) for row in _OBSERVATIONS.get()]
    finally:
        _OBSERVATIONS.reset(token)
    if not observations:
        return receipt
    evidence = copy.deepcopy(_dict(receipt.get("evidence")))
    ui_evidence = copy.deepcopy(_dict(evidence.get(_formal.EVIDENCE_KEY)))
    ui_evidence["visual_baseline_observations"] = observations
    ui_evidence["visual_baseline_observation_count"] = len(observations)
    ui_evidence["visual_ai_judgement_consumed"] = False
    evidence[_formal.EVIDENCE_KEY] = ui_evidence
    return _receipt(
        observer_id=_text(receipt.get("observer_id")) or _formal.OBSERVER_ID,
        status=_text(receipt.get("status")) or "INDETERMINATE",
        reason_code=_text(receipt.get("reason_code")),
        evidence=evidence,
        campaign_id=_text(receipt.get("campaign_id")),
        execution_id=_text(receipt.get("execution_id")),
    )


def install_professional_ui_visual_baseline() -> None:
    if getattr(_professional, _INSTALL_MARKER, False):
        return
    original_validate = getattr(
        _professional,
        _ORIGINAL_VALIDATE_STEP,
        _professional._validate_professional_step,
    )
    original_execute = getattr(
        _professional,
        _ORIGINAL_EXECUTE,
        _professional._execute_expectation,
    )
    original_adapter = getattr(
        _adapter,
        _ORIGINAL_ADAPTER,
        _adapter._playwright_request_result,
    )
    original_source_gaps = getattr(
        _source_parser,
        _ORIGINAL_SOURCE_GAPS,
        _source_parser._expectation_structure_gaps,
    )
    setattr(_professional, _ORIGINAL_VALIDATE_STEP, original_validate)
    setattr(_professional, _ORIGINAL_EXECUTE, original_execute)
    setattr(_adapter, _ORIGINAL_ADAPTER, original_adapter)
    setattr(_source_parser, _ORIGINAL_SOURCE_GAPS, original_source_gaps)

    from . import observer_contracts_base as _observers

    original_observer = _observers._REGISTERED_OBSERVER_HANDLERS.get(
        _formal.OBSERVER_ID
    )
    if not callable(original_observer):
        raise RuntimeError("formal_ui_observer_handler_missing")
    setattr(_formal, _ORIGINAL_OBSERVER, original_observer)

    def validate_with_visual(raw: dict[str, Any], action: str) -> None:
        if action == ACTION:
            _validate_visual_step(raw)
            return
        original_validate(raw, action)

    def execute_with_visual(
        *,
        page: Any,
        step: dict[str, Any],
        console: list[dict[str, Any]],
        network: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if _text(step.get("action")).lower() == ACTION:
            return _execute_visual_baseline(page, step)
        return original_execute(
            page=page,
            step=step,
            console=console,
            network=network,
        )

    def source_gaps_with_visual(
        expectations: list[dict[str, Any]],
    ) -> list[str]:
        return [
            *original_source_gaps(expectations),
            *_source_visual_gaps(expectations),
        ]

    _professional.PROFESSIONAL_EXPECTATIONS = frozenset({
        *_professional.PROFESSIONAL_EXPECTATIONS,
        ACTION,
    })
    _professional.READ_ONLY_ACTIONS = frozenset({
        *_professional.READ_ONLY_ACTIONS,
        ACTION,
    })
    _professional._validate_professional_step = validate_with_visual
    _professional._execute_expectation = execute_with_visual
    _formal._SUPPORTED_EXPECTATIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _guard._READ_ONLY_ACTIONS = _professional.READ_ONLY_ACTIONS
    _overlay._EXPECTATION_ACTIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _source_binding._EXPECTATION_ACTIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _source_parser._EXPECTATION_ACTIONS = frozenset({
        *_source_parser._EXPECTATION_ACTIONS,
        ACTION,
    })
    _source_parser._ALLOWED_ACTIONS = frozenset({
        *_source_parser._ALLOWED_ACTIONS,
        ACTION,
    })
    _source_parser._expectation_structure_gaps = source_gaps_with_visual
    _adapter._playwright_request_result = _adapter_with_visual_context
    _observers._REGISTERED_OBSERVER_HANDLERS[_formal.OBSERVER_ID] = (
        _observer_with_visual_receipts
    )
    setattr(_professional, _INSTALL_MARKER, True)


__all__ = [
    "ACTION",
    "BASELINE_SCOPE",
    "COMPARISON_METHOD",
    "install_professional_ui_visual_baseline",
]
