"""Evidence privacy for read-only formal visual baseline execution.

The generic read-only browser executor persists an unmasked final screenshot,
HAR, Playwright trace, console text and network URLs. Those artifacts are useful
for broad smoke diagnostics but are not acceptable defaults for source-declared
visual regression. This guard gives visual plans a stricter evidence profile:

* browser contexts never record HAR or trace;
* every screenshot destined for disk is captured to memory, masked using the
  union of source-declared visual dynamic regions and automatic secret regions,
  then written once in masked form;
* console text, network URLs and untyped runtime exceptions are fingerprinted in
  the first JSON serialization;
* formal visual comparison receipts remain unchanged and authoritative.
"""
from __future__ import annotations

import contextvars
import copy
import json
from pathlib import Path
from typing import Any

from . import auto_browser_setup as _auto_browser
from . import professional_ui_readonly as _professional
from . import professional_ui_visual_baseline as _visual

EVIDENCE_POLICY = "masked_visual_screenshots_no_trace_no_har"
_INSTALL_MARKER = "_qualibug_visual_evidence_privacy_installed"
_ORIGINAL_ENSURE_BROWSER = "_qualibug_ensure_browser_before_visual_privacy"
_ORIGINAL_EXECUTOR = "_qualibug_readonly_executor_before_visual_privacy"
ORIGINAL_JSON = "_qualibug_readonly_json_before_visual_privacy"
_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_visual_evidence_privacy_context",
    default={},
)
_TYPED_REASON_PREFIXES = ("UI_EXPECTATION_UNSATISFIED:",)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _visual_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(row)
        for row in _list(_dict(plan).get("steps"))
        if isinstance(row, dict)
        and _text(row.get("action")).lower() == _visual.ACTION
    ]


def _combined_mask_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
    selectors: list[str] = []
    intents: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []
    for step in steps:
        selectors.extend(_text(value) for value in _list(step.get("mask_selectors")))
        intents.extend(
            copy.deepcopy(value)
            for value in _list(step.get("mask_locator_intents"))
            if isinstance(value, dict)
        )
        regions.extend(
            copy.deepcopy(value)
            for value in _list(step.get("mask_regions"))
            if isinstance(value, dict)
        )
    return {
        "mask_selectors": list(dict.fromkeys(value for value in selectors if value)),
        "mask_locator_intents": intents,
        "mask_regions": regions,
    }


def _safe_reason(value: Any) -> str:
    reason = _text(value)
    if not reason:
        return ""
    if reason.startswith(_TYPED_REASON_PREFIXES):
        return reason
    return "UI_VISUAL_RUNTIME_ERROR:" + _visual._fingerprint(reason)[:20]


def _scrub_result(result: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(_dict(result))
    row["reason"] = _safe_reason(row.get("reason"))
    row["console"] = [
        {
            "type": _text(value.get("type")),
            "text_fingerprint": _visual._fingerprint(_text(value.get("text"))),
        }
        for value in _list(row.get("console"))
        if isinstance(value, dict)
    ]
    row["network"] = [
        {
            "method": _text(value.get("method")),
            "status": int(value.get("status") or 0),
            "url_fingerprint": _visual._fingerprint(_text(value.get("url"))),
        }
        for value in _list(row.get("network"))
        if isinstance(value, dict)
    ]
    row["trace_ref"] = ""
    row["har_ref"] = ""
    row["visual_evidence_privacy"] = {
        "policy": EVIDENCE_POLICY,
        "har_persisted": False,
        "trace_persisted": False,
        "raw_console_text_persisted": False,
        "raw_network_url_persisted": False,
        "runtime_exception_text_persisted": False,
        "persisted_screenshots_masked_before_first_write": True,
        "raw_visual_comparison_pixels_embedded_in_json": False,
    }
    return row


def _looks_like_browser_result(value: Any) -> bool:
    row = _dict(value)
    return bool(
        _CONTEXT.get()
        and "professional_ui_expectation_count" in row
        and "execution_mode" in row
        and "steps" in row
        and "duration_ms" in row
        and "status" in row
    )


class _VisualJsonProxy:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def dumps(self, value: Any, *args: Any, **kwargs: Any) -> str:
        safe = _scrub_result(value) if _looks_like_browser_result(value) else value
        return self._delegate.dumps(safe, *args, **kwargs)


class _NoopTracing:
    def start(self, *args: Any, **kwargs: Any) -> None:
        return None

    def stop(self, *args: Any, **kwargs: Any) -> None:
        return None


class _VisualPrivacyPage:
    def __init__(self, page: Any, mask_step: dict[str, Any]) -> None:
        self._page = page
        self._mask_step = mask_step

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)

    def screenshot(self, *args: Any, **kwargs: Any) -> bytes:
        options = dict(kwargs)
        destination = options.pop("path", None)
        # Never let Playwright persist the raw screenshot. It returns bytes first.
        raw = self._page.screenshot(*args, **options)
        if isinstance(raw, bytearray):
            raw = bytes(raw)
        if not isinstance(raw, bytes):
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_SCREENSHOT_BYTES_MISSING"
            )
        if destination is None:
            return raw
        image = _visual._open_rgba(raw)
        boxes = _visual._mask_boxes(self._page, self._mask_step)
        _visual._apply_masks(image, boxes)
        output = Path(str(destination))
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG")
        buffer = __import__("io").BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class _VisualPrivacyContext:
    def __init__(self, context: Any, mask_step: dict[str, Any]) -> None:
        self._context = context
        self._mask_step = mask_step
        self.tracing = _NoopTracing()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    def new_page(self, *args: Any, **kwargs: Any) -> _VisualPrivacyPage:
        return _VisualPrivacyPage(
            self._context.new_page(*args, **kwargs),
            self._mask_step,
        )


