"""Strict source admission for professional browser/device execution matrices.

A matrix is execution authority, not an adapter hint.  The source must declare every
browser/device profile explicitly and the matrix may only expand a safe read-only UI
contract.  Governed writes remain single-profile because each profile would repeat the
business mutation and cleanup transaction.

Visual pixel comparison is also deliberately excluded from matrix v1.  One PNG captured
under Chromium is not valid authority for Firefox or WebKit.  Cross-engine visual
regression will require profile-specific governed baseline records rather than silently
reusing the canonical Chromium baseline.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from . import _formal_ui_contracts as _contracts

SCHEMA_VERSION = "qualibug.ui-browser-matrix.v1"
AGGREGATION_POLICY = "all_profiles_must_pass"
SUPPORTED_ENGINES = frozenset({"chromium", "firefox", "webkit"})
SUPPORTED_DEVICE_CLASSES = frozenset({"desktop", "tablet", "mobile"})
SUPPORTED_COLOR_SCHEMES = frozenset({"light", "dark", "no-preference"})
SUPPORTED_REDUCED_MOTION = frozenset({"reduce", "no-preference"})
MIN_PROFILES = 2
MAX_PROFILES = 12
_INSTALL_MARKER = "_qualibug_formal_ui_browser_matrix_guard_installed"
_ORIGINAL_VALIDATE = "_qualibug_ui_contract_validator_before_browser_matrix"
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _integer(value: Any, *, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"browser_matrix.{field}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"browser_matrix.{field}") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"browser_matrix.{field}")
    return number


def _number(value: Any, *, minimum: float, maximum: float, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"browser_matrix.{field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"browser_matrix.{field}") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"browser_matrix.{field}")
    return number


def _boolean(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"browser_matrix.{field}")
    return value


def _locale(value: Any) -> str:
    locale = _text(value, limit=40)
    if not locale or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*", locale):
        raise ValueError("browser_matrix.locale")
    return locale


def _timezone(value: Any) -> str:
    timezone = _text(value, limit=100)
    if not timezone or timezone.startswith(('/', '\\')) or ".." in timezone:
        raise ValueError("browser_matrix.timezone_id")
    if not re.fullmatch(r"[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*", timezone):
        raise ValueError("browser_matrix.timezone_id")
    return timezone


def _normalize_profile(raw: dict[str, Any]) -> dict[str, Any]:
    profile_id = _text(raw.get("profile_id"), limit=80)
    if not _PROFILE_ID_RE.fullmatch(profile_id):
        raise ValueError("browser_matrix.profile_id")
    engine = _text(raw.get("browser_engine"), limit=20).lower()
    if engine not in SUPPORTED_ENGINES:
        raise ValueError("browser_matrix.browser_engine")
    device_class = _text(raw.get("device_class"), limit=20).lower()
    if device_class not in SUPPORTED_DEVICE_CLASSES:
        raise ValueError("browser_matrix.device_class")
    width = _integer(
        raw.get("viewport_width"),
        minimum=240,
        maximum=7680,
        field="viewport_width",
    )
    height = _integer(
        raw.get("viewport_height"),
        minimum=240,
        maximum=4320,
        field="viewport_height",
    )
    scale = _number(
        raw.get("device_scale_factor", 1),
        minimum=1.0,
        maximum=4.0,
        field="device_scale_factor",
    )
    is_mobile = _boolean(raw.get("is_mobile", False), field="is_mobile")
    has_touch = _boolean(raw.get("has_touch", False), field="has_touch")
    if device_class == "desktop" and (is_mobile or has_touch):
        raise ValueError("browser_matrix.desktop_touch_identity")
    if device_class in {"tablet", "mobile"} and not has_touch:
        raise ValueError("browser_matrix.touch_device_requires_has_touch")
    if device_class == "mobile" and not is_mobile:
        raise ValueError("browser_matrix.mobile_requires_is_mobile")
    if engine == "firefox" and is_mobile:
        # Playwright does not provide deterministic Firefox mobile emulation.
        raise ValueError("browser_matrix.firefox_mobile_unsupported")
    color_scheme = _text(raw.get("color_scheme") or "no-preference", limit=20).lower()
    if color_scheme not in SUPPORTED_COLOR_SCHEMES:
        raise ValueError("browser_matrix.color_scheme")
    reduced_motion = _text(
        raw.get("reduced_motion") or "no-preference",
        limit=20,
    ).lower()
    if reduced_motion not in SUPPORTED_REDUCED_MOTION:
        raise ValueError("browser_matrix.reduced_motion")
    user_agent = _text(raw.get("user_agent"), limit=500)
    return {
        "profile_id": profile_id,
        "browser_engine": engine,
        "device_class": device_class,
        "viewport_width": width,
        "viewport_height": height,
        "device_scale_factor": scale,
        "is_mobile": is_mobile,
        "has_touch": has_touch,
        "locale": _locale(raw.get("locale") or "zh-CN"),
        "timezone_id": _timezone(raw.get("timezone_id") or "Asia/Shanghai"),
        "color_scheme": color_scheme,
        "reduced_motion": reduced_motion,
        "user_agent": user_agent,
    }


def normalize_browser_matrix(value: Any) -> dict[str, Any]:
    matrix = _dict(value)
    if not matrix:
        return {}
    if _text(matrix.get("schema_version")) != SCHEMA_VERSION:
        raise ValueError("browser_matrix.schema_version")
    if _text(matrix.get("aggregation_policy")) != AGGREGATION_POLICY:
        raise ValueError("browser_matrix.aggregation_policy")
    raw_profiles = _list(matrix.get("profiles"))
    if not MIN_PROFILES <= len(raw_profiles) <= MAX_PROFILES:
        raise ValueError("browser_matrix.profile_count")
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            raise ValueError("browser_matrix.profile_object")
        profile = _normalize_profile(raw)
        profile_id = profile["profile_id"]
        if profile_id in seen:
            raise ValueError("browser_matrix.profile_id_duplicate")
        seen.add(profile_id)
        profiles.append(profile)
    return {
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "profiles": profiles,
    }


def _matrix_contract_gap(
    *,
    contract: dict[str, Any],
    source_id: str,
    locator: str,
    requirement: str,
) -> dict[str, Any]:
    return {
        "gap_type": "formal_ui_browser_matrix_incomplete",
        "reason_code": "FORMAL_UI_BROWSER_MATRIX_INCOMPLETE",
        "contract_id": _text(contract.get("contract_id")),
        "source_id": source_id,
        "source_locator": locator,
        "missing_requirements": [requirement],
        "status": "unsupported",
    }


def install_formal_ui_browser_matrix_guard() -> None:
    if getattr(_contracts, _INSTALL_MARKER, False):
        return
    original = getattr(
        _contracts,
        _ORIGINAL_VALIDATE,
        _contracts._validate_contract,
    )
    setattr(_contracts, _ORIGINAL_VALIDATE, original)

    def validate_with_browser_matrix(
        raw: dict[str, Any],
        *,
        source_id: str,
        locator: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        contract, gap = original(raw, source_id=source_id, locator=locator)
        if gap or not contract:
            return contract, gap
        request = _dict(contract.get("ui_request"))
        matrix_value = request.get("browser_matrix")
        if not matrix_value:
            return contract, None
        try:
            matrix = normalize_browser_matrix(matrix_value)
        except ValueError as exc:
            return None, _matrix_contract_gap(
                contract=contract,
                source_id=source_id,
                locator=locator,
                requirement=str(exc),
            )
        plan = _dict(request.get("browser_plan"))
        steps = [row for row in _list(plan.get("steps")) if isinstance(row, dict)]
        actions = [_text(row.get("action"), limit=80).lower() for row in steps]
        mode = _text(request.get("execution_mode") or plan.get("execution_mode"))
        if mode != "safe_read_only":
            return None, _matrix_contract_gap(
                contract=contract,
                source_id=source_id,
                locator=locator,
                requirement="browser_matrix.safe_read_only_only",
            )
        if any(action in _contracts.INTERACTIVE_ACTIONS for action in actions):
            return None, _matrix_contract_gap(
                contract=contract,
                source_id=source_id,
                locator=locator,
                requirement="browser_matrix.interactive_execution_unsupported_v1",
            )
        if "expect_visual_baseline" in actions:
            return None, _matrix_contract_gap(
                contract=contract,
                source_id=source_id,
                locator=locator,
                requirement="browser_matrix.profile_specific_visual_baselines_required",
            )
        if actions.count("set_viewport") > 1:
            return None, _matrix_contract_gap(
                contract=contract,
                source_id=source_id,
                locator=locator,
                requirement="browser_matrix.single_viewport_configuration",
            )
        if actions.count("set_media") > 1:
            return None, _matrix_contract_gap(
                contract=contract,
                source_id=source_id,
                locator=locator,
                requirement="browser_matrix.single_media_configuration",
            )
        accepted = copy.deepcopy(contract)
        accepted_request = _dict(accepted.get("ui_request"))
        accepted_request["browser_matrix"] = matrix
        accepted_request["metadata"] = {
            **_dict(accepted_request.get("metadata")),
            "browser_matrix_source_declared": True,
            "browser_matrix_profile_count": len(matrix["profiles"]),
        }
        accepted["ui_request"] = accepted_request
        return accepted, None

    _contracts._validate_contract = validate_with_browser_matrix
    setattr(_contracts, _INSTALL_MARKER, True)


__all__ = [
    "AGGREGATION_POLICY",
    "MAX_PROFILES",
    "SCHEMA_VERSION",
    "SUPPORTED_DEVICE_CLASSES",
    "SUPPORTED_ENGINES",
    "install_formal_ui_browser_matrix_guard",
    "normalize_browser_matrix",
]
