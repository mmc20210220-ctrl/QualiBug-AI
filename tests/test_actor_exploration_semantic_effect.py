from ai_test_asset_center.actor_exploration_execution import (
    exploration_execution_policy,
    should_continue_actor_exploration,
)


def _reversible_experiment() -> dict:
    return {
        "cleanup_plan": [{"action": "restore_before_snapshot"}],
        "write_reversibility_proof": {
            "proof_status": "PROVEN",
            "proof_kind": "field_snapshot_restore",
            "cleanup_authority": {"kind": "field_snapshot_restore"},
        },
    }


def test_source_declared_read_post_is_admitted_without_cleanup_proof():
    operation = {
        "id": "op-preview",
        "method": "POST",
        "path": "/api/contracts/evaluate",
        "read_write": "read",
    }

    assert exploration_execution_policy(
        operation=operation,
        experiment={},
        requested_max_attempts=3,
    ) == (True, 3, "safe_read")


def test_read_post_reuses_safe_read_retry_policy():
    operation = {
        "id": "op-preview",
        "method": "POST",
        "path": "/api/contracts/evaluate",
        "side_effect_class": "no_side_effect",
    }
    exploration_execution_policy(
        operation=operation,
        experiment={},
        requested_max_attempts=3,
    )

    assert should_continue_actor_exploration(
        method="POST",
        outcome="infrastructure_failed",
        status_code=503,
    ) == (True, "safe_read_retryable")


def test_explicit_write_declaration_overrides_query_looking_path():
    operation = {
        "id": "op-search-index-refresh",
        "method": "POST",
        "path": "/api/search/rebuild",
        "read_write": "write",
    }

    assert exploration_execution_policy(
        operation=operation,
        experiment={},
        requested_max_attempts=3,
    ) == (False, 0, "write_without_cleanup_proof")


def test_write_effect_replaces_prior_read_effect_in_same_execution_context():
    exploration_execution_policy(
        operation={
            "method": "POST",
            "path": "/api/contracts/evaluate",
            "read_write": "read",
        },
        experiment={},
        requested_max_attempts=3,
    )
    exploration_execution_policy(
        operation={
            "method": "POST",
            "path": "/api/search/rebuild",
            "read_write": "write",
        },
        experiment=_reversible_experiment(),
        requested_max_attempts=2,
    )

    assert should_continue_actor_exploration(
        method="POST",
        outcome="infrastructure_failed",
        status_code=503,
    ) == (False, "write_side_effect_unknown")


def test_direct_retry_call_can_use_operation_semantics_without_policy_state():
    assert should_continue_actor_exploration(
        method="POST",
        operation={
            "method": "POST",
            "path": "/api/contracts/evaluate",
            "read_write": "read",
        },
        outcome="permission_denied",
        status_code=403,
    ) == (True, "safe_read_retryable")
