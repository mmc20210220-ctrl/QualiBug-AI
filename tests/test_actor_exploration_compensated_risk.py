from ai_test_asset_center.actor_exploration_execution import (
    exploration_execution_policy,
)


def _proof(kind="explicit_compensator") -> dict:
    return {
        "cleanup_plan": [
            {
                "action": "source_declared_compensation",
                "mode": "compensating_transition",
            }
        ],
        "write_reversibility_proof": {
            "proof_status": "PROVEN",
            "proof_kind": kind,
            "cleanup_authority": {"kind": kind},
        },
    }


def test_proven_refund_write_is_not_rejected_by_path_word_alone():
    assert exploration_execution_policy(
        operation={
            "id": "op-refund-request",
            "method": "POST",
            "path": "/api/refunds",
            "read_write": "write",
        },
        experiment=_proof(),
        requested_max_attempts=2,
    ) == (True, 2, "compensated_destructive_write")


def test_proven_payment_write_is_not_rejected_by_path_word_alone():
    assert exploration_execution_policy(
        operation={
            "id": "op-payment",
            "method": "POST",
            "path": "/api/payments/pay",
            "read_write": "write",
        },
        experiment=_proof(),
        requested_max_attempts=2,
    ) == (True, 2, "compensated_destructive_write")


def test_risky_write_without_real_proof_stays_blocked():
    assert exploration_execution_policy(
        operation={
            "id": "op-refund-request",
            "method": "POST",
            "path": "/api/refunds",
            "read_write": "write",
        },
        experiment={},
        requested_max_attempts=2,
    ) == (False, 0, "write_without_cleanup_proof")


def test_accepted_residue_never_unlocks_risky_write():
    assert exploration_execution_policy(
        operation={
            "id": "op-payment",
            "method": "POST",
            "path": "/api/payments/pay",
            "read_write": "write",
        },
        experiment={
            "cleanup_plan": [
                {
                    "action": "accepted_residue",
                    "mode": "accepted_residue_no_cleanup",
                    "residue": True,
                }
            ],
            "write_reversibility_proof": {
                "proof_status": "PROVEN",
                "proof_kind": "accepted_residue",
                "reversibility": "none",
                "cleanup_authority": {"kind": "accepted_residue"},
            },
        },
        requested_max_attempts=2,
    ) == (False, 0, "accepted_residue_is_not_reversible")


def test_delete_remains_blocked_even_with_exact_recreate_proof():
    assert exploration_execution_policy(
        operation={
            "id": "op-delete",
            "method": "DELETE",
            "path": "/api/resources/{id}",
            "read_write": "write",
        },
        experiment=_proof("exact_recreate"),
        requested_max_attempts=2,
    ) == (False, 0, "destructive_operation")


def test_state_transition_still_requires_owner_after_proof():
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
