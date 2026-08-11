from __future__ import annotations

from ai_test_asset_center.blocker_attribution import profile_reason_code


def test_cleanup_failure_reason_codes_are_registered() -> None:
    expected = {
        "HARNESS_CLEANUP_TRANSPORT_FAILED": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE"),
        "HARNESS_CLEANUP_RESPONSE_REJECTED": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE"),
        "HARNESS_CLEANUP_EQUIVALENCE_FAILED": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE"),
        "HARNESS_CLEANUP_FAILURE_UNATTRIBUTED": ("CLEANUP_CAPABILITY_GAP", "UNKNOWN"),
    }
    for reason_code, (family, recoverability) in expected.items():
        profile = profile_reason_code(reason_code)
        assert profile["registry_status"] == "REGISTERED"
        assert profile["reason_family"] == family
        assert profile["recoverability"] == recoverability
