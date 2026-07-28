"""Governed cross-browser and device-profile execution for formal UI contracts.

The matrix expands one source-declared, safe-read-only UI request into bounded
Playwright profiles.  Every child still passes through the existing professional
browser validator, evidence privacy layer, typed observer and Contract Oracle.
The module never consumes provider findings and never upgrades a partial matrix
into PROPERTY_HELD.

Matrix v1 intentionally excludes governed writes and visual pixel comparison:
replaying a mutation across browsers is a different safety problem, while visual
baselines must be registered per renderer before Firefox/WebKit pixels can be
compared formally.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import importlib.metadata
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import auto_browser_setup as _auto_browser
from . import formal_ui_surface as _formal
from . import ui_execution_adapter as _adapter
from .enterprise_knowledge_center._formal_ui_browser_matrix_guard import (
    AGGREGATION_POLICY,
    SCHEMA_VERSION,
    SUPPORTED_ENGINES,
    normalize_browser_matrix,
)

_INSTALL_RUNTIME_MARKER = "_qualibug_browser_matrix_runtime_installed"
_INSTALL_ADAPTER_MARKER = "_qualibug_browser_matrix_adapter_installed"
_ORIGINAL_ENSURE = "_qualibug_ensure_browser_before_matrix"
_ORIGINAL_NORMALIZE = "_qualibug_normalize_ui_requests_before_matrix"
_ORIGINAL_ADAPTER = "_qualibug_playwright_adapter_before_matrix"
_ORIGINAL_OBSERVER = "_qualibug_ui_observer_before_matrix"
_MAX_ENGINE_INSTALL_SECONDS = 300
_ACTIVE_PROFILE: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_browser_matrix_active_profile",
    default={},
)
_ACTIVE_RUNTIME: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_browser_matrix_active_runtime",
    default={},
)
_LAST_RECEIPT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_browser_matrix_last_receipt",
    default={},
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:20]


def _playwright_version() -> str:
    try:
        return importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        return ""


def _install_engine(engine: str) -> str:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if _auto_browser._is_china_network():
        env["PLAYWRIGHT_DOWNLOAD_HOST"] = env.get(
            "PLAYWRIGHT_DOWNLOAD_HOST",
            _auto_browser._PLAYWRIGHT_MIRROR,
        )
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", engine],
            capture_output=True,
            text=True,
            timeout=_MAX_ENGINE_INSTALL_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"browser_matrix_engine_install_timeout:{engine}"
    except Exception as exc:  # noqa: BLE001
        return f"browser_matrix_engine_install_error:{engine}:{type(exc).__name__}"
    if result.returncode != 0:
        return f"browser_matrix_engine_install_failed:{engine}:{result.returncode}"
    return ""


def _launch_engine(
    profile: dict[str, Any],
    *,
    headless: bool,
    timeout: int,
) -> tuple[Any, Any]:
    engine = _text(profile.get("browser_engine"), limit=20).lower()
    if engine not in SUPPORTED_ENGINES:
        return None, f"browser_matrix_engine_unsupported:{engine or 'missing'}"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        package_error = _auto_browser._install_playwright_pkg()
        if package_error:
            return None, "browser_matrix_playwright_package_unavailable"
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None, "browser_matrix_playwright_import_missing"

    last_error = ""
    for attempt in range(2):
        runtime = None
        try:
            runtime = sync_playwright().start()
            browser_type = getattr(runtime, engine)
            browser = browser_type.launch(
                headless=headless,
                timeout=timeout,
            )
            version = _text(getattr(browser, "version", ""), limit=120)
            _ACTIVE_RUNTIME.set({
                "browser_engine": engine,
                "browser_version": version,
                "playwright_version": _playwright_version(),
                "bundled_engine_required": True,
                "system_browser_fallback_used": False,
            })
            return runtime, _MatrixBrowser(browser, profile)
        except Exception as exc:  # noqa: BLE001
            last_error = f"browser_matrix_engine_launch_failed:{engine}:{type(exc).__name__}"
            try:
                if runtime is not None:
                    runtime.stop()
            except Exception:
                pass
            if attempt == 0:
                install_error = _install_engine(engine)
                if not install_error:
                    continue
                last_error = install_error
            break
    return None, last_error or f"browser_matrix_engine_unavailable:{engine}"


class _MatrixBrowser:
    """Merge source-declared device identity into every browser context."""

    def __init__(self, browser: Any, profile: dict[str, Any]) -> None:
        self._browser = browser
        self._profile = copy.deepcopy(profile)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._browser, name)

    def new_context(self, *args: Any, **kwargs: Any) -> Any:
        options = dict(kwargs)
        profile = self._profile
        options.update({
            "viewport": {
                "width": int(profile["viewport_width"]),
                "height": int(profile["viewport_height"]),
            },
            "device_scale_factor": float(profile["device_scale_factor"]),
            "is_mobile": profile["is_mobile"] is True,
            "has_touch": profile["has_touch"] is True,
            "locale": _text(profile.get("locale"), limit=40),
            "timezone_id": _text(profile.get("timezone_id"), limit=100),
            "color_scheme": _text(profile.get("color_scheme"), limit=20),
            "reduced_motion": _text(profile.get("reduced_motion"), limit=20),
        })
        user_agent = _text(profile.get("user_agent"), limit=500)
        if user_agent:
            options["user_agent"] = user_agent
        return self._browser.new_context(*args, **options)


def _profile_plan(plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    source_steps = [
        copy.deepcopy(row)
        for row in _list(_dict(plan).get("steps"))
        if isinstance(row, dict)
    ]
    viewport_template = next(
        (
            row
            for row in source_steps
            if _text(row.get("action"), limit=80).lower() == "set_viewport"
        ),
        {},
    )
    media_template = next(
        (
            row
            for row in source_steps
            if _text(row.get("action"), limit=80).lower() == "set_media"
        ),
        {},
    )
    steps = [
        row
        for row in source_steps
        if _text(row.get("action"), limit=80).lower()
        not in {"set_viewport", "set_media"}
    ]
    viewport = {
        **viewport_template,
        "action": "set_viewport",
        "width": int(profile["viewport_width"]),
        "height": int(profile["viewport_height"]),
    }
    media = {
        **media_template,
        "action": "set_media",
        "color_scheme": _text(profile.get("color_scheme"), limit=20),
        "reduced_motion": _text(profile.get("reduced_motion"), limit=20),
    }
    return {
        **copy.deepcopy(plan),
        "steps": [viewport, media, *steps],
        "matrix_profile_id": profile["profile_id"],
        "matrix_browser_engine": profile["browser_engine"],
        "matrix_device_class": profile["device_class"],
    }


def _request_for_profile(
    request: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    child = copy.deepcopy(request)
    base_request_id = _text(request.get("request_id"), limit=160) or "ui_request"
    child["request_id"] = f"{base_request_id}__{profile['profile_id']}"
    child.pop("browser_matrix", None)
    child["browser_plan"] = _profile_plan(
        _dict(request.get("browser_plan")),
        profile,
    )
    child["metadata"] = {
        **_dict(request.get("metadata")),
        "browser_matrix_child": True,
        "browser_matrix_profile_id": profile["profile_id"],
        "browser_engine": profile["browser_engine"],
        "device_class": profile["device_class"],
    }
    return child


def _matrix_runtime_error(request: dict[str, Any], matrix: dict[str, Any]) -> str:
    mode = _text(request.get("execution_mode") or "safe_read_only")
    plan = _dict(request.get("browser_plan"))
    actions = [
        _text(row.get("action"), limit=80).lower()
        for row in _list(plan.get("steps"))
        if isinstance(row, dict)
    ]
    if mode != "safe_read_only":
        return "UI_BROWSER_MATRIX_SAFE_READ_ONLY_REQUIRED"
    if any(action in {"click", "fill", "check", "uncheck", "select_option", "press"} for action in actions):
        return "UI_BROWSER_MATRIX_INTERACTION_UNSUPPORTED_V1"
    if "expect_visual_baseline" in actions:
        return "UI_BROWSER_MATRIX_PROFILE_VISUAL_BASELINES_REQUIRED"
    if len(_list(matrix.get("profiles"))) < 2:
        return "UI_BROWSER_MATRIX_PROFILE_COUNT_INVALID"
    return ""


def _safe_reason_code(reason: Any) -> str:
    text = _text(reason, limit=1000)
    if not text:
        return ""
    prefix = text.split(":", 1)[0]
    if prefix.startswith(("UI_", "BROWSER_", "browser_")):
        return prefix[:160]
    return "UI_BROWSER_MATRIX_RUNTIME_ERROR_" + _fingerprint(text)


def _profile_receipt(
    profile: dict[str, Any],
    result: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    artifacts = [
        _dict(row)
        for row in _list(result.get("artifacts"))
        if isinstance(row, dict) and _text(_dict(row).get("ref"))
    ]
    return {
        "profile_id": profile["profile_id"],
        "browser_engine": profile["browser_engine"],
        "browser_version": _text(runtime.get("browser_version"), limit=120),
        "playwright_version": _text(runtime.get("playwright_version"), limit=120),
        "bundled_engine_required": True,
        "system_browser_fallback_used": False,
        "device_class": profile["device_class"],
        "viewport_width": int(profile["viewport_width"]),
        "viewport_height": int(profile["viewport_height"]),
        "device_scale_factor": float(profile["device_scale_factor"]),
        "is_mobile": profile["is_mobile"] is True,
        "has_touch": profile["has_touch"] is True,
        "locale": profile["locale"],
        "timezone_id": profile["timezone_id"],
        "color_scheme": profile["color_scheme"],
        "reduced_motion": profile["reduced_motion"],
        "user_agent_fingerprint": (
            _fingerprint(profile.get("user_agent"))
            if _text(profile.get("user_agent"))
            else ""
        ),
        "status": _text(result.get("status"), limit=40).lower() or "blocked",
        "execution_status": _text(result.get("execution_status"), limit=40),
        "reason_code": _safe_reason_code(result.get("reason")),
        "completed_step_count": len(_list(result.get("steps"))),
        "artifact_fingerprints": [
            _fingerprint(row.get("ref")) for row in artifacts
        ],
        "duration_ms": int(result.get("duration_ms") or 0),
        "raw_console_in_receipt": False,
        "raw_network_urls_in_receipt": False,
    }


def _aggregate_result(
    request: dict[str, Any],
    matrix: dict[str, Any],
    child_results: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    profile_receipts = [
        _profile_receipt(profile, result, runtime)
        for profile, result, runtime in child_results
    ]
    status_counts = Counter(row["status"] for row in profile_receipts)
    failed = next(
        (
            (profile, result)
            for profile, result, _runtime in child_results
            if _text(result.get("status")).lower() == "failed"
        ),
        None,
    )
    incomplete = any(row["status"] != "executed" for row in profile_receipts)
    selected_result = (
        failed[1]
        if failed
        else child_results[0][1]
        if child_results
        else {}
    )
    aggregate_status = "failed" if failed else "blocked" if incomplete else "executed"
    aggregate_reason = (
        _text(selected_result.get("reason"))
        if failed
        else "UI_BROWSER_MATRIX_INCOMPLETE"
        if incomplete
        else ""
    )
    artifacts: list[dict[str, Any]] = []
    for profile, result, _runtime in child_results:
        for artifact in _list(result.get("artifacts")):
            row = _dict(artifact)
            ref = _text(row.get("ref"))
            if not ref:
                continue
            artifacts.append({
                **copy.deepcopy(row),
                "matrix_profile_id": profile["profile_id"],
                "browser_engine": profile["browser_engine"],
            })
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "status": (
            "VIOLATION_OBSERVED"
            if failed
            else "INCOMPLETE"
            if incomplete
            else "ALL_PROFILES_EXECUTED"
        ),
        "profile_count": len(profile_receipts),
        "executed_profile_count": status_counts.get("executed", 0),
        "failed_profile_count": status_counts.get("failed", 0),
        "blocked_profile_count": sum(
            count
            for status, count in status_counts.items()
            if status not in {"executed", "failed"}
        ),
        "all_profiles_executed": not incomplete,
        "property_held_requires_all_profiles": True,
        "violation_requires_one_typed_profile_failure": True,
        "profiles": profile_receipts,
        "provider_findings_consumed": False,
        "interactive_matrix_supported": False,
        "cross_engine_visual_baseline_supported": False,
    }
    top = {
        **copy.deepcopy(selected_result),
        "request_id": _text(request.get("request_id")),
        "title": _text(request.get("title"), limit=200),
        "provider": "playwright_browser_plan",
        "task": _text(request.get("task")),
        "start_url": _text(request.get("start_url")),
        "execution_mode": "safe_read_only",
        "browser_matrix": copy.deepcopy(matrix),
        "browser_matrix_receipt": receipt,
        "matrix_results": profile_receipts,
        "status": aggregate_status,
        "reason": aggregate_reason,
        "execution_status": (
            "executed" if aggregate_status == "executed" else "failed"
            if aggregate_status == "failed" else "not_executed"
        ),
        "confirmation_status": (
            "candidate" if aggregate_status in {"executed", "failed"} else "blocked"
        ),
        "artifacts": artifacts,
        "provider_clues": [],
        "findings": [],
        "duration_ms": sum(row["duration_ms"] for row in profile_receipts),
        "metadata": {
            **_dict(request.get("metadata")),
            "browser_matrix_executed": True,
            "browser_matrix_profile_count": len(profile_receipts),
        },
    }
    _LAST_RECEIPT.set(copy.deepcopy(receipt))
    return top


def install_professional_ui_browser_matrix_runtime() -> None:
    """Install engine selection before evidence-privacy browser wrappers."""
    if getattr(_auto_browser, _INSTALL_RUNTIME_MARKER, False):
        return
    original = getattr(
        _auto_browser,
        _ORIGINAL_ENSURE,
        _auto_browser.ensure_browser,
    )
    setattr(_auto_browser, _ORIGINAL_ENSURE, original)

    def ensure_browser_with_matrix(
        headless: bool = True,
        timeout: int = 30_000,
    ) -> tuple[Any, Any]:
        profile = _dict(_ACTIVE_PROFILE.get())
        if not profile:
            return original(headless=headless, timeout=timeout)
        return _launch_engine(profile, headless=headless, timeout=timeout)

    _auto_browser.ensure_browser = ensure_browser_with_matrix
    setattr(_auto_browser, _INSTALL_RUNTIME_MARKER, True)


def install_professional_ui_browser_matrix() -> None:
    """Install request expansion after all existing UI adapter safety wrappers."""
    if getattr(_adapter, _INSTALL_ADAPTER_MARKER, False):
        return
    original_normalize = getattr(
        _adapter,
        _ORIGINAL_NORMALIZE,
        _adapter.normalize_ui_execution_requests,
    )
    original_adapter = getattr(
        _adapter,
        _ORIGINAL_ADAPTER,
        _adapter._playwright_request_result,
    )
    original_observer = getattr(
        _formal,
        _ORIGINAL_OBSERVER,
        _formal._ui_observer_handler,
    )
    setattr(_adapter, _ORIGINAL_NORMALIZE, original_normalize)
    setattr(_adapter, _ORIGINAL_ADAPTER, original_adapter)
    setattr(_formal, _ORIGINAL_OBSERVER, original_observer)

    def normalize_with_browser_matrix(value: Any) -> list[dict[str, Any]]:
        normalized = original_normalize(value)
        raw_rows = _list(value)
        for index, row in enumerate(normalized):
            raw = _dict(raw_rows[index]) if index < len(raw_rows) else {}
            if isinstance(raw.get("browser_matrix"), dict):
                row["browser_matrix"] = copy.deepcopy(raw["browser_matrix"])
        return normalized

    def playwright_request_with_matrix(
        project_id: str,
        request: dict[str, Any],
        runtime_contract: dict[str, Any],
        *,
        root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        _LAST_RECEIPT.set({})
        matrix_value = request.get("browser_matrix")
        if not matrix_value:
            return original_adapter(
                project_id,
                request,
                runtime_contract,
                root=root,
                run_id=run_id,
            )
        try:
            matrix = normalize_browser_matrix(matrix_value)
        except ValueError as exc:
            return _adapter._blocked_request_result(
                request,
                "UI_BROWSER_MATRIX_INVALID:" + _text(exc, limit=160),
                root=root,
            )
        runtime_error = _matrix_runtime_error(request, matrix)
        if runtime_error:
            return _adapter._blocked_request_result(request, runtime_error, root=root)

        child_results: list[
            tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
        ] = []
        for profile in _list(matrix.get("profiles")):
            profile_token = _ACTIVE_PROFILE.set(copy.deepcopy(_dict(profile)))
            runtime_token = _ACTIVE_RUNTIME.set({})
            try:
                child = _request_for_profile(request, _dict(profile))
                result = original_adapter(
                    project_id,
                    child,
                    runtime_contract,
                    root=root,
                    run_id=run_id,
                )
                runtime = copy.deepcopy(_dict(_ACTIVE_RUNTIME.get()))
            finally:
                _ACTIVE_RUNTIME.reset(runtime_token)
                _ACTIVE_PROFILE.reset(profile_token)
            child_results.append((copy.deepcopy(_dict(profile)), result, runtime))
        return _aggregate_result(request, matrix, child_results)

    def observer_with_browser_matrix(envelope: dict[str, Any]) -> dict[str, Any]:
        token = _LAST_RECEIPT.set({})
        try:
            receipt = original_observer(envelope)
            matrix_receipt = copy.deepcopy(_dict(_LAST_RECEIPT.get()))
        finally:
            _LAST_RECEIPT.reset(token)
        if not matrix_receipt:
            return receipt
        output = copy.deepcopy(receipt)
        evidence = _dict(output.get("evidence"))
        ui_evidence = _dict(evidence.get(_formal.EVIDENCE_KEY))
        ui_evidence["browser_matrix"] = matrix_receipt
        evidence[_formal.EVIDENCE_KEY] = ui_evidence
        output["evidence"] = evidence
        return output

    _adapter.normalize_ui_execution_requests = normalize_with_browser_matrix
    _adapter._playwright_request_result = playwright_request_with_matrix
    _formal._ui_observer_handler = observer_with_browser_matrix
    setattr(_adapter, _INSTALL_ADAPTER_MARKER, True)


__all__ = [
    "install_professional_ui_browser_matrix",
    "install_professional_ui_browser_matrix_runtime",
]
