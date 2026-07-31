"""Professional source-declared read-only UI/UX expectations.

This installer extends the existing formal UI authority; it does not create a
second finding path. Every new assertion remains:

    source contract -> formal UI obligation -> governed browser execution
    -> typed observer receipt -> Contract Oracle -> Delivery Gate

The first professional increment is deliberately read-only. It covers rendered
state, accessibility semantics, form state, element geometry, viewport fit,
occlusion, horizontal overflow, console errors and failed network responses.
It does not click, fill, submit or mutate customer state.

Observed DOM/text/attribute values are represented by fingerprints and numeric
measurements in step receipts. Raw page text, input values, response bodies,
headers and tokens are never copied into formal evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from . import browser_execution as _browser
from . import formal_ui_surface as _formal
from . import formal_ui_surface_guard as _guard
from .multimodal_locator import (
    MultimodalLocatorError,
    validate_locator_intent,
)

INSTALL_MARKER = "_qualibug_professional_ui_readonly_installed"
ORIGINAL_EXECUTOR = "_qualibug_original_browser_executor_before_professional_ui"
ORIGINAL_VALIDATOR = "_qualibug_original_browser_validator_before_professional_ui"
ORIGINAL_DESCRIPTOR = "_qualibug_original_ui_descriptor_before_professional_ui"
ORIGINAL_FAILURE_CLASSIFIER = (
    "_qualibug_original_ui_failure_classifier_before_professional_ui"
)

PROFESSIONAL_EXPECTATIONS = frozenset({
    "expect_text",
    "expect_url",
    "expect_visible",
    "expect_hidden",
    "expect_enabled",
    "expect_disabled",
    "expect_value",
    "expect_checked",
    "expect_unchecked",
    "expect_count",
    "expect_attribute",
    "expect_css",
    "expect_role",
    "expect_accessible_name",
    "expect_dimensions",
    "expect_in_viewport",
    "expect_not_obscured",
    "expect_no_horizontal_overflow",
    "expect_no_console_errors",
    "expect_no_failed_requests",
})
READ_ONLY_ACTIONS = frozenset({
    "goto",
    "wait_for_load",
    "screenshot",
    *PROFESSIONAL_EXPECTATIONS,
})
LOCATOR_ACTIONS = frozenset({
    "expect_text",
    "expect_visible",
    "expect_hidden",
    "expect_enabled",
    "expect_disabled",
    "expect_value",
    "expect_checked",
    "expect_unchecked",
    "expect_count",
    "expect_attribute",
    "expect_css",
    "expect_role",
    "expect_accessible_name",
    "expect_dimensions",
    "expect_in_viewport",
    "expect_not_obscured",
})
PAGE_ACTIONS = frozenset({
    "expect_url",
    "expect_no_horizontal_overflow",
    "expect_no_console_errors",
    "expect_no_failed_requests",
})
_MATCH_MODES = frozenset({"equals", "contains", "regex"})
_MAX_REGEX_LENGTH = 500
_MAX_EXPECTED_TEXT_LENGTH = 4000


class ProfessionalUIExpectationError(AssertionError):
    """A source-declared read-only expectation was observed and not satisfied."""

    def __init__(self, action: str, code: str) -> None:
        super().__init__(f"UI_EXPECTATION_UNSATISFIED:{action}:{code}")
        self.action = action
        self.code = code


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = _MAX_EXPECTED_TEXT_LENGTH) -> str:
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


def _match_mode(step: dict[str, Any]) -> str:
    mode = _text(step.get("match") or "equals").lower()
    if mode not in _MATCH_MODES:
        raise _browser.BrowserExecutionError(f"browser_match_mode_unsupported:{mode}")
    return mode


def _validate_expected(step: dict[str, Any], key: str = "expected") -> str:
    expected = _text(step.get(key))
    if not expected:
        raise _browser.BrowserExecutionError(
            f"browser_expectation_value_missing:{_text(step.get('action'))}:{key}"
        )
    if _match_mode(step) == "regex":
        if len(expected) > _MAX_REGEX_LENGTH:
            raise _browser.BrowserExecutionError("browser_expectation_regex_too_long")
        try:
            re.compile(expected)
        except re.error as exc:
            raise _browser.BrowserExecutionError(
                "browser_expectation_regex_invalid"
            ) from exc
    return expected


def _matches(actual: str, expected: str, mode: str) -> bool:
    if mode == "equals":
        return actual == expected
    if mode == "contains":
        return expected in actual
    return re.search(expected, actual) is not None


def _validate_number(step: dict[str, Any], key: str) -> float | None:
    if key not in step:
        return None
    value = step.get(key)
    if isinstance(value, bool):
        raise _browser.BrowserExecutionError(
            f"browser_expectation_number_invalid:{key}"
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _browser.BrowserExecutionError(
            f"browser_expectation_number_invalid:{key}"
        ) from exc
    if number < 0:
        raise _browser.BrowserExecutionError(
            f"browser_expectation_number_negative:{key}"
        )
    return number


def _validate_professional_step(raw: dict[str, Any], action: str) -> None:
    if action in LOCATOR_ACTIONS:
        selector = _text(raw.get("selector"))
        intent = raw.get("locator_intent")
        if not selector and not isinstance(intent, dict):
            raise _browser.BrowserExecutionError(
                f"browser_locator_missing:{action}"
            )
        if selector and intent:
            raise _browser.BrowserExecutionError(
                f"browser_locator_authority_ambiguous:{action}"
            )
        if isinstance(intent, dict):
            try:
                raw["locator_intent"] = validate_locator_intent(intent)
            except MultimodalLocatorError as exc:
                raise _browser.BrowserExecutionError(str(exc)) from exc
    if action in {"expect_text", "expect_value", "expect_accessible_name"}:
        _validate_expected(raw, "text" if action == "expect_text" else "expected")
    elif action == "expect_attribute":
        if not _text(raw.get("name")):
            raise _browser.BrowserExecutionError("browser_attribute_name_missing")
        _validate_expected(raw)
    elif action == "expect_css":
        if not _text(raw.get("property")):
            raise _browser.BrowserExecutionError("browser_css_property_missing")
        _validate_expected(raw)
    elif action == "expect_role":
        _validate_expected(raw)
    elif action == "expect_count":
        exact = _validate_number(raw, "count")
        minimum = _validate_number(raw, "min_count")
        maximum = _validate_number(raw, "max_count")
        if exact is None and minimum is None and maximum is None:
            raise _browser.BrowserExecutionError("browser_count_expectation_missing")
        if exact is not None and (minimum is not None or maximum is not None):
            raise _browser.BrowserExecutionError("browser_count_authority_ambiguous")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise _browser.BrowserExecutionError("browser_count_range_invalid")
    elif action == "expect_dimensions":
        keys = ("min_width", "max_width", "min_height", "max_height")
        values = {key: _validate_number(raw, key) for key in keys}
        if all(value is None for value in values.values()):
            raise _browser.BrowserExecutionError("browser_dimension_expectation_missing")
        if (
            values["min_width"] is not None
            and values["max_width"] is not None
            and values["min_width"] > values["max_width"]
        ):
            raise _browser.BrowserExecutionError("browser_width_range_invalid")
        if (
            values["min_height"] is not None
            and values["max_height"] is not None
            and values["min_height"] > values["max_height"]
        ):
            raise _browser.BrowserExecutionError("browser_height_range_invalid")
    elif action == "expect_no_horizontal_overflow":
        _validate_number(raw, "tolerance_px")
    elif action == "expect_no_failed_requests":
        threshold = _validate_number(raw, "status_threshold")
        if threshold is not None and not 400 <= threshold <= 599:
            raise _browser.BrowserExecutionError(
                "browser_network_status_threshold_invalid"
            )
        patterns = _list(raw.get("ignore_url_patterns"))
        for pattern in patterns:
            text = _text(pattern, limit=_MAX_REGEX_LENGTH)
            try:
                re.compile(text)
            except re.error as exc:
                raise _browser.BrowserExecutionError(
                    "browser_network_ignore_pattern_invalid"
                ) from exc


def validate_professional_browser_plan(
    plan: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise _browser.BrowserExecutionError("browser_plan_invalid")
    if _text(_dict(runtime_contract).get("status")) != "approved":
        raise _browser.BrowserExecutionError("browser_runtime_contract_not_approved")
    base_url = _text(_dict(runtime_contract).get("approved_base_url"))
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _browser.BrowserExecutionError("browser_approved_base_url_invalid")
    mode = _text(plan.get("execution_mode") or "safe_read_only")
    if mode != "safe_read_only":
        # This professional increment is intentionally read-only. The governed
        # interaction/cleanup layer is a separate authority.
        raise _browser.BrowserExecutionError(
            "professional_ui_readonly_mode_required"
        )
    normalized: list[dict[str, Any]] = []
    for position, source in enumerate(_browser._as_steps(plan), start=1):
        raw = copy.deepcopy(source)
        action = _text(raw.get("action")).lower()
        if action not in READ_ONLY_ACTIONS:
            raise _browser.BrowserExecutionError(
                f"browser_action_unsupported:{action or position}"
            )
        if action == "goto":
            target = _text(raw.get("url"))
            if not target:
                raise _browser.BrowserExecutionError("browser_goto_url_missing")
            resolved = urljoin(base_url.rstrip("/") + "/", target)
            if not _browser._same_approved_origin(base_url, resolved):
                raise _browser.BrowserExecutionError(
                    "browser_target_outside_approved_base_url"
                )
            raw["url"] = resolved
        elif action == "expect_url":
            if not _text(raw.get("pattern") or raw.get("url")):
                raise _browser.BrowserExecutionError(
                    "browser_url_expectation_missing"
                )
        elif action in PROFESSIONAL_EXPECTATIONS:
            _validate_professional_step(raw, action)
        raw["action"] = action
        raw["step_index"] = position
        normalized.append(raw)
    return {
        "execution_mode": "safe_read_only",
        "base_url": base_url.rstrip("/"),
        "steps": normalized,
    }


def _candidate(page: Any, step: dict[str, Any]) -> tuple[Any, str]:
    selector = _text(step.get("selector"))
    if selector:
        return page.locator(selector), "source_css"
    intent = validate_locator_intent(_dict(step.get("locator_intent")))
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
    raise _browser.BrowserExecutionError("locator_intent_not_resolvable")


def _locator_receipt(locator: Any, strategy: str, step: dict[str, Any]) -> dict[str, Any]:
    count = int(locator.count())
    receipt: dict[str, Any] = {
        "locator_strategy": strategy,
        "locator_intent_fingerprint": _fingerprint(
            step.get("locator_intent") or step.get("selector")
        ),
        "matched_count": count,
    }
    if count == 1:
        box = locator.bounding_box()
        receipt.update({
            "visible": bool(locator.is_visible()),
            "enabled": bool(locator.is_enabled()),
            "bounding_box": {
                key: round(float(_dict(box).get(key) or 0), 3)
                for key in ("x", "y", "width", "height")
            } if isinstance(box, dict) else {},
            "dom_fingerprint": _fingerprint(locator.evaluate(
                """el => ({
                  tag: (el.tagName || '').toLowerCase(),
                  id: el.id || '',
                  role: el.getAttribute('role') || '',
                  ariaLabel: el.getAttribute('aria-label') || '',
                  type: el.getAttribute('type') || ''
                })"""
            )),
        })
    return receipt


def _require_unique(locator: Any, action: str) -> None:
    count = int(locator.count())
    if count == 0:
        raise ProfessionalUIExpectationError(action, "target_missing")
    if count != 1:
        raise ProfessionalUIExpectationError(action, f"target_ambiguous_{count}")


def _accessible_name(locator: Any) -> str:
    value = locator.evaluate(
        r"""el => {
          const labelledBy = (el.getAttribute('aria-labelledby') || '')
            .split(/\s+/).filter(Boolean)
            .map(id => document.getElementById(id)?.innerText || '')
            .join(' ').trim();
          const labels = el.labels ? Array.from(el.labels)
            .map(label => label.innerText || label.textContent || '')
            .join(' ').trim() : '';
          return (
            el.getAttribute('aria-label') || labelledBy || labels ||
            el.getAttribute('alt') || el.getAttribute('title') ||
            el.innerText || el.textContent || ''
          ).trim();
        }"""
    )
    return _text(value)


def _computed_role(locator: Any) -> str:
    return _text(locator.evaluate(
        """el => {
          const explicit = el.getAttribute('role');
          if (explicit) return explicit;
          const tag = (el.tagName || '').toLowerCase();
          const type = (el.getAttribute('type') || '').toLowerCase();
          if (tag === 'button') return 'button';
          if (tag === 'a' && el.hasAttribute('href')) return 'link';
          if (tag === 'select') return 'combobox';
          if (tag === 'textarea') return 'textbox';
          if (tag === 'input' && ['button','submit','reset'].includes(type)) return 'button';
          if (tag === 'input' && type === 'checkbox') return 'checkbox';
          if (tag === 'input' && type === 'radio') return 'radio';
          if (tag === 'input') return 'textbox';
          if (/^h[1-6]$/.test(tag)) return 'heading';
          if (tag === 'img') return 'img';
          return '';
        }"""
    ))


def _assert_match(action: str, actual: str, step: dict[str, Any], key: str = "expected") -> None:
    expected = _text(step.get(key))
    mode = _match_mode(step)
    if not _matches(actual, expected, mode):
        raise ProfessionalUIExpectationError(action, "value_mismatch")


def _execute_expectation(
    *,
    page: Any,
    step: dict[str, Any],
    console: list[dict[str, Any]],
    network: list[dict[str, Any]],
) -> dict[str, Any]:
    action = step["action"]
    receipt: dict[str, Any] = {
        "expectation": action,
        "source_expectation_fingerprint": _fingerprint(step),
        "raw_observed_value_included": False,
    }
    if action == "expect_url":
        page.wait_for_url(
            _text(step.get("pattern") or step.get("url")),
            timeout=int(step.get("timeout_ms") or 10_000),
        )
        receipt["observed_url_fingerprint"] = _fingerprint(page.url)
        return receipt
    if action == "expect_no_horizontal_overflow":
        metrics = page.evaluate(
            """() => ({
              scrollWidth: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
              clientWidth: document.documentElement.clientWidth
            })"""
        )
        tolerance = float(step.get("tolerance_px") or 0)
        overflow = float(_dict(metrics).get("scrollWidth") or 0) - float(
            _dict(metrics).get("clientWidth") or 0
        )
        receipt["horizontal_overflow_px"] = round(max(0.0, overflow), 3)
        receipt["tolerance_px"] = tolerance
        if overflow > tolerance:
            raise ProfessionalUIExpectationError(action, "overflow_detected")
        return receipt
    if action == "expect_no_console_errors":
        ignore = [re.compile(_text(value, limit=_MAX_REGEX_LENGTH)) for value in _list(step.get("ignore_patterns"))]
        errors = [
            row for row in console
            if _text(_dict(row).get("type")).lower() == "error"
            and not any(pattern.search(_text(_dict(row).get("text"))) for pattern in ignore)
        ]
        receipt["console_error_count"] = len(errors)
        receipt["console_error_fingerprints"] = [
            _fingerprint(_text(_dict(row).get("text"))) for row in errors[:20]
        ]
        if errors:
            raise ProfessionalUIExpectationError(action, "console_errors_observed")
        return receipt
    if action == "expect_no_failed_requests":
        threshold = int(step.get("status_threshold") or 400)
        ignore = [re.compile(_text(value, limit=_MAX_REGEX_LENGTH)) for value in _list(step.get("ignore_url_patterns"))]
        failures = [
            row for row in network
            if int(_dict(row).get("status") or 0) >= threshold
            and not any(pattern.search(_text(_dict(row).get("url"))) for pattern in ignore)
        ]
        receipt["failed_request_count"] = len(failures)
        receipt["failed_request_fingerprints"] = [
            _fingerprint({
                "url": _text(_dict(row).get("url")),
                "status": int(_dict(row).get("status") or 0),
                "method": _text(_dict(row).get("method")),
            })
            for row in failures[:50]
        ]
        if failures:
            raise ProfessionalUIExpectationError(action, "failed_requests_observed")
        return receipt

    locator, strategy = _candidate(page, step)
    receipt["locator"] = _locator_receipt(locator, strategy, step)
    if action == "expect_count":
        count = int(locator.count())
        if "count" in step and count != int(step["count"]):
            raise ProfessionalUIExpectationError(action, "count_mismatch")
        if "min_count" in step and count < int(step["min_count"]):
            raise ProfessionalUIExpectationError(action, "count_below_minimum")
        if "max_count" in step and count > int(step["max_count"]):
            raise ProfessionalUIExpectationError(action, "count_above_maximum")
        receipt["observed_count"] = count
        return receipt

    _require_unique(locator, action)
    timeout = int(step.get("timeout_ms") or 10_000)
    if action == "expect_visible":
        locator.wait_for(state="visible", timeout=timeout)
    elif action == "expect_hidden":
        locator.wait_for(state="hidden", timeout=timeout)
    elif action == "expect_enabled":
        locator.wait_for(state="visible", timeout=timeout)
        if locator.is_enabled() is not True:
            raise ProfessionalUIExpectationError(action, "element_disabled")
    elif action == "expect_disabled":
        if locator.is_enabled() is not False:
            raise ProfessionalUIExpectationError(action, "element_enabled")
    elif action == "expect_text":
        locator.wait_for(state="visible", timeout=timeout)
        _assert_match(action, _text(locator.inner_text()), step, key="text")
        receipt["observed_text_fingerprint"] = _fingerprint(_text(locator.inner_text()))
    elif action == "expect_value":
        value = _text(locator.input_value(timeout=timeout))
        _assert_match(action, value, step)
        receipt["observed_value_fingerprint"] = _fingerprint(value)
    elif action in {"expect_checked", "expect_unchecked"}:
        checked = bool(locator.is_checked(timeout=timeout))
        expected = action == "expect_checked"
        if checked is not expected:
            raise ProfessionalUIExpectationError(action, "checked_state_mismatch")
        receipt["observed_checked"] = checked
    elif action == "expect_attribute":
        value = _text(locator.get_attribute(_text(step.get("name"))))
        _assert_match(action, value, step)
        receipt["observed_attribute_fingerprint"] = _fingerprint(value)
    elif action == "expect_css":
        prop = _text(step.get("property"))
        value = _text(locator.evaluate(
            "(el, prop) => getComputedStyle(el).getPropertyValue(prop)",
            prop,
        ))
        _assert_match(action, value, step)
        receipt["observed_css_fingerprint"] = _fingerprint(value)
    elif action == "expect_role":
        role = _computed_role(locator)
        _assert_match(action, role, step)
        receipt["observed_role_fingerprint"] = _fingerprint(role)
    elif action == "expect_accessible_name":
        name = _accessible_name(locator)
        _assert_match(action, name, step)
        receipt["observed_accessible_name_fingerprint"] = _fingerprint(name)
    elif action == "expect_dimensions":
        box = locator.bounding_box()
        if not isinstance(box, dict):
            raise ProfessionalUIExpectationError(action, "visual_bounds_missing")
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
        checks = {
            "min_width": width >= float(step.get("min_width")) if "min_width" in step else True,
            "max_width": width <= float(step.get("max_width")) if "max_width" in step else True,
            "min_height": height >= float(step.get("min_height")) if "min_height" in step else True,
            "max_height": height <= float(step.get("max_height")) if "max_height" in step else True,
        }
        receipt["width"] = round(width, 3)
        receipt["height"] = round(height, 3)
        if not all(checks.values()):
            raise ProfessionalUIExpectationError(action, "dimension_budget_exceeded")
    elif action == "expect_in_viewport":
        visible = bool(locator.evaluate(
            """el => {
              const r = el.getBoundingClientRect();
              return r.top >= 0 && r.left >= 0 &&
                r.bottom <= window.innerHeight && r.right <= window.innerWidth;
            }"""
        ))
        receipt["within_viewport"] = visible
        if not visible:
            raise ProfessionalUIExpectationError(action, "outside_viewport")
    elif action == "expect_not_obscured":
        unobscured = bool(locator.evaluate(
            """el => {
              const r = el.getBoundingClientRect();
              if (!r.width || !r.height) return false;
              const x = r.left + r.width / 2;
              const y = r.top + r.height / 2;
              const top = document.elementFromPoint(x, y);
              return top === el || !!(top && el.contains(top));
            }"""
        ))
        receipt["center_point_unobscured"] = unobscured
        if not unobscured:
            raise ProfessionalUIExpectationError(action, "element_obscured")
    return receipt


def execute_professional_browser_plan(
    project_id: str,
    plan: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str = "",
) -> dict[str, Any]:
    validated = validate_professional_browser_plan(plan, runtime_contract)
    project = _browser._safe_project(project_id)
    execution_id = _browser._safe_run_id(run_id)
    artifact_dir = Path(root) / "platform_workspace" / project / "browser_runs" / execution_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    playwright_runtime = None
    browser = None
    browser_error = ""
    try:
        from .auto_browser_setup import ensure_browser
    except Exception:
        ensure_browser = None
    if ensure_browser is not None:
        try:
            playwright_runtime, browser_or_error = ensure_browser(
                headless=True,
                timeout=30_000,
            )
        except Exception as exc:  # noqa: BLE001
            browser_or_error = f"browser_runtime_bootstrap_failed:{type(exc).__name__}"
            playwright_runtime = None
        if playwright_runtime is not None:
            browser = browser_or_error
        else:
            browser_error = _text(browser_or_error)
    if browser is None and not browser_error:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            browser_error = "playwright_import_missing"
        else:
            try:
                playwright_runtime = sync_playwright().start()
                browser = playwright_runtime.chromium.launch(headless=True)
            except Exception as exc:  # noqa: BLE001
                browser_error = f"{type(exc).__name__}:{str(exc)[:300]}"
    if browser is None:
        return {
            "status": "blocked",
            "reason": f"BROWSER_RUNTIME_UNAVAILABLE:{browser_error or 'unknown'}",
            "execution_status": "not_executed",
            "confirmation_status": "blocked",
            "artifact_dir": str(artifact_dir.relative_to(Path(root))),
            "steps": [],
        }

    started = time.time()
    receipts: list[dict[str, Any]] = []
    console: list[dict[str, str]] = []
    network: list[dict[str, Any]] = []
    trace_path = artifact_dir / "trace.zip"
    har_path = artifact_dir / "network.har"
    screenshot_path = artifact_dir / "final.png"
    status = "executed"
    reason = ""
    context = None
    try:
        context = browser.new_context(
            record_har_path=str(har_path),
            record_har_content="embed",
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.on(
            "console",
            lambda message: console.append({
                "type": message.type,
                "text": message.text[:4000],
            }),
        )
        page.on(
            "response",
            lambda response: network.append({
                "url": _browser._redact_url(response.url),
                "status": response.status,
                "method": response.request.method,
            }),
        )
        for step in validated["steps"]:
            action = step["action"]
            receipt: dict[str, Any] = {
                "step_index": step["step_index"],
                "action": action,
                "started_at_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(),
                ),
            }
            if action == "goto":
                response = page.goto(
                    step["url"],
                    wait_until=_text(step.get("wait_until") or "networkidle"),
                    timeout=int(step.get("timeout_ms") or 30_000),
                )
                receipt.update({
                    "url": _browser._redact_url(step["url"]),
                    "status": response.status if response else 0,
                })
            elif action == "wait_for_load":
                page.wait_for_load_state(
                    _text(step.get("state") or "networkidle"),
                    timeout=int(step.get("timeout_ms") or 30_000),
                )
            elif action == "screenshot":
                output = artifact_dir / f"step_{step['step_index']}.png"
                page.screenshot(
                    path=str(output),
                    full_page=bool(step.get("full_page", True)),
                )
                receipt["screenshot"] = output.name
            else:
                receipt.update(_execute_expectation(
                    page=page,
                    step=step,
                    console=console,
                    network=network,
                ))
            receipts.append(receipt)
        page.screenshot(path=str(screenshot_path), full_page=True)
        context.tracing.stop(path=str(trace_path))
    except ProfessionalUIExpectationError as exc:
        status = "failed"
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        reason = f"{type(exc).__name__}:{str(exc)[:300]}"
    finally:
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if playwright_runtime is not None:
                playwright_runtime.stop()
        except Exception:
            pass

    result = {
        "status": status,
        "reason": reason,
        "execution_status": "executed" if status == "executed" else "failed",
        "confirmation_status": "candidate",
        "execution_mode": validated["execution_mode"],
        "artifact_dir": str(artifact_dir.relative_to(Path(root))),
        "trace_ref": str(trace_path.relative_to(Path(root))) if trace_path.exists() else "",
        "har_ref": str(har_path.relative_to(Path(root))) if har_path.exists() else "",
        "screenshot_ref": str(screenshot_path.relative_to(Path(root))) if screenshot_path.exists() else "",
        "steps": receipts,
        "console": console,
        "network": network,
        "professional_ui_expectation_count": sum(
            1 for step in validated["steps"] if step["action"] in PROFESSIONAL_EXPECTATIONS
        ),
        "duration_ms": int((time.time() - started) * 1000),
    }
    (artifact_dir / "browser_execution.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return result


def _descriptor(step: dict[str, Any]) -> dict[str, Any]:
    action = _text(step.get("action")).lower()
    descriptor: dict[str, Any] = {
        "action": action,
        "selector_fingerprint": _fingerprint(_text(step.get("selector")))
        if _text(step.get("selector"))
        else "",
        "locator_intent_fingerprint": _fingerprint(step.get("locator_intent"))
        if isinstance(step.get("locator_intent"), dict)
        else "",
    }
    for key in (
        "text",
        "expected",
        "name",
        "property",
        "match",
        "pattern",
        "url",
        "count",
        "min_count",
        "max_count",
        "min_width",
        "max_width",
        "min_height",
        "max_height",
        "tolerance_px",
        "status_threshold",
    ):
        if key in step:
            descriptor[key] = copy.deepcopy(step[key])
    if "ignore_patterns" in step:
        descriptor["ignore_pattern_fingerprints"] = [
            _fingerprint(_text(value)) for value in _list(step.get("ignore_patterns"))
        ]
    if "ignore_url_patterns" in step:
        descriptor["ignore_url_pattern_fingerprints"] = [
            _fingerprint(_text(value)) for value in _list(step.get("ignore_url_patterns"))
        ]
    return descriptor


def install_professional_ui_readonly() -> None:
    """Extend the one formal UI authority and the existing browser provider."""
    if getattr(_formal, INSTALL_MARKER, False):
        return
    setattr(
        _browser,
        ORIGINAL_VALIDATOR,
        getattr(_browser, "validate_browser_plan"),
    )
    setattr(
        _browser,
        ORIGINAL_EXECUTOR,
        getattr(_browser, "execute_browser_plan"),
    )
    setattr(
        _formal,
        ORIGINAL_DESCRIPTOR,
        getattr(_formal, "_expectation_descriptor"),
    )
    setattr(
        _formal,
        ORIGINAL_FAILURE_CLASSIFIER,
        getattr(_formal, "_timeout_expectation_failure"),
    )

    original_failure = getattr(_formal, ORIGINAL_FAILURE_CLASSIFIER)

    def classify_professional_failure(
        reason: str,
        failed_step: dict[str, Any],
    ) -> bool:
        if _text(reason).startswith("UI_EXPECTATION_UNSATISFIED:"):
            return _text(failed_step.get("action")).lower() in PROFESSIONAL_EXPECTATIONS
        return original_failure(reason, failed_step)

    _browser.validate_browser_plan = validate_professional_browser_plan
    _browser.execute_browser_plan = execute_professional_browser_plan
    _formal._SUPPORTED_EXPECTATIONS = PROFESSIONAL_EXPECTATIONS
    _formal._expectation_descriptor = _descriptor
    _formal._timeout_expectation_failure = classify_professional_failure
    _guard._READ_ONLY_ACTIONS = READ_ONLY_ACTIONS
    setattr(_formal, INSTALL_MARKER, True)


__all__ = [
    "PROFESSIONAL_EXPECTATIONS",
    "READ_ONLY_ACTIONS",
    "execute_professional_browser_plan",
    "install_professional_ui_readonly",
    "validate_professional_browser_plan",
]
