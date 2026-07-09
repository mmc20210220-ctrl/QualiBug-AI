from __future__ import annotations

"""Display-ready boundary patch: no customer-facing fix advice.

The legacy formatter still knows how to build diagnostic/root-cause/fix fields.
The product boundary is now stricter: QualiBug-AI reports defect facts,
evidence, regression verification, and release status only.  This patch strips
repair advice at the display-ready generation source before frontend/API guards
need to catch it.
"""

from typing import Any, Callable

from ai_test_asset_center import display_ready_formatter as _formatter

PATCH_SOURCE = "ai_test_asset_center.display_ready_no_fix_advice_patch"

_STRIPPED_KEYS = {
    "recommended_fix",
    "fix_advice",
    "fix_suggestion",
    "repair_suggestion",
    "repair_plan",
    "remediation",
    "remediation_advice",
    "remediation_plan",
    "patch_suggestion",
    "code_fix",
    "possible_root_cause",
    "root_cause_hypothesis",
}

_BOUNDARY = {
    "scope": "defect_discovery_evidence_post_fix_regression_release_status",
    "no_fix_advice": True,
    "source": PATCH_SOURCE,
    "customer_meaning": "QualiBug-AI only reports defect facts, evidence chains, post-fix regression verification, and release status. It does not provide fix advice, repair plans, root-cause commitments, or code changes.",
}


def _strip_advice(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_advice(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if str(key) in _STRIPPED_KEYS:
            continue
        cleaned[key] = _strip_advice(item)
    return cleaned


def _normalize_regression_obligations(details: dict[str, Any], finding: dict[str, Any]) -> dict[str, Any]:
    obligations = details.get("regression_verification_obligations")
    if not isinstance(obligations, list):
        suggestions = details.get("regression_suggestions")
        obligations = suggestions if isinstance(suggestions, list) else []
    method = str((details.get("api_endpoint") or {}).get("method") or finding.get("_api_method") or finding.get("method") or "").strip().upper()
    path = str((details.get("api_endpoint") or {}).get("path") or finding.get("_api_path") or finding.get("path") or "").strip()
    if not obligations and (method or path):
        obligations = [f"系统应在客户处理后回归验证 {method or 'HTTP'} {path or '<unknown>'} 是否仍可复现"]
    details["regression_verification_obligations"] = [str(item) for item in obligations if str(item).strip()]
    details.pop("regression_suggestions", None)
    return details


def _safe_details_from_original(original: Callable[..., dict[str, Any]], finding: dict[str, Any], investigation: dict[str, Any], reproduction: dict[str, Any]) -> dict[str, Any]:
    details = original(finding, investigation, reproduction)
    details = _strip_advice(details) if isinstance(details, dict) else {}
    details = _normalize_regression_obligations(details, finding)
    details["product_responsibility_boundary"] = dict(_BOUNDARY)
    return details


def _safe_finding_from_original(original: Callable[..., dict[str, Any]], finding: dict[str, Any], enterprise_ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    formatted = original(finding, enterprise_ctx)
    formatted = _strip_advice(formatted) if isinstance(formatted, dict) else {}
    technical = formatted.get("technical_details") if isinstance(formatted.get("technical_details"), dict) else {}
    formatted["technical_details"] = _normalize_regression_obligations(_strip_advice(technical), finding if isinstance(finding, dict) else {})
    formatted["technical_details"]["product_responsibility_boundary"] = dict(_BOUNDARY)
    regression = formatted.get("regression_suggestions")
    if isinstance(regression, list):
        formatted["regression_verification_obligations"] = [str(item) for item in regression if str(item).strip()]
    formatted.pop("regression_suggestions", None)
    formatted["product_responsibility_boundary"] = dict(_BOUNDARY)
    return formatted


def install_display_ready_no_fix_advice_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    if getattr(_formatter, "_NO_FIX_ADVICE_DISPLAY_READY_PATCHED", False):
        return
    original_details = getattr(_formatter, "_build_technical_details")
    original_finding = getattr(_formatter, "_format_single_finding")

    def _build_technical_details_no_fix(finding: dict, investigation: dict, reproduction: dict) -> dict[str, Any]:
        return _safe_details_from_original(original_details, finding, investigation, reproduction)

    def _format_single_finding_no_fix(finding: dict, enterprise_ctx: dict | None = None) -> dict[str, Any]:
        return _safe_finding_from_original(original_finding, finding, enterprise_ctx)

    _formatter._ORIGINAL_BUILD_TECHNICAL_DETAILS_NO_FIX = original_details  # type: ignore[attr-defined]
    _formatter._ORIGINAL_FORMAT_SINGLE_FINDING_NO_FIX = original_finding  # type: ignore[attr-defined]
    _formatter._build_technical_details = _build_technical_details_no_fix  # type: ignore[attr-defined]
    _formatter._format_single_finding = _format_single_finding_no_fix  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_PATCHED = True  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_display_ready_no_fix_advice_patch() -> None:
    original_details = getattr(_formatter, "_ORIGINAL_BUILD_TECHNICAL_DETAILS_NO_FIX", None)
    original_finding = getattr(_formatter, "_ORIGINAL_FORMAT_SINGLE_FINDING_NO_FIX", None)
    if callable(original_details):
        _formatter._build_technical_details = original_details  # type: ignore[attr-defined]
    if callable(original_finding):
        _formatter._format_single_finding = original_finding  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_PATCHED = False  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_PATCH_SOURCE = ""  # type: ignore[attr-defined]
