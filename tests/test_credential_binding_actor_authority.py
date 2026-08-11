from __future__ import annotations


def _actor(ref: str, secret: str) -> dict:
    return {
        "id": ref,
        "role": "member",
        "credential_secret_ref": secret,
    }


def test_single_required_actor_can_bind_credential_placeholder() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _govern_credential_bindings,
    )

    rows = _govern_credential_bindings(
        [
            {
                "target": "password",
                "status": "runtime_resolvable",
                "source_priority": "actor_credential_secret",
                "actor_ref": "wrong-old-choice",
                "credential_secret_ref": "wrong-old-secret",
                "fixture_setup": {"kind": "actor_credential_field"},
            }
        ],
        obligation={},
        actors=[_actor("actor-a", "secret:test:a")],
    )

    row = rows[0]
    assert row["status"] == "runtime_resolvable"
    assert row["actor_ref"] == "actor-a"
    assert row["credential_secret_ref"] == "secret:test:a"
    assert row["credential_actor_authority"] == "unique_required_actor"


def test_two_arm_plan_cannot_choose_first_actors_secret() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _govern_credential_bindings,
    )

    rows = _govern_credential_bindings(
        [
            {
                "target": "password",
                "status": "runtime_resolvable",
                "source_priority": "actor_credential_secret",
                "actor_ref": "actor-a",
                "credential_secret_ref": "secret:test:a",
                "fixture_setup": {
                    "kind": "actor_credential_field",
                    "actor_ref": "actor-a",
                    "credential_secret_ref": "secret:test:a",
                },
            }
        ],
        obligation={
            "property": {
                "control_actor_ref": "actor-a",
                "treatment_actor_ref": "actor-b",
            }
        },
        actors=[
            _actor("actor-a", "secret:test:a"),
            _actor("actor-b", "secret:test:b"),
        ],
    )

    row = rows[0]
    assert row["status"] == "blocked"
    assert row["blocked_reason"] == "CREDENTIAL_BINDING_ACTOR_AMBIGUOUS"
    assert row["actor_ref"] == ""
    assert row["credential_secret_ref"] == ""
    assert "actor_ref" not in row["fixture_setup"]
    assert "credential_secret_ref" not in row["fixture_setup"]


def test_explicit_single_actor_coordinate_can_disambiguate_candidate_pool() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _credential_actor_authority,
    )

    actor, authority = _credential_actor_authority(
        {"property": {"actor_ref": "actor-b"}},
        [
            _actor("actor-a", "secret:test:a"),
            _actor("actor-b", "secret:test:b"),
        ],
    )

    assert actor is not None
    assert actor["id"] == "actor-b"
    assert authority == "explicit_actor_consensus"
