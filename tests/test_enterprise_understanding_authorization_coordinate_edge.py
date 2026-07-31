"""Authorization contracts require complete source-backed coordinates."""
from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)


def _asset(permission: dict) -> dict:
    return {
        "asset_id": "asset:authorization-coordinate-edge",
        "roles": [
            {
                "role_id": "role:operator",
                "source_id": "src_roles",
                "source_locator": "line:role",
                "statement": "operator role",
                "role": "operator",
            }
        ],
        "permission_matrix": [
            {
                "permission_id": "permission:edge",
                "source_id": "src_roles",
                "source_locator": "line:permission",
                "statement": "source-backed authorization declaration",
                "role": "operator",
                "scope": "tenant",
                **permission,
            }
        ],
        "business_fact_ledger": {"items": []},
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
    }


def _actor(model: dict) -> dict:
    return next(row for row in model["actors"] if row["name"] == "operator")


def test_explicit_restriction_is_deduplicated() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            {
                "resource": "orders",
                "decision": "deny",
                "denied_actions": ["remove"],
            }
        )
    )
    actor = _actor(model)

    assert len(actor["restrictions"]) == 1
    assert len(actor["authorization_contracts"]) == 1
    assert actor["restrictions"][0]["actions"] == ["remove"]


def test_missing_resource_downgrades_declared_decision_to_unknown() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            {
                "resource": "",
                "decision": "deny",
                "denied_actions": ["remove"],
            }
        )
    )
    actor = _actor(model)

    assert actor["restrictions"] == []
    assert len(actor["permission_unknowns"]) == 1
    contract = actor["permission_unknowns"][0]
    assert contract["decision"] == "UNKNOWN"
    assert contract["declared_decision"] == "DENY"
    assert contract["coordinate_complete"] is False
    assert contract["resolution_reason"] == "ACTOR_AUTHORIZATION_COORDINATE_INCOMPLETE"
    assert model["authorization_unknowns"]
