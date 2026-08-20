"""Recall regressions for source-rule dimensions in canonical defect identity."""
from __future__ import annotations

from copy import deepcopy

import ai_test_asset_center.canonical_defect_registry as registry


_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _evidence(expected_signature: object) -> dict:
    return {
        "schema_version": registry.CANONICAL_IDENTITY_EVIDENCE_SCHEMA,
        "operation": {
            "adapter": "http",
            "verb": "POST",
            "operation_ref": "inventory.adjust",
            "source_locator": "/inventory/adjust",
        },
        "property": {
            "assertion_kind": "field_compare",
            "expected_signature": expected_signature,
        },
        "actor_relation": {
            "control_actor_class": "not_identity_defining",
            "treatment_actor_class": "not_identity_defining",
            "relation": "actor_insensitive_property",
        },
        "resource_identity_class": {
            "source_locators": ["/inventory/adjust"],
        },
        "mutation": {
            "class": "boundary",
            "selector": "quantity",
            "operator": "above_max",
        },
        "observed_outcome": {
            "assertion_kind": "field_compare",
            "expected_signature": expected_signature,
            "actual_signature": {"type": "number", "class": "positive"},
            "control_observation_class": "not_observed",
            "treatment_observation_class": "http:2xx",
        },
        "proof": {
            "assertion_receipt_id": "assert_receipt",
            "oracle_receipt_id": "oracle_receipt",
            "reproduction_receipt_id": "repro_receipt",
            "request_body_fingerprint": _SHA_A,
            "request_semantics_fingerprint": _SHA_B,
            "evidence_actor_classes": [],
        },
    }


def test_distinct_positive_source_thresholds_do_not_collapse() -> None:
    expected_10 = registry._source_expected_semantic_value(
        10,
        assertion_kind="field_compare",
    )
    expected_100 = registry._source_expected_semantic_value(
        100,
        assertion_kind="field_compare",
    )

    # Runtime values are intentionally coarse so repeated manifestations can
    # aggregate; source-declared expectations are not runtime noise.
    assert registry._core._semantic_value(
        10, assertion_kind="field_compare"
    ) == registry._core._semantic_value(
        100, assertion_kind="field_compare"
    )
    assert expected_10 != expected_100

    defect_10 = registry.build_canonical_defect_identity(
        target_id="target",
        evidence=_evidence(expected_10),
    )
    defect_100 = registry.build_canonical_defect_identity(
        target_id="target",
        evidence=_evidence(expected_100),
    )

    assert defect_10["canonical_defect_id"] != defect_100["canonical_defect_id"]


def test_source_string_numbers_are_not_normalized_as_runtime_ids() -> None:
    # Source rules can encode thresholds in text. Runtime identity normalization
    # deliberately collapses long numeric instance IDs; source expectations must
    # retain the contract difference without exposing raw text.
    expected_1000 = registry._source_expected_semantic_value(
        "quantity must be <= 1000",
        assertion_kind="field_compare",
    )
    expected_2000 = registry._source_expected_semantic_value(
        "quantity must be <= 2000",
        assertion_kind="field_compare",
    )
    assert expected_1000 != expected_2000
    assert "1000" not in str(expected_1000)
    assert "2000" not in str(expected_2000)


def test_sealed_requirement_refs_are_identity_defining_but_actor_breadth_is_not() -> None:
    expected = registry._source_expected_semantic_value(
        10,
        assertion_kind="field_compare",
    )
    base = _evidence(expected)
    base["property"]["assertion_requirement_ref"] = "rule:max-stock"
    base["observed_outcome"]["assertion_requirement_ref"] = "rule:max-stock"

    actor_variant = deepcopy(base)
    actor_variant["proof"]["evidence_actor_classes"] = ["buyer", "auditor"]

    different_rule = deepcopy(base)
    different_rule["property"]["assertion_requirement_ref"] = "rule:max-credit"
    different_rule["observed_outcome"]["assertion_requirement_ref"] = "rule:max-credit"

    base_id = registry.build_canonical_defect_identity(
        target_id="target",
        evidence=base,
    )["canonical_defect_id"]
    actor_variant_id = registry.build_canonical_defect_identity(
        target_id="target",
        evidence=actor_variant,
    )["canonical_defect_id"]
    different_rule_id = registry.build_canonical_defect_identity(
        target_id="target",
        evidence=different_rule,
    )["canonical_defect_id"]

    assert base_id == actor_variant_id
    assert base_id != different_rule_id
