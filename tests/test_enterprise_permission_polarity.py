from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _permission_entries


def test_structured_permission_denial_preserves_decision() -> None:
    rows = _permission_entries(
        "",
        {
            "permissions": [{
                "role": "reader",
                "resource": "/records/{id}",
                "actions": ["write"],
                "decision": "deny",
            }],
        },
        "permission-source",
    )

    assert rows[0]["decision"] == "deny"
    assert rows[0]["actions"] == ["write"]


def test_structured_denied_actions_are_not_rewritten_as_allowed_actions() -> None:
    rows = _permission_entries(
        "",
        {
            "permissions": [{
                "role": "reader",
                "resource": "/records/{id}",
                "actions": ["read"],
                "denied_actions": ["write", "delete"],
            }],
        },
        "permission-source",
    )

    assert rows[0]["actions"] == ["read"]
    assert rows[0]["denied_actions"] == ["write", "delete"]


def test_permission_without_explicit_polarity_does_not_invent_deny() -> None:
    rows = _permission_entries(
        "",
        {
            "permissions": [{
                "role": "reader",
                "resource": "/records/{id}",
                "actions": ["read"],
            }],
        },
        "permission-source",
    )

    assert "decision" not in rows[0]
    assert "denied_actions" not in rows[0]

