from __future__ import annotations

"""Customer boundary patch: never expose fix advice in customer payloads.

QualiBug-AI's responsibility is defect discovery, evidence, post-fix regression,
and release status.  It must not provide remediation advice, repair steps,
patches, or root-cause claims to customers.  This runtime normalizer strips such
fields from command-center/customer API envelopes while keeping evidence and
regression verification intact.
"""

from typing import Any

PATCH_SOURCE = "ai_test_asset_center.private_pilot_no_fix_advice_patch"

_FIX_ADVICE_KEYS = {
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


def _strip_fix_advice(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_fix_advice(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if str(key) in _FIX_ADVICE_KEYS:
            continue
        cleaned[key] = _strip_fix_advice(item)
    return cleaned


def _attach_boundary(value: dict[str, Any]) -> dict[str, Any]:
    boundary = value.get("product_responsibility_boundary") if isinstance(value.get("product_responsibility_boundary"), dict) else {}
    value["product_responsibility_boundary"] = {
        **boundary,
        "scope": "defect_discovery_evidence_post_fix_regression_release_status",
        "no_fix_advice": True,
        "customer_meaning": "QualiBug-AI only reports defect facts, evidence chains, post-fix regression verification, and release status. It does not provide fix advice, repair plans, or code changes.",
    }
    return value


def sanitize_customer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    sanitized = _strip_fix_advice(payload)
    data = sanitized.get("data") if isinstance(sanitized.get("data"), dict) else sanitized
    if isinstance(data, dict):
        data = _attach_boundary(data)
        for key in ("defects", "findings", "real_findings", "clues", "bug_scores"):
            if isinstance(data.get(key), list):
                data[key] = [_attach_boundary(item) if isinstance(item, dict) else item for item in data[key]]
        contract = data.get("data_contract") if isinstance(data.get("data_contract"), dict) else {}
        contract["product_responsibility_boundary"] = {
            "display_key": "product_responsibility_boundary",
            "source": PATCH_SOURCE,
            "honesty_rule": "Customer payloads must not contain fix advice, remediation plans, root-cause claims, or code changes. The platform only verifies defects and post-fix closure status.",
            "customer_meaning": "The customer owns remediation. QualiBug-AI owns defect evidence and regression verification after remediation.",
        }
        data["data_contract"] = contract
        if isinstance(sanitized.get("data"), dict):
            sanitized["data"] = data
    return sanitized


def install_no_fix_advice_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    from ai_test_asset_center import private_pilot_service as service

    if getattr(service, "_NO_FIX_ADVICE_PATCHED", False):
        return

    original_normalizer = getattr(service, "_normalize_command_center_envelope", None)

    def _normalize_without_fix_advice(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = original_normalizer(payload) if callable(original_normalizer) else payload
        try:
            return sanitize_customer_payload(normalized)
        except Exception:
            return normalized

    service._ORIGINAL_NO_FIX_ADVICE_NORMALIZER = original_normalizer  # type: ignore[attr-defined]
    service._normalize_command_center_envelope = _normalize_without_fix_advice  # type: ignore[attr-defined]
    service._NO_FIX_ADVICE_PATCHED = True  # type: ignore[attr-defined]
    service._NO_FIX_ADVICE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_no_fix_advice_patch() -> None:
    from ai_test_asset_center import private_pilot_service as service

    original = getattr(service, "_ORIGINAL_NO_FIX_ADVICE_NORMALIZER", None)
    if callable(original):
        service._normalize_command_center_envelope = original  # type: ignore[attr-defined]
    service._NO_FIX_ADVICE_PATCHED = False  # type: ignore[attr-defined]
    service._NO_FIX_ADVICE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
