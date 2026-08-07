"""Reason-code catalog completeness (链路定位 ①).

Every registered reason code must carry attribution / recoverability /
blocking semantics plus diagnostic guidance (explicitly marked synthetic).
The catalog is the single directory consumed by the positioning report, CLI
and frontend; unregistered codes must stay visibly UNREGISTERED.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.blocker_attribution import (
    GUIDANCE_KIND,
    REASON_CODE_REGISTRY,
    REASON_CODE_REGISTRY_SCHEMA,
    build_reason_code_catalog,
    profile_reason_code,
    register_reason_code,
)


def test_catalog_schema_and_guidance_kind() -> None:
    catalog = build_reason_code_catalog()
    assert catalog["schema_version"] == REASON_CODE_REGISTRY_SCHEMA
    assert catalog["guidance_kind"] == GUIDANCE_KIND
    assert catalog["code_count"] == len(catalog["codes"])


def test_every_registered_code_has_complete_definition() -> None:
    catalog = build_reason_code_catalog()
    for code, row in catalog["codes"].items():
        assert row["reason_family"], code
        assert row["recoverability"], code
        for field in ("meaning", "likely_root_cause", "suggested_action", "guidance_kind"):
            assert field in row, f"{code}: missing {field}"
        assert row["guidance_kind"] == GUIDANCE_KIND


def test_key_codes_are_registered_with_guidance() -> None:
    catalog = build_reason_code_catalog()["codes"]
    for code in (
        # compile/ledger family
        "BLOCKED_MISSING_BINDING",
        "BLOCKED_UNSUPPORTED_ADAPTER",
        "BLOCKED_MISSING_OBSERVER",
        "non_production_environment_required",
        # reasoner failure family
        "http_401",
        "http_429",
        "timeout",
        "parse_error",
        "empty_hypotheses",
        # LLM transport
        "QB-L001",
        "QB-L006",
        # contract derivation family
        "conflicting_derived_claims",
        "event_fields_incomplete",
        "embeddings_unavailable",
    ):
        assert code in catalog, code
        assert catalog[code]["meaning"], code
        assert catalog[code]["suggested_action"], code


def test_family_guidance_is_inherited_by_codes_without_override() -> None:
    # BLOCKED_MISSING_FIXTURE has no per-code override -> family guidance
    profile = profile_reason_code("BLOCKED_MISSING_FIXTURE")
    assert profile["registry_status"] == "REGISTERED"
    assert "夹具" in profile["meaning"]
    # per-code override wins over family text
    override = profile_reason_code("BLOCKED_CONTROL_ARM_NOT_PROVEN")
    assert "控制臂" in override["meaning"]


def test_open_registration_and_empty_code_rejection() -> None:
    register_reason_code(
        "TEST_POSITIONING_CODE",
        attribution="SOURCE_GAP",
        recoverability="RECOVERABLE",
        meaning="测试码含义",
        likely_root_cause="测试根因",
        suggested_action="测试动作",
        source_module="tests",
    )
    try:
        catalog = build_reason_code_catalog()["codes"]
        assert catalog["TEST_POSITIONING_CODE"]["meaning"] == "测试码含义"
        assert catalog["TEST_POSITIONING_CODE"]["source_module"] == "tests"
        with pytest.raises(ValueError):
            register_reason_code("", attribution="SOURCE_GAP")
    finally:
        # keep the global registry clean for other tests
        REASON_CODE_REGISTRY.pop("TEST_POSITIONING_CODE", None)
        from ai_test_asset_center import blocker_attribution as _ba

        _ba._CODE_GUIDANCE.pop("TEST_POSITIONING_CODE", None)


def test_unknown_code_stays_visibly_unregistered() -> None:
    profile = profile_reason_code("SOME_UNKNOWN_REASON")
    assert profile["registry_status"] == "UNREGISTERED"
    assert profile["reason_family"] == "UNREGISTERED"
    # unregistered codes still carry the family guidance so reports can
    # explain the registration gap
    assert profile["meaning"]


def test_registry_has_no_empty_guidance_gaps() -> None:
    """Every code in the built-in registry carries guidance (no pending)."""
    catalog = build_reason_code_catalog()
    pending = [code for code, row in catalog["codes"].items() if row["guidance_pending"]]
    assert pending == [], f"codes missing guidance: {pending}"


def test_registered_codes_are_nonempty_and_unique() -> None:
    assert REASON_CODE_REGISTRY
    assert len(REASON_CODE_REGISTRY) == len(set(REASON_CODE_REGISTRY))
