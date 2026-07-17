"""Adaptive planner cold-start bias toward isolation and uncovered prefixes."""
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


def test_score_obligation_boosts_isolation_with_ownership_param() -> None:
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
    assert iso_score > auth_score


def test_plan_selects_isolation_and_uncovered_prefix_under_budget() -> None:
    obligations = []
    experiments = {}
    # Many authorization twins on /api/cart
    for index in range(8):
        oid = f"obl_auth_cart_{index}"
        obligations.append({
            "obligation_id": oid,
            "risk_family": "authorization",
            "compile_status": "COMPILED",
            "subject_refs": [f"op-cart-{index}", "actor"],
            "confidence": 0.9,
            "property": {
                "operation_ref": f"op-cart-{index}",
                "operation_path_prefix": "/api/cart",
            },
        })
        experiments[oid] = _compiled(oid)
    # Isolation on a still-uncovered prefix
    oid_iso = "obl_iso_orders"
    obligations.append({
        "obligation_id": oid_iso,
        "risk_family": "isolation",
        "compile_status": "COMPILED",
        "subject_refs": ["op-orders", "owner", "viewer"],
        "confidence": 0.7,
        "property": {
            "operation_ref": "op-orders",
            "operation_path_prefix": "/api/orders",
            "ownership_param": "userId",
        },
    })
    experiments[oid_iso] = _compiled(oid_iso)
    # Isolation on cart (same prefix as auth flood)
    oid_cart_iso = "obl_iso_cart"
    obligations.append({
        "obligation_id": oid_cart_iso,
        "risk_family": "isolation",
        "compile_status": "COMPILED",
        "subject_refs": ["op-cart-get", "owner", "viewer"],
        "confidence": 0.7,
        "property": {
            "operation_ref": "op-cart-get",
            "operation_path_prefix": "/api/cart",
            "ownership_param": "userId",
        },
    })
    experiments[oid_cart_iso] = _compiled(oid_cart_iso)

    plan = plan_obligation_round(
        obligations,
        experiments_by_obligation=experiments,
        budget=4,
    )
    selected_ids = {row["obligation_id"] for row in plan["selected"]}
    selected_families = {row["risk_family"] for row in plan["selected"]}
    selected_prefixes = {row["path_prefix"] for row in plan["selected"] if row.get("path_prefix")}

    assert "isolation" in selected_families
    assert oid_iso in selected_ids or "/api/orders" in selected_prefixes
    assert oid_cart_iso in selected_ids or oid_iso in selected_ids
    assert len(plan["selected"]) == 4
