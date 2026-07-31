"""Actor responsibility and authorization are separate source-backed contracts."""
from __future__ import annotations

import hashlib

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)


def _evidence(statement: str, *, source_id: str = "src_roles", locator: str = "line:1") -> dict:
    return {
        "source_id": source_id,
        "locator": locator,
        "quote": statement,
        "quote_hash": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
    }


def _permission(
    *,
    permission_id: str,
    decision: str = "",
    actions: list[str] | None = None,
    denied_actions: list[str] | None = None,
    resource: str = "orders",
) -> dict:
    statement = f"operator {decision or 'unknown'} {resource} {actions or []} {denied_actions or []}"
    return {
        "permission_id": permission_id,
        "source_id": "src_roles",
        "source_locator": f"line:{permission_id}",
        "statement": statement,
        "role": "operator",
        "resource": resource,
        "actions": list(actions or []),
        "denied_actions": list(denied_actions or []),
        **({"decision": decision} if decision else {}),
        "scope": "tenant",
    }


def _asset(*, permissions: list[dict] | None = None, facts: list[dict] | None = None) -> dict:
    return {
        "asset_id": "asset:actor-permission-contract",
        "roles": [
            {
                "role_id": "role:operator",
                "source_id": "src_roles",
                "source_locator": "line:role",
                "statement": "operator role",
                "role": "operator",
            }
        ],
        "permission_matrix": list(permissions or []),
        "business_fact_ledger": {"items": list(facts or [])},
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
    }


def _actor(model: dict) -> dict:
    return next(row for row in model["actors"] if row["name"] == "operator")


def _fact(*, modality: str, action: str = "approve", object_ref: str = "orders") -> dict:
    statement = f"operator {modality} {action} {object_ref}"
    return {
        "fact_id": f"fact:{modality.lower()}:{action}",
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "modality": modality,
        "polarity": "negative" if modality == "MUST_NOT" else "positive",
        "subject": {"actor_refs": ["operator"], "entity_refs": [object_ref]},
        "object": {"entity_refs": []},
        "action": {"canonical": action, "raw": action},
        "source_spans": [_evidence(statement)],
    }


def test_unknown_permission_never_becomes_allow_or_deny() -> None:
    model = build_enterprise_understanding_model(
        _asset(permissions=[_permission(permission_id="p1", actions=["read"])])
    )
    actor = _actor(model)

    assert actor["permissions"] == []
    assert actor["restrictions"] == []
    assert [row["decision"] for row in actor["permission_unknowns"]] == ["UNKNOWN"]
    assert actor["authorization_status"] == "UNRESOLVED"
    assert model["gate"]["authorization_gate"]["entry_allowed"] is False
    assert model["gate"]["authorization_gate"]["unknown_never_authorizes"] is True


def test_mixed_allow_and_denied_actions_are_split_not_collapsed() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            permissions=[
                _permission(
                    permission_id="p2",
                    decision="allow",
                    actions=["read", "list"],
                    denied_actions=["delete"],
                )
            ]
        )
    )
    actor = _actor(model)

    assert {action for row in actor["permissions"] for action in row["actions"]} == {
        "list",
        "read",
    }
    assert {action for row in actor["restrictions"] for action in row["actions"]} == {
        "delete"
    }
    assert actor["permission_unknowns"] == []
    assert actor["authorization_status"] == "RESOLVED"


def test_required_business_participant_is_responsible_not_automatically_authorized() -> None:
    model = build_enterprise_understanding_model(_asset(facts=[_fact(modality="MUST")]))
    actor = _actor(model)

    assert actor["responsibility_operation_refs"]
    assert actor["permissions"] == []
    assert actor["restrictions"] == []
    assert actor["authorization_status"] == "NOT_DECLARED"


def test_may_and_must_not_facts_project_explicit_authorization_only() -> None:
    model = build_enterprise_understanding_model(
        _asset(
            facts=[
                _fact(modality="MAY", action="read"),
                _fact(modality="MUST_NOT", action="delete"),
            ]
        )
    )
    actor = _actor(model)

    assert {action for row in actor["permissions"] for action in row["actions"]} == {
        "read"
    }
    assert {action for row in actor["restrictions"] for action in row["actions"]} == {
        "delete"
    }


def test_authorization_change_changes_model_identity() -> None:
    allow_model = build_enterprise_understanding_model(
        _asset(
            permissions=[
                _permission(permission_id="p3", decision="allow", actions=["read"])
            ]
        )
    )
    deny_model = build_enterprise_understanding_model(
        _asset(
            permissions=[
                _permission(permission_id="p3", decision="deny", actions=["read"])
            ]
        )
    )

    assert allow_model["model_id"] != deny_model["model_id"]


def test_authorization_unknowns_are_local_not_global_business_unknowns() -> None:
    model = build_enterprise_understanding_model(
        _asset(permissions=[_permission(permission_id="p4", actions=["read"])])
    )

    assert model["authorization_unknowns"]
    assert not any(
        row.get("kind") == "ACTOR_AUTHORIZATION_UNRESOLVED"
        for row in model["unknowns"]
    )
    assert model["gate"]["authorization_gate"]["status"] == (
        "PARTIAL_AUTHORIZATION_UNRESOLVED"
    )
