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
    from ai_test_asset_center.private_pilot_command_center_envelope import register_envelope_post_hook

    if getattr(service, "_NO_FIX_ADVICE_PATCHED", False):
        return

    def _no_fix_advice_hook(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return sanitize_customer_payload(payload)
        except Exception:
            return payload

    register_envelope_post_hook("no_fix_advice", _no_fix_advice_hook)
    service._NO_FIX_ADVICE_PATCHED = True  # type: ignore[attr-defined]
    service._NO_FIX_ADVICE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    service._NO_FIX_ADVICE_MODE = "first_class_hook"  # type: ignore[attr-defined]


def restore_no_fix_advice_patch() -> None:
    from ai_test_asset_center import private_pilot_service as service
    from ai_test_asset_center.private_pilot_command_center_envelope import register_envelope_post_hook

    register_envelope_post_hook("no_fix_advice", None)
    service._NO_FIX_ADVICE_PATCHED = False  # type: ignore[attr-defined]
    service._NO_FIX_ADVICE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    if hasattr(service, "_NO_FIX_ADVICE_MODE"):
        delattr(service, "_NO_FIX_ADVICE_MODE")
