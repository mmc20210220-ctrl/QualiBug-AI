from ai_test_asset_center.actor_exploration_execution import (
    exploration_execution_policy,
)
from ai_test_asset_center.actor_exploration_runtime import can_explore_actor


def _proof() -> dict:
    return {
        "cleanup_plan": [{"action": "restore_before_snapshot"}],
        "write_reversibility_proof": {
            "proof_status": "PROVEN",
            "proof_kind": "field_snapshot_restore",
            "cleanup_authority": {"kind": "field_snapshot_restore"},
        },
    }


def test_bank_does_not_match_ban_at_compile_time():
    decision = can_explore_actor(
        {
            "id": "op-bank-account-update",
            "method": "POST",
            "path": "/api/bank/accounts/reconcile",
            "read_write": "write",
        },
        {},
    )

    assert decision.allowed is True
    assert decision.reason == "general_write"


def test_relationship_does_not_match_ship_at_runtime():
    assert exploration_execution_policy(
        operation={
            "id": "op-relationship-sync",
            "method": "POST",
            "path": "/api/relationships/sync",
            "read_write": "write",
        },
        experiment=_proof(),
        requested_max_attempts=2,
    ) == (True, 2, "compensated_write")


def test_disclose_does_not_match_close_at_runtime():
    assert exploration_execution_policy(
        operation={
            "id": "op-disclose-record",
            "method": "POST",
            "path": "/api/disclosures/record",
            "read_write": "write",
        },
        experiment=_proof(),
        requested_max_attempts=2,
    ) == (True, 2, "compensated_write")


def test_actual_ban_marker_remains_destructive_at_compile_time():
    decision = can_explore_actor(
        {
            "id": "op-ban-user",
            "method": "POST",
            "path": "/api/users/{id}/ban",
            "read_write": "write",
        },
        {},
    )

    assert decision.allowed is False
    assert decision.reason == "destructive_operation"


def test_actual_refund_marker_remains_risky_at_runtime():
    assert exploration_execution_policy(
        operation={
            "id": "op-refund",
            "method": "POST",
            "path": "/api/refunds",
            "read_write": "write",
        },
        experiment=_proof(),
        requested_max_attempts=2,
    ) == (True, 2, "compensated_destructive_write")


def test_actual_ship_marker_still_requires_owner():
    assert exploration_execution_policy(
        operation={
            "id": "op-ship",
            "method": "POST",
            "path": "/api/orders/{id}/ship",
            "read_write": "write",
        },
        experiment=_proof(),
        requested_max_attempts=2,
    ) == (False, 0, "state_transition_owner_unproven")
