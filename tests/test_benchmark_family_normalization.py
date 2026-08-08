"""Product family vocabulary must normalize onto evaluator match ontology keys.

Two taxonomies describe the same defect classes: the product registry
(ai_test_asset_center.test_obligation.CANONICAL_RISK_FAMILIES short ids and
bug_ontology_registry family ids surfaced by classify_risk_family) and the
evaluator match ontology (_benchmark_match_ontology.json).  Product labels that
are NOT literal aliases of their evaluator counterpart (``conservation`` vs
``money_quantity_conservation``, ``idempotency`` vs
``idempotency_duplicate_submit``) used to resolve to a raw non-ontology key and
be rejected as a family mismatch (-0.20).  These tests pin the normalization
(_PRODUCT_FAMILY_TO_EVALUATOR via _evaluator_family) and assert that unmapped
labels keep today's pass-through behavior.
"""

from __future__ import annotations

from ai_test_asset_center.test_obligation import CANONICAL_RISK_FAMILIES
from benchmark_evaluator.benchmark_compute import (
    _PRODUCT_FAMILY_TO_EVALUATOR,
    _benchmark_match_ontology,
    _canonical_match_family,
    _evaluator_family,
    _match_finding_to_gt,
)


def test_every_product_canonical_family_normalizes_to_ontology_key() -> None:
    """All 10 product short ids resolve onto the evaluator match ontology."""
    ontology = _benchmark_match_ontology()
    for family in CANONICAL_RISK_FAMILIES:
        resolved = _canonical_match_family({"risk_family": family})
        assert resolved in ontology, (
            f"product family {family!r} resolved to {resolved!r}, not an ontology key"
        )


def test_all_mapping_targets_are_ontology_keys() -> None:
    """Every declared mapping target must exist in the match ontology."""
    ontology = _benchmark_match_ontology()
    for source, target in _PRODUCT_FAMILY_TO_EVALUATOR.items():
        assert target in ontology, f"mapping {source!r} -> {target!r} targets unknown key"


def test_short_id_mappings_resolve_exactly() -> None:
    """Short ids that are not literal aliases must land on the semantic key."""
    expected = {
        "authorization": "authorization_access_control",
        "isolation": "tenant_isolation",
        "state": "state_machine",
        "conservation": "money_quantity_conservation",
        "idempotency": "idempotency_duplicate_submit",
        "concurrency": "concurrency_race_condition",
        "validation": "input_validation_boundary",
        "visibility": "visibility_disclosure",
        "temporal": "async_eventual_consistency",
        "privacy": "visibility_disclosure",
    }
    for source, target in expected.items():
        assert _canonical_match_family({"risk_family": source}) == target, source


def test_registry_id_mappings_resolve_exactly() -> None:
    """bug_ontology_registry ids surfaced by classify_risk_family normalize too."""
    expected = {
        "input_boundary": "input_validation_boundary",
        "data_integrity": "data_consistency",
        "lifecycle": "state_machine",
        "eventual_consistency": "async_eventual_consistency",
        "audit_trail": "audit_traceability",
        "workflow": "workflow_approval",
        "audit": "audit_traceability",
    }
    for source, target in expected.items():
        assert _canonical_match_family({"risk_family": source}) == target, source


def test_gt_classified_as_product_idempotency_normalizes() -> None:
    """GT rows the product classifier labels 'idempotency' must not stay raw."""
    gt = {
        "bug_id": "DB-001",
        "type": "数据库约束/幂等",
        "title": "payments.idempotency_key 未设置唯一约束",
    }
    assert _canonical_match_family(gt) == "idempotency_duplicate_submit"


def test_unmapped_family_keeps_pass_through_behavior() -> None:
    """Labels outside both taxonomies pass through unchanged (incl. unclassified)."""
    assert _evaluator_family("bogus_family") == "bogus_family"
    assert _evaluator_family("unclassified") == "unclassified"
    assert _evaluator_family("") == ""


def test_conservation_finding_matches_money_gt() -> None:
    """A conservation deliverable now family-matches a money GT (was -0.20)."""
    finding = {
        "risk_family": "conservation",
        "title": "refund amount exceeds paid balance",
        "description": "refund created without ledger credit",
        "path": "/api/orders/123/refund",
        "reproduction": {"method": "POST", "path": "/api/orders/123/refund"},
    }
    money_gt = {
        "bug_id": "PAY-X",
        "type": "资金",
        "title": "退款金额多退 余额不对",
        "trigger": "POST /api/orders/123/refund",
        "match_keywords": ["refund", "amount", "余额"],
    }
    assert _canonical_match_family(finding) == "money_quantity_conservation"
    assert _canonical_match_family(money_gt) == "money_quantity_conservation"
    matched = _match_finding_to_gt(finding, [money_gt], set())
    assert matched is not None
    assert matched["bug_id"] == "PAY-X"
    assert float(matched["__match_score"]) >= 0.58
