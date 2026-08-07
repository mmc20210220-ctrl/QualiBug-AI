"""Adaptive planner selection must be family-neutral.

Regression (2026-08-07, E2E run 3): the planner carried two hardcoded
``isolation`` preferences — a cold-start score boost (+25%/+40%) and an
uncovered-prefix tie-break — added when authorization twins outnumbered
isolation obligations. After source-declared ownership relations made
isolation obligations abundant, the same bias let isolation flood the
execution budget and starved authorization obligations, so previously found
authorization defects (product status/delete, user status, coupon
create/status, order address) stopped reappearing. Selection must rank on
score + diversity soft-caps only; no family name may change the outcome.
"""
from __future__ import annotations

from ai_test_asset_center.adaptive_discovery_planner import (
    plan_obligation_round,
    score_obligation,
)


def _compiled(oid: str) -> dict:
    return {
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "COMPILED"},
    }


def test_score_obligation_has_no_family_name_boost() -> None:
    auth = {
        "obligation_id": "obl_auth",
        "risk_family": "authorization",
        "subject_refs": ["op-a", "actor-1"],
        "confidence": 0.8,
        "property": {"operation_ref": "op-a"},
    }
    isolation = {
        "obligation_id": "obl_iso",
        "risk_family": "isolation",
        "subject_refs": ["op-b", "owner", "viewer"],
        "confidence": 0.8,
        "property": {
            "operation_ref": "op-b",
            "ownership_param": "userId",
            "ownership_param_location": "query",
        },
    }
    auth_score = score_obligation(auth, covered_keys=set())
    iso_score = score_obligation(isolation, covered_keys=set())
    # Identical confidence/subject structure -> identical score. A family name
    # must never change ranking.
    assert iso_score == auth_score


def test_uncovered_prefix_seed_is_score_ordered_not_family_ordered() -> None:
    obligations = []
    experiments = {}
    # One high-confidence authorization obligation on an uncovered prefix…
    oid_auth = "obl_auth_orders"
    obligations.append({
        "obligation_id": oid_auth,
        "risk_family": "authorization",
        "compile_status": "COMPILED",
        "subject_refs": ["op-orders", "actor"],
        "confidence": 0.9,
        "property": {
            "operation_ref": "op-orders",
            "operation_path_prefix": "/api/orders",
        },
    })
    experiments[oid_auth] = _compiled(oid_auth)
    # …and a lower-confidence isolation obligation on the same prefix.
    oid_iso = "obl_iso_orders"
    obligations.append({
        "obligation_id": oid_iso,
        "risk_family": "isolation",
        "compile_status": "COMPILED",
        "subject_refs": ["op-orders-iso", "owner", "viewer"],
        "confidence": 0.7,
        "property": {
            "operation_ref": "op-orders-iso",
            "operation_path_prefix": "/api/orders",
            "ownership_param": "userId",
        },
    })
    experiments[oid_iso] = _compiled(oid_iso)

    plan = plan_obligation_round(
        obligations,
        experiments_by_obligation=experiments,
        budget=2,
    )
    selected_ids = {row["obligation_id"] for row in plan["selected"]}
    # The uncovered-prefix seed goes to the higher-scoring obligation,
    # regardless of family name.
    assert oid_auth in selected_ids


def test_budget_distributes_families_by_soft_caps_not_names() -> None:
    obligations = []
    experiments = {}
    # 6 authorization + 6 isolation obligations on the same prefix, equal
    # confidence: soft caps (budget // family count) must let both families in.
    for index in range(6):
        for family in ("authorization", "isolation"):
            oid = f"obl_{family}_{index}"
            obligations.append({
                "obligation_id": oid,
                "risk_family": family,
                "compile_status": "COMPILED",
                "subject_refs": [f"op-{index}", "actor"],
                "confidence": 0.8,
                "property": {
                    "operation_ref": f"op-{index}",
                    "operation_path_prefix": "/api/things",
                },
            })
            experiments[oid] = _compiled(oid)

    plan = plan_obligation_round(
        obligations,
        experiments_by_obligation=experiments,
        budget=4,
    )
    selected_families = {row["risk_family"] for row in plan["selected"]}
    assert "authorization" in selected_families
    assert "isolation" in selected_families
    assert len(plan["selected"]) == 4
