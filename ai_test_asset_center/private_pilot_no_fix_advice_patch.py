from __future__ import annotations

"""Customer boundary patch: never expose fix advice in customer payloads.

QualiBug-AI's responsibility is defect discovery, evidence, post-customer-change
regression, and release status. It must not provide remediation advice, repair
steps, patches, or root-cause claims to customers. This runtime normalizer strips
such fields from command-center/customer API envelopes while keeping evidence and
regression verification intact.
"""

from typing import Any

from ai_test_asset_center.customer_report_boundary import (
    attach_product_responsibility_boundary,
    data_contract_product_responsibility_boundary,
    strip_fix_advice_fields,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_no_fix_advice_patch"


def sanitize_customer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    sanitized = strip_fix_advice_fields(payload)
    data = sanitized.get("data") if isinstance(sanitized.get("data"), dict) else sanitized
    if isinstance(data, dict):
        data = attach_product_responsibility_boundary(data, PATCH_SOURCE)
        for key in ("defects", "findings", "real_findings", "clues", "bug_scores"):
            if isinstance(data.get(key), list):
                data[key] = [attach_product_responsibility_boundary(item, PATCH_SOURCE) if isinstance(item, dict) else item for item in data[key]]
        contract = data.get("data_contract") if isinstance(data.get("data_contract"), dict) else {}
        contract["product_responsibility_boundary"] = data_contract_product_responsibility_boundary(PATCH_SOURCE)
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