class _VisualPrivacyBrowser:
    def __init__(self, browser: Any, mask_step: dict[str, Any]) -> None:
        self._browser = browser
        self._mask_step = mask_step

    def __getattr__(self, name: str) -> Any:
        return getattr(self._browser, name)

    def new_context(self, *args: Any, **kwargs: Any) -> _VisualPrivacyContext:
        options = dict(kwargs)
        options.pop("record_har_path", None)
        options.pop("record_har_content", None)
        options.pop("record_har_mode", None)
        return _VisualPrivacyContext(
            self._browser.new_context(*args, **options),
            self._mask_step,
        )


def _rewrite_artifact(result: dict[str, Any], root: Path) -> None:
    artifact_ref = _text(result.get("artifact_dir"))
    if not artifact_ref:
        return
    root_path = Path(root).resolve()
    artifact_dir = (root_path / artifact_ref).resolve()
    if root_path != artifact_dir and root_path not in artifact_dir.parents:
        return
    output = artifact_dir / "browser_execution.json"
    if output.is_file():
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )


def install_visual_evidence_privacy() -> None:
    if getattr(_professional, _INSTALL_MARKER, False):
        return
    original_ensure = getattr(
        _auto_browser,
        _ORIGINAL_ENSURE_BROWSER,
        _auto_browser.ensure_browser,
    )
    original_executor = getattr(
        _professional,
        _ORIGINAL_EXECUTOR,
        _professional.execute_professional_browser_plan,
    )
    original_json = getattr(
        _professional,
        ORIGINAL_JSON,
        _professional.json,
    )
    setattr(_auto_browser, _ORIGINAL_ENSURE_BROWSER, original_ensure)
    setattr(_professional, _ORIGINAL_EXECUTOR, original_executor)
    setattr(_professional, ORIGINAL_JSON, original_json)

    def ensure_browser_with_visual_privacy(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        runtime, browser_or_error = original_ensure(*args, **kwargs)
        context = _dict(_CONTEXT.get())
        if runtime is None or not context:
            return runtime, browser_or_error
        browser = _VisualPrivacyBrowser(
            browser_or_error,
            _dict(context.get("mask_step")),
        )
        return runtime, browser

    def execute_with_visual_privacy(
        project_id: str,
        plan: dict[str, Any],
        runtime_contract: dict[str, Any],
        *,
        root: Path,
        run_id: str = "",
    ) -> dict[str, Any]:
        visual_steps = _visual_steps(plan)
        if not visual_steps:
            return original_executor(
                project_id,
                plan,
                runtime_contract,
                root=root,
                run_id=run_id,
            )
        token = _CONTEXT.set({
            "mask_step": _combined_mask_step(visual_steps),
            "project": _text(project_id),
            "run_id": _text(run_id),
        })
        try:
            result = original_executor(
                project_id,
                plan,
                runtime_contract,
                root=root,
                run_id=run_id,
            )
        finally:
            _CONTEXT.reset(token)
        scrubbed = _scrub_result(result)
        _rewrite_artifact(scrubbed, Path(root))
        return scrubbed

    _auto_browser.ensure_browser = ensure_browser_with_visual_privacy
    _professional.json = _VisualJsonProxy(original_json)
    _professional.execute_professional_browser_plan = execute_with_visual_privacy
    _professional._browser.execute_browser_plan = execute_with_visual_privacy
    setattr(_professional, _INSTALL_MARKER, True)


__all__ = [
    "EVIDENCE_POLICY",
    "install_visual_evidence_privacy",
]
